"""Members (phase 2): sign-in with Firebase (email link or Google), what each member follows, and their own clicks.

Firestore
  members/{uid}                  email, created_at, last_seen_at, tier, channels[], keywords[], watch[], clicks_total
                                 keywords[] in the member's order: id, query, min_discount, added_at, expires_at
  settings/member_tiers          tiers[]: id, name, search_words (N), word_days (admin setting; "free" to start)
  members/{uid}/clicks/{deal}    deal_id, title, store, total, last_click_at
  member_finds/{keyword_id}      Amazon results for a member keyword that is not on the admin whitelist.
                                 Shown only to members who added that keyword, never on the public page.

Search words: any number, each valid for the tier's word_days. Of the words that have not expired, the first N
(the tier's search_words) are searched for the member; the rest only filter deals that were collected anyway.
Expired words stay in the list, greyed out, until the member renews them.
"""
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import Blueprint, abort, jsonify, request
from google.cloud import firestore

bp = Blueprint("members", __name__)
MAX_KEYWORDS, MAX_WATCH = 100, 50  # a safety cap; how many words are searched depends on the member's tier
DEFAULT_TIERS = [{"id": "free", "name": "Free", "search_words": 3, "word_days": 3, "watch_items": 3, "watch_days": 3,
                  "instant_checks": 10}]
WATCH_DOC = "watch_status"  # watch_status/{site}:{key}: the last check of a watched product, shared by everyone watching it
TIERS_DOC = ("settings", "member_tiers")
# Each search word has the member's own minimum discount. The collector keeps every discounted Amazon result for
# private words; the member's choice decides what is shown.
MIN_DISCOUNTS, DEFAULT_MIN = (0, 10, 20, 30, 40, 50, 60, 70), 20


def kw_min(k):
    v = k.get("min_discount", DEFAULT_MIN)
    return v if v in MIN_DISCOUNTS else DEFAULT_MIN


def parse_min(value):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v if v in MIN_DISCOUNTS else None

# Channels a member can follow. `stores` are the names deals carry in their "store" field.
CHANNELS = [
    {"id": "vodafone", "name": "Vodafone", "group": "Operators", "stores": ["Vodafone"], "setting": ("operators", "vodafone")},
    {"id": "three", "name": "Three", "group": "Operators", "stores": ["Three"], "setting": ("operators", "three")},
    {"id": "eir", "name": "eir", "group": "Operators", "stores": ["eir"], "setting": ("operators", "eir")},
    {"id": "tesco_mobile", "name": "Tesco Mobile", "group": "Operators", "stores": ["Tesco Mobile"], "setting": ("operators", "tesco_mobile")},
    {"id": "harvey_norman", "name": "Harvey Norman", "group": "Shops", "stores": ["Harvey Norman"], "setting": ("shops", "harvey_norman")},
    {"id": "expert", "name": "Expert", "group": "Shops", "stores": ["Expert Ireland", "Expert"], "setting": ("shops", "expert")},
    {"id": "powercity", "name": "Powercity", "group": "Shops", "stores": ["Powercity"], "setting": ("shops", "powercity")},
    {"id": "very", "name": "Very.ie", "group": "Shops", "stores": ["Very.ie"], "setting": ("shops", "very")},
    {"id": "amazon", "name": "Amazon.ie", "group": "Online", "stores": ["Amazon.ie"], "setting": None},
    {"id": "mobile123", "name": "Mobile123", "group": "Online", "stores": ["Mobile123"], "setting": None},
]
WATCH_SITES = {
    "amazon.ie": "Amazon.ie", "harveynorman.ie": "Harvey Norman", "expert.ie": "Expert", "powercity.ie": "Powercity",
    "mobile123.ie": "Mobile123",
}
_ctx = {}  # set by init(): db, verify (token -> claims), snapshot, settings loader


def init(db, verify, snapshot, load_settings):
    _ctx.update(db=db, verify=verify, snapshot=snapshot, load_settings=load_settings)


# ---------------------------------------------------------------- tiers and search-word states
def validate_tiers(tiers):
    out, seen = [], set()
    for t in tiers or []:
        tid = str(t.get("id") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9_-]{1,30}", tid) or tid in seen:
            raise ValueError("Each level needs a unique short id (letters, numbers, - or _)")
        seen.add(tid)
        try:
            n, days = int(t.get("search_words")), int(t.get("word_days"))
            wn, wdays = int(t.get("watch_items", 3)), int(t.get("watch_days", 3))
            instant = int(t.get("instant_checks", 10))
        except (TypeError, ValueError):
            raise ValueError("Numbers of words, products and days must be whole numbers")
        if not 0 <= n <= 50 or not 0 <= wn <= 100:
            raise ValueError("Words searched must be 0 to 50 and products checked 0 to 100")
        if not 1 <= days <= 365 or not 1 <= wdays <= 365:
            raise ValueError("Days must be between 1 and 365")
        if not 0 <= instant <= 100:
            raise ValueError("Instant checks per hour must be between 0 and 100")
        out.append({"id": tid, "name": str(t.get("name") or tid).strip()[:40], "search_words": n, "word_days": days,
                    "watch_items": wn, "watch_days": wdays, "instant_checks": instant})
    if not any(t["id"] == "free" for t in out):
        raise ValueError("Keep the Free level: new members start on it")
    return out


_tiers_cache = {"at": 0.0, "value": None}


def load_tiers(db=None, fresh=False):
    if fresh or _tiers_cache["value"] is None or time.monotonic() - _tiers_cache["at"] > 60:
        snap = (db or _ctx["db"]).collection(TIERS_DOC[0]).document(TIERS_DOC[1]).get()
        tiers = DEFAULT_TIERS
        if snap.exists:
            try:
                tiers = validate_tiers((snap.to_dict() or {}).get("tiers"))
            except ValueError:
                pass
        _tiers_cache.update(at=time.monotonic(), value=tiers)
    return _tiers_cache["value"]


def member_tier(data, tiers=None):
    tiers = tiers or load_tiers()
    return next((t for t in tiers if t["id"] == data.get("tier")), None) or next(t for t in tiers if t["id"] == "free")


def parse_time(iso):
    try:
        return datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def keyword_states(kws, tier, now=None):
    """Each word with its expiry and status, in the member's order:
    "searching" (one of the first N words that have not expired), "matching" (not expired, beyond N) or "expired"."""
    now = now or datetime.now(timezone.utc)
    out, searching = [], 0
    for k in kws:
        exp = parse_time(k.get("expires_at"))
        if exp is None:  # words added before expiry existed: counted from when they were added
            exp = (parse_time(k.get("added_at")) or now) + timedelta(days=tier["word_days"])
        if exp <= now:
            status = "expired"
        elif searching < tier["search_words"]:
            status, searching = "searching", searching + 1
        else:
            status = "matching"
        out.append(dict(k, expires_at=exp.isoformat(timespec="seconds"), status=status, min_discount=kw_min(k)))
    return out


def collection():
    try:
        return _ctx["collection_info"]() if "collection_info" in _ctx else None
    except Exception:
        return None


def watch_states(watch, tier, now=None):
    """Each watched product with its expiry and status, in the member's order: "checking" (one of the first N
    products that have not expired), "not_checked" (beyond N) or "expired"."""
    now = now or datetime.now(timezone.utc)
    out, checking = [], 0
    for w in watch:
        exp = parse_time(w.get("expires_at")) or (parse_time(w.get("added_at")) or now) + timedelta(days=tier["watch_days"])
        if exp <= now:
            status = "expired"
        elif checking < tier["watch_items"]:
            status, checking = "checking", checking + 1
        else:
            status = "not_checked"
        out.append(dict(w, expires_at=exp.isoformat(timespec="seconds"), status=status, condition=w.get("condition") or {"type": "any"}))
    return out


def watch_doc_id(site, key):
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{site}:{key}")[:300]


def condition_met(cond, st):
    """Whether a checked product meets the member's condition: any discount, X% or more off, or a price below EUR X."""
    if not st or st.get("status") != "checked" or st.get("price_minor") is None:
        return False
    t, v = (cond or {}).get("type", "any"), (cond or {}).get("value")
    if t == "price":
        return v is not None and st["price_minor"] < int(round(float(v) * 100))
    d = st.get("discount_percent")
    if d is None:
        return False
    return d >= float(v) if t == "pct" and v is not None else d > 0


def parse_condition(body):
    t = body.get("type", "any")
    if t == "any":
        return {"type": "any"}
    try:
        v = float(body.get("value"))
    except (TypeError, ValueError):
        return None
    if t == "pct" and 1 <= v <= 95:
        return {"type": "pct", "value": int(v)}
    if t == "price" and 0 < v < 100000:
        return {"type": "price", "value": round(v, 2)}
    return None


def watch_statuses(watch):
    out = {}
    for w in watch:
        snap = _ctx["db"].collection(WATCH_DOC).document(watch_doc_id(w["site"], w["key"])).get()
        if snap.exists:
            out[w["id"]] = snap.to_dict()
    return out


def active_keywords(data):
    return [k for k in keyword_states(data.get("keywords", []), member_tier(data)) if k["status"] != "expired"]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "item"


def norm_words(text):
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def claims_from(token):
    try:
        claims = _ctx["verify"](token)
    except Exception:
        return None
    return claims if claims.get("email_verified") and claims.get("email") else None


def current_member():
    auth = request.headers.get("Authorization", "")
    claims = claims_from(auth[7:]) if auth.startswith("Bearer ") else None
    if not claims:
        abort(401)
    ref = _ctx["db"].collection("members").document(claims["user_id"])
    snap = ref.get()
    if snap.exists:
        data = snap.to_dict()
        if data.get("last_seen_at", "")[:13] != now_iso()[:13]:
            ref.update({"last_seen_at": now_iso()})
    else:
        provider = (claims.get("firebase") or {}).get("sign_in_provider", "")
        # New members start with every shop and network ticked; they can untick what they don't want.
        data = {"email": claims["email"].lower(), "created_at": now_iso(), "last_seen_at": now_iso(), "sign_in": provider,
                "channels": [], "channels_saved": False, "keywords": [], "watch": [], "clicks_total": 0}
        ref.set(data)
    return ref, data


def available_channels():
    settings = _ctx["load_settings"]()
    out = []
    for c in CHANNELS:
        if c["setting"]:
            group, cid = c["setting"]
            if not any(x["id"] == cid and x.get("enabled", True) for x in settings.get(group, [])):
                continue
        out.append({k: c[k] for k in ("id", "name", "group")})
    return out


def whitelist():
    """Admin Amazon keywords and their other words, lower-case."""
    words = set()
    for k in _ctx["load_settings"]().get("amazon_keywords", []):
        if k.get("enabled", True):
            words.add(" ".join(norm_words(k["query"])))
            words.update(" ".join(norm_words(a)) for a in k.get("aliases", []))
    return words


def watch_key(url):
    """(site name, product key) for a product link on a supported shop, else (None, None)."""
    try:
        u = urlparse((url or "").strip())
    except ValueError:
        return None, None
    host = (u.hostname or "").lower().removeprefix("www.")
    if u.scheme not in ("http", "https") or host not in WATCH_SITES:
        return None, None
    path = u.path.rstrip("/")
    if host == "amazon.ie":
        m = re.search(r"/(?:dp|gp/product|gp/aw/d)/([A-Z0-9]{10})", path)
        return (WATCH_SITES[host], m[1]) if m else (None, None)
    if host in ("harveynorman.ie", "mobile123.ie"):
        m = re.search(r"/products/([^/?#]+)", path)
        return (WATCH_SITES[host], m[1].lower()) if m else (None, None)
    last = path.rsplit("/", 1)[-1].lower()
    return (WATCH_SITES[host], last) if last and len(path) > 1 else (None, None)


def deal_watch_key(d):
    if d.get("asin"):
        return "Amazon.ie", d["asin"]
    site, key = watch_key(d.get("buy_url"))
    return site, key


def keyword_matches(query, title):
    words = [w for w in norm_words(query) if len(w) > 1]
    t = " " + " ".join(norm_words(title)) + " "
    return bool(words) and all(f" {w} " in t or (len(w) > 3 and w in t) for w in words)


# ---------------------------------------------------------------- member API
@bp.get("/api/me")
def me():
    _, data = current_member()
    return jsonify({"member": public_member(data), "channels": available_channels(), "collection": collection(),
                    "limits": {"keywords": MAX_KEYWORDS, "watch": MAX_WATCH}})


def member_channels(d):
    """Until a member saves their own choice, they follow every available shop and network."""
    avail = [c["id"] for c in available_channels()]
    return [c for c in d.get("channels", []) if c in avail] if d.get("channels_saved") else avail


def public_member(d):
    wl = whitelist()
    tier = member_tier(d)
    kws = [dict(k, private=" ".join(norm_words(k["query"])) not in wl) for k in keyword_states(d.get("keywords", []), tier)]
    return {"email": d.get("email"), "created_at": d.get("created_at"), "channels": member_channels(d),
            "keywords": kws, "watch": watch_states(d.get("watch", []), tier), "tier": tier}


@bp.put("/api/me/channels")
def put_channels():
    ref, _ = current_member()
    ids = {c["id"] for c in available_channels()}
    chosen = [c for c in (request.get_json(silent=True) or {}).get("channels", []) if c in ids]
    ref.update({"channels": chosen, "channels_saved": True})
    return jsonify({"channels": chosen})


@bp.post("/api/me/keywords")
def add_keyword():
    ref, data = current_member()
    body = request.get_json(silent=True) or {}
    q = re.sub(r"\s+", " ", str(body.get("query", ""))).strip()
    mind = parse_min(body.get("min_discount", DEFAULT_MIN))
    if mind is None:
        return jsonify({"error": "Choose a minimum discount from the list."}), 400
    if not 2 <= len(q) <= 60 or not norm_words(q):
        return jsonify({"error": "Type a word or short phrase (2 to 60 characters)."}), 400
    kws = data.get("keywords", [])
    if any(" ".join(norm_words(k["query"])) == " ".join(norm_words(q)) for k in kws):
        return jsonify({"error": f"You already follow “{q}”."}), 400
    if len(kws) >= MAX_KEYWORDS:
        return jsonify({"error": f"You can keep up to {MAX_KEYWORDS} search words. Remove one first."}), 400
    days = member_tier(data)["word_days"]
    kws.append({"id": slug(q), "query": q, "min_discount": mind, "added_at": now_iso(),
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")})
    ref.update({"keywords": kws})
    return jsonify({"member": public_member(dict(data, keywords=kws))})


@bp.put("/api/me/keywords/<kid>")
def update_keyword(kid):
    ref, data = current_member()
    mind = parse_min((request.get_json(silent=True) or {}).get("min_discount"))
    if mind is None:
        return jsonify({"error": "Choose a minimum discount from the list."}), 400
    kws = [dict(k, min_discount=mind) if k["id"] == kid else k for k in data.get("keywords", [])]
    ref.update({"keywords": kws})
    return jsonify({"member": public_member(dict(data, keywords=kws))})


@bp.post("/api/me/keywords/<kid>/renew")
def renew_keyword(kid):
    ref, data = current_member()
    days = member_tier(data)["word_days"]
    until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")
    kws = [dict(k, expires_at=until) if k["id"] == kid else k for k in data.get("keywords", [])]
    ref.update({"keywords": kws})
    return jsonify({"member": public_member(dict(data, keywords=kws))})


@bp.put("/api/me/keywords/order")
def order_keywords():
    """The member's order decides which words are searched (the first N that have not expired)."""
    ref, data = current_member()
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    kws = {k["id"]: k for k in data.get("keywords", [])}
    if sorted(ids) != sorted(kws):
        return jsonify({"error": "The list changed. Reload the page and try again."}), 400
    ordered = [kws[i] for i in ids]
    ref.update({"keywords": ordered})
    return jsonify({"member": public_member(dict(data, keywords=ordered))})


@bp.delete("/api/me/keywords/<kid>")
def delete_keyword(kid):
    ref, data = current_member()
    kws = [k for k in data.get("keywords", []) if k["id"] != kid]
    ref.update({"keywords": kws})
    return jsonify({"member": public_member(dict(data, keywords=kws))})


@bp.post("/api/me/watch")
def add_watch():
    ref, data = current_member()
    body = request.get_json(silent=True) or {}
    url = str(body.get("url", "")).strip()[:500]
    site, key = watch_key(url)
    if not site:
        return jsonify({"error": "Paste a product link from Amazon.ie, Harvey Norman, Expert, Powercity or Mobile123."}), 400
    watch = data.get("watch", [])
    if any(w["site"] == site and w["key"] == key for w in watch):
        return jsonify({"error": "You already watch this product."}), 400
    if len(watch) >= MAX_WATCH:
        return jsonify({"error": f"You can keep up to {MAX_WATCH} products. Remove one first."}), 400
    label = str(body.get("label", "")).strip()[:80] or None
    cond = parse_condition(body.get("condition") or {"type": "any"})
    if cond is None:
        return jsonify({"error": "Set a discount from 1 to 95%, or a price in euro."}), 400
    days = member_tier(data)["watch_days"]
    watch.append({"id": uuid.uuid4().hex[:10], "site": site, "key": key, "url": url, "label": label, "added_at": now_iso(),
                  "expires_at": (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds"), "condition": cond})
    ref.update({"watch": watch})
    data = dict(data, watch=watch)
    check = instant_check(ref, data, watch[-1])
    return jsonify({"member": public_member(data), "check": check})


_checker = {}


def checker():
    """The watch-service checker (outputs/watch-service/check.py), loaded once."""
    if "mod" not in _checker:
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "outputs/watch-service/check.py"
        spec = importlib.util.spec_from_file_location("watch_check", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _checker["mod"] = mod
    return _checker["mod"]


def take_amazon_search(now):
    """Amazon searches by instant checks are capped for the whole site (settings/member_tiers amazon_instant_searches,
    per hour), so members adding products can't wear out Amazon's tolerance of our servers."""
    cap = load_tiers_doc().get("amazon_instant_searches", 20)
    ref = _ctx["db"].collection("meta").document("amazon_instant_searches")
    snap = ref.get()
    recent = [t for t in ((snap.to_dict() or {}).get("times", []) if snap.exists else []) if (parse_time(t) or now) > now - timedelta(hours=1)]
    if len(recent) >= cap:
        return False
    ref.set({"times": recent + [now.isoformat(timespec="seconds")]})
    return True


def load_tiers_doc():
    snap = _ctx["db"].collection(TIERS_DOC[0]).document(TIERS_DOC[1]).get()
    return (snap.to_dict() or {}) if snap.exists else {}


def instant_check(ref, data, w):
    """Check a product the member just added, within the member's hourly allowance (tier "instant_checks").
    The result is stored like a collection check, so everyone watching that product sees it."""
    tier = member_tier(data)
    now = datetime.now(timezone.utc)
    recent = [t for t in data.get("instant_checks", []) if (parse_time(t) or now) > now - timedelta(hours=1)]
    if len(recent) >= tier["instant_checks"]:
        return {"done": False, "reason": f"You've used your {tier['instant_checks']} instant checks this hour; it's checked at the next collection."}
    ref.update({"instant_checks": recent + [now.isoformat(timespec="seconds")]})
    amazon_search = w["site"] == "Amazon.ie" and take_amazon_search(now)
    try:
        r = checker().check_one({"site": w["site"], "key": w["key"], "url": w["url"]}, amazon_search=amazon_search)
    except Exception as exc:
        return {"done": False, "reason": "Couldn't check it now; it's checked at the next collection.", "error": str(exc)[:150]}
    coll = _ctx["db"].collection(WATCH_DOC)
    doc = watch_doc_id(w["site"], w["key"])
    snap = coll.document(doc).get()
    prev = snap.to_dict() if snap.exists else {}
    rec = merge_watch_result(prev, r, now)
    if rec is not None and r.get("status") == "not_checked":
        # No price yet (Amazon hid it): keep the name and picture so the member sees what they added.
        rec = {k: r.get(k) for k in ("site", "key", "buy_url", "title", "image_url", "checked_at")} | {"status": "pending", "note": r.get("error")}
    if rec is not None:
        coll.document(doc).set(rec)
    shown = rec or prev
    return {"done": r.get("status") in ("checked", "unavailable", "not_found"), "status": r.get("status"), "reason": r.get("error"),
            "met": condition_met(w.get("condition"), shown)}


@bp.put("/api/me/watch/<wid>")
def update_watch(wid):
    ref, data = current_member()
    cond = parse_condition((request.get_json(silent=True) or {}).get("condition") or {})
    if cond is None:
        return jsonify({"error": "Set a discount from 1 to 95%, or a price in euro."}), 400
    watch = [dict(w, condition=cond) if w["id"] == wid else w for w in data.get("watch", [])]
    ref.update({"watch": watch})
    return jsonify({"member": public_member(dict(data, watch=watch))})


@bp.post("/api/me/watch/<wid>/renew")
def renew_watch(wid):
    ref, data = current_member()
    until = (datetime.now(timezone.utc) + timedelta(days=member_tier(data)["watch_days"])).isoformat(timespec="seconds")
    watch = [dict(w, expires_at=until) if w["id"] == wid else w for w in data.get("watch", [])]
    ref.update({"watch": watch})
    return jsonify({"member": public_member(dict(data, watch=watch))})


@bp.put("/api/me/watch/order")
def order_watch():
    ref, data = current_member()
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    items = {w["id"]: w for w in data.get("watch", [])}
    if sorted(ids) != sorted(items):
        return jsonify({"error": "The list changed. Reload the page and try again."}), 400
    ordered = [items[i] for i in ids]
    ref.update({"watch": ordered})
    return jsonify({"member": public_member(dict(data, watch=ordered))})


@bp.get("/api/me/watch/results")
def my_watch_results():
    """Last check of each watched product, and whether it meets the member's condition."""
    _, data = current_member()
    states = watch_states(data.get("watch", []), member_tier(data))
    st = watch_statuses(states)
    return jsonify({"watch": [dict(w, check=st.get(w["id"]), met=w["status"] != "expired" and condition_met(w["condition"], st.get(w["id"])))
                              for w in states], "collection": collection()})


@bp.delete("/api/me/watch/<wid>")
def delete_watch(wid):
    ref, data = current_member()
    watch = [w for w in data.get("watch", []) if w["id"] != wid]
    ref.update({"watch": watch})
    return jsonify({"member": public_member(dict(data, watch=watch))})


def public_deals():
    """Every deal a member may see: the public ones plus members-only ones (from 20% up to the public minimum,
    or found through members' own search words)."""
    rows, _ = _ctx["snapshot"]()
    return [r.get("public_view") or r.get("member_view") for r in rows
            if (r.get("publish") and r.get("public_view")) or r.get("member_view")]


def keyword_results(kws, public):
    """For each keyword: discounted public deals whose name matches it, plus (for a word not on the admin list)
    the private Amazon results the collector found for it."""
    wl = whitelist()
    out = []
    for k in kws:
        private = " ".join(norm_words(k["query"])) not in wl
        if k.get("status") == "expired":
            out.append({"id": k["id"], "query": k["query"], "private": private, "status": "expired", "searched_at": None,
                        "min_discount": kw_min(k), "count": 0, "found": 0, "deals": []})
            continue
        found = {d["id"]: d for d in public if d.get("discount_percent") is not None and keyword_matches(k["query"], d.get("title"))}
        searched_at = None
        if private:
            snap = _ctx["db"].collection("member_finds").document(k["id"]).get()
            if snap.exists:
                f = snap.to_dict()
                searched_at = f.get("searched_at")
                for d in f.get("deals", []):
                    found.setdefault(d["id"], dict(d, private=True))
        mind = kw_min(k)
        deals = sorted((d for d in found.values() if (d.get("discount_percent") or 0) >= mind), key=lambda d: -(d.get("discount_percent") or 0))
        out.append({"id": k["id"], "query": k["query"], "private": private, "status": k.get("status"), "searched_at": searched_at, "min_discount": mind,
                    "count": len(deals), "found": len(found), "deals": deals})
    return out


@bp.get("/api/me/keywords/results")
def my_keyword_results():
    _, data = current_member()
    return jsonify({"keywords": keyword_results(keyword_states(data.get("keywords", []), member_tier(data)), public_deals()),
                    "collection": collection()})


@bp.get("/api/me/deals")
def my_deals():
    """Deals matching what the member follows. Each deal says why it is listed."""
    _, data = current_member()
    public = public_deals()
    channels = {c["id"]: c for c in CHANNELS}
    stores = {s: cid for cid in member_channels(data) if cid in channels for s in channels[cid]["stores"]}
    found, why = {}, {}

    def add(d, reason):
        found.setdefault(d["id"], d)
        why.setdefault(d["id"], [])
        if reason not in why[d["id"]]:
            why[d["id"]].append(reason)

    for d in public:
        if d.get("discount_percent") is None:
            continue
        if d.get("store") in stores:
            add(d, {"type": "channel", "label": d["store"]})
    # Watched products (not expired) whose last check meets the member's condition: listed first.
    states = [w for w in watch_states(data.get("watch", []), member_tier(data)) if w["status"] != "expired"]
    checks = watch_statuses(states)
    watch_hits = {}
    for w in states:
        st = checks.get(w["id"])
        if condition_met(w["condition"], st):
            view = {"id": "watch:" + watch_doc_id(w["site"], w["key"]), "module": "watch", "store": w["site"],
                    "title": st.get("title") or w.get("label") or w["key"], "image_url": st.get("image_url"),
                    "price_minor": st.get("price_minor"), "reference_minor": st.get("reference_minor"),
                    "discount_percent": st.get("discount_percent"), "buy_url": st.get("buy_url") or w["url"],
                    "affiliate": w["site"] == "Amazon.ie", "checked_at": st.get("checked_at"),
                    "price_source": "amazon_page" if w["site"] == "Amazon.ie" else "retailer",
                    "reference_kind": st.get("reference_kind") or "was_price", "reference_seen_on": st.get("baseline_on"),
                    "category": ""}
            # The same product may also be a collected deal: show it once.
            dup = next((d for d in public if deal_watch_key(d) == (w["site"], w["key"])), None)
            if dup:
                add(dup, {"type": "watch", "label": "Target reached"})
                watch_hits[w["id"]] = dup["id"]
                continue
            found[view["id"]] = view
            why[view["id"]] = [{"type": "watch", "label": "Target reached"}]
            watch_hits[w["id"]] = view["id"]
    for k in keyword_results(active_keywords(data), public):
        for d in k["deals"]:
            add(d, {"type": "keyword", "label": k["query"], "private": bool(d.get("private"))})
    out = sorted(found.values(), key=lambda d: (not any(r["type"] == "watch" for r in why[d["id"]]), -(d.get("discount_percent") or 0)))
    return jsonify({"deals": [dict(d, why=why[d["id"]]) for d in out],
                    "watch": [dict(w, deal_id=watch_hits.get(w["id"])) for w in data.get("watch", [])],
                    })


# ---------------------------------------------------------------- clicks by member
def record_member_click(token, deal_id, view):
    claims = claims_from(token) if token else None
    if not claims:
        return
    db = _ctx["db"]
    ref = db.collection("members").document(claims["user_id"])
    if not ref.get().exists:
        return
    inc = firestore.Increment(1)
    batch = db.batch()
    batch.update(ref, {"clicks_total": inc})
    batch.set(ref.collection("clicks").document(deal_id.replace("/", "_")[:400]),
              {"deal_id": deal_id, "title": view.get("title"), "store": view.get("store"), "buy_url": view.get("buy_url"),
               "total": inc, "last_click_at": now_iso()}, merge=True)
    batch.commit()


# ---------------------------------------------------------------- admin views
def admin_members(require_admin):
    @bp.get("/api/admin/members")
    def admin_list_members():
        require_admin()
        wl = whitelist()
        tiers = load_tiers(fresh=True)
        out = []
        for s in _ctx["db"].collection("members").stream():
            d = s.to_dict()
            tier = member_tier(d, tiers)
            out.append({"uid": s.id, "email": d.get("email"), "created_at": d.get("created_at"), "last_seen_at": d.get("last_seen_at"),
                        "sign_in": d.get("sign_in"), "clicks_total": d.get("clicks_total", 0), "channels": member_channels(d),
                        "tier": tier["id"], "tier_name": tier["name"],
                        "keywords": [dict(k, private=" ".join(norm_words(k["query"])) not in wl)
                                     for k in keyword_states(d.get("keywords", []), tier)],
                        "watch": watch_states(d.get("watch", []), tier)})
        out.sort(key=lambda m: m.get("last_seen_at") or "", reverse=True)
        counts = {}
        for m in out:
            for k in m["keywords"]:
                key = " ".join(norm_words(k["query"]))
                c = counts.setdefault(key, {"query": k["query"], "members": 0, "searching": 0, "private": k["private"]})
                c["members"] += 1
                c["searching"] += k["status"] == "searching"
        return jsonify({"members": out, "keywords": sorted(counts.values(), key=lambda c: (-c["searching"], -c["members"])),
                        "channels": {c["id"]: c["name"] for c in CHANNELS}, "tiers": tiers,
                        "amazon_instant_searches": load_tiers_doc().get("amazon_instant_searches", 20)})

    @bp.get("/api/admin/members/<uid>/clicks")
    def admin_member_clicks(uid):
        require_admin()
        rows = [s.to_dict() for s in _ctx["db"].collection("members").document(uid).collection("clicks").stream()]
        rows.sort(key=lambda r: -r.get("total", 0))
        return jsonify({"clicks": rows[:200]})

    @bp.get("/api/admin/member-tiers")
    def admin_get_tiers():
        require_admin()
        return jsonify({"tiers": load_tiers(fresh=True), "amazon_instant_searches": load_tiers_doc().get("amazon_instant_searches", 20)})

    @bp.put("/api/admin/member-tiers")
    def admin_put_tiers():
        who = require_admin()
        body = request.get_json(silent=True) or {}
        try:
            tiers = validate_tiers(body.get("tiers"))
            cap = int(body.get("amazon_instant_searches", load_tiers_doc().get("amazon_instant_searches", 20)))
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        if not 0 <= cap <= 200:
            return jsonify({"error": "Amazon instant searches must be between 0 and 200 an hour"}), 400
        _ctx["db"].collection(TIERS_DOC[0]).document(TIERS_DOC[1]).set({"tiers": tiers, "amazon_instant_searches": cap,
                                                                        "updated_at": now_iso(), "updated_by": who})
        _tiers_cache["value"] = None
        return jsonify({"tiers": tiers, "amazon_instant_searches": cap, "saved": True})

    @bp.put("/api/admin/members/<uid>/tier")
    def admin_set_member_tier(uid):
        require_admin()
        tid = (request.get_json(silent=True) or {}).get("tier")
        if tid not in {t["id"] for t in load_tiers(fresh=True)}:
            return jsonify({"error": "Unknown level"}), 400
        ref = _ctx["db"].collection("members").document(uid)
        if not ref.get().exists:
            return jsonify({"error": "Unknown member"}), 404
        ref.update({"tier": tid})
        return jsonify({"tier": tid})


def searching_words_for_collector(db, load_settings_fn):
    """Members' words being searched this run: for each member, the first N words that have not expired (N by tier).
    Each word: id, query, members (how many members search it), on_whitelist (already an admin Amazon keyword)."""
    _ctx.setdefault("load_settings", load_settings_fn)
    _ctx.setdefault("db", db)
    wl = set()
    for k in load_settings_fn().get("amazon_keywords", []):
        if k.get("enabled", True):
            wl.add(" ".join(norm_words(k["query"])))
            wl.update(" ".join(norm_words(a)) for a in k.get("aliases", []))
    tiers = load_tiers(db, fresh=True)
    out = {}
    for s in db.collection("members").stream():
        d = s.to_dict()
        for k in keyword_states(d.get("keywords", []), member_tier(d, tiers)):
            if k["status"] != "searching":
                continue
            norm = " ".join(norm_words(k["query"]))
            if norm:
                w = out.setdefault(k["id"], {"id": k["id"], "query": k["query"], "members": 0, "on_whitelist": norm in wl})
                w["members"] += 1
    return sorted(out.values(), key=lambda k: -k["members"])


def watched_items_for_collector(db):
    """Products to check this run: for each member, the first N watched products that have not expired (N by tier).
    One entry per product, least recently checked first. Also returns every product still watched (not expired),
    so old check results can be removed."""
    tiers = load_tiers(db, fresh=True)
    items, keep = {}, set()
    for s in db.collection("members").stream():
        d = s.to_dict()
        for w in watch_states(d.get("watch", []), member_tier(d, tiers)):
            if w["status"] == "expired":
                continue
            doc = watch_doc_id(w["site"], w["key"])
            keep.add(doc)
            if w["status"] == "checking":
                items.setdefault(doc, {"site": w["site"], "key": w["key"], "url": w["url"], "doc": doc})
    for doc, it in items.items():
        snap = db.collection(WATCH_DOC).document(doc).get()
        prev = snap.to_dict() if snap.exists else {}
        it["product_id"], it["checked_at"] = prev.get("product_id"), prev.get("checked_at") or ""
    return sorted(items.values(), key=lambda i: i["checked_at"]), keep


WATCH_BASELINE_DAYS = 30


def merge_watch_result(prev, r, now=None):
    """The record to store for one check of a watched product. A failed check keeps the last good one (returns None).
    For a shop without a was price (Powercity), the reference is the highest price seen in the last 30 days,
    kept as one number, not a history."""
    now = now or datetime.now(timezone.utc)
    prev = prev or {}
    if r.get("status") in ("not_checked", "error") and prev.get("status") == "checked":
        return None
    rec = dict(r, reference_kind="was_price" if r.get("reference_minor") else None)
    for k in ("title", "image_url"):  # keep what an earlier check found if this one lacks it
        if not rec.get(k) and prev.get(k):
            rec[k] = prev[k]
    base, base_on = prev.get("baseline_minor"), prev.get("baseline_on")
    if base_on and (now - datetime.fromisoformat(base_on)).days > WATCH_BASELINE_DAYS:
        base, base_on = None, None
    price = r.get("price_minor")
    if price is not None and r.get("status") == "checked":
        if base is None or price >= base:
            base, base_on = price, now.isoformat(timespec="seconds")
        if not r.get("reference_minor") and base and base > price:
            rec.update(reference_minor=base, reference_kind="price_drop", discount_percent=round((base - price) * 100 / base, 1))
    rec.update(baseline_minor=base, baseline_on=base_on, baseline_note="highest price seen in the last 30 days")
    return rec
