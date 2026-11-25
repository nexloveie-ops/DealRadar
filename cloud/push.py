"""Push notifications to the iOS app, sent through Firebase Cloud Messaging after each collection.

Who gets what (one message per phone per collection, in this order):
  1. A watched product meets the member's condition (every collection): sent the first time it meets it, and again
     only if the price drops further.
  2. A member's search words (evening collection, once a day): new deals matching the words since the last evening.
  3. Daily summary (evening collection, once a day): new deals since the last evening. For phones that aren't signed
     in, and for members who follow no words and no products.
"New" means not among the deals of the previous evening's collection. Nothing is sent between 22:00 and 07:00 Irish
time, and nothing is sent when there is nothing new.

Firestore:
  push_devices/{sha256(token)[:40]}  token, uid (None when signed out), prefs {watch, words, daily}, platform, dates
  members/{uid}.push_watch_sent      {watch id: price in cent last sent}
  meta/push_baseline                 ids of the deals at the last evening send, and its Irish date
  settings/push                      mode "test" (only test_uids get pushes) or "all"; test_uids
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request

import members

bp = Blueprint("push", __name__)
DUBLIN = ZoneInfo("Europe/Dublin")
DEVICES = "push_devices"
BASELINE_DOC = ("meta", "push_baseline")
SETTINGS_DOC = ("settings", "push")
QUIET_FROM, QUIET_UNTIL = 22, 7     # Irish time: nothing sent from 22:00 until 07:00
EVENING_FROM = 17                   # the first collection from 17:00 sends the daily words and summary pushes
DEFAULT_PREFS = {"watch": True, "words": True, "daily": True}
PROJECT = "dealradar-515523"


def device_id(token):
    return hashlib.sha256(token.encode()).hexdigest()[:40]


def eur(minor):
    """4999 -> "€49.99", 5900 -> "€59", 129900 -> "€1,299"."""
    if minor is None:
        return ""
    s = f"{minor / 100:,.2f}"
    return "€" + (s[:-3] if s.endswith(".00") else s)


def pct(d):
    return f"{round(d['discount_percent'])}% off"


# ---------------------------------------------------------------- registration API (called by the app)
@bp.post("/api/push/device")
def register_device():
    """Save this phone's push token and choices. Signed in (Bearer token): linked to the member; otherwise anonymous."""
    body = request.get_json(silent=True) or {}
    token = str(body.get("token", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9:_\-]{20,4096}", token):
        return jsonify({"error": "Missing or invalid push token."}), 400
    auth = request.headers.get("Authorization", "")
    claims = members.claims_from(auth[7:]) if auth.startswith("Bearer ") else None
    if auth.startswith("Bearer ") and not claims:
        return jsonify({"error": "Your sign-in has expired. Sign in again."}), 401
    prefs = dict(DEFAULT_PREFS)
    prefs.update({k: bool(v) for k, v in (body.get("prefs") or {}).items() if k in DEFAULT_PREFS})
    ref = members._ctx["db"].collection(DEVICES).document(device_id(token))
    now = members.now_iso()
    snap = ref.get()
    doc = {"token": token, "uid": claims["user_id"] if claims else None, "platform": str(body.get("platform", "ios"))[:10],
           "prefs": prefs, "updated_at": now}
    if not snap.exists:
        doc["created_at"] = now
    ref.set(doc, merge=True)
    return jsonify({"prefs": prefs})


# ---------------------------------------------------------------- what to send (pure: no database, easy to test)
def visible_deals(rows):
    """Every deal a member may see (public plus members-only), and the public ones alone."""
    seen, public = [], []
    for r in rows:
        v = r.get("public_view") if r.get("publish") and r.get("public_view") else r.get("member_view")
        if v and v.get("discount_percent") is not None:
            seen.append(v)
            if r.get("publish") and r.get("public_view"):
                public.append(v)
    return seen, public


def summary_line(deals):
    """'up to 62% off · from €9.99': the biggest discount, and that product's price."""
    top = max(deals, key=lambda d: d.get("discount_percent") or 0)
    return f"up to {pct(top)} · from {eur(top.get('price_minor'))}", top


def watch_message(hits):
    """hits: [(watch, status)] -> (title, body, data)."""
    def line(w, st):
        name = st.get("title") or w.get("label") or w["key"]
        cond = w.get("condition") or {"type": "any"}
        price = eur(st.get("price_minor"))
        if cond.get("type") == "price":
            return f"{name} is now {price}, below your {eur(int(round(float(cond['value']) * 100)))} target"
        off = f", {round(st['discount_percent'])}% off" if st.get("discount_percent") else ""
        return f"{name} is now {price}{off}"
    if len(hits) == 1:
        w, st = hits[0]
        return "Price alert", line(w, st), {"type": "watch", "watch_id": w["id"], "url": st.get("buy_url") or w.get("url", "")}
    names = "; ".join(line(w, st) for w, st in hits[:3])
    return f"{len(hits)} watched products hit your target", names, {"type": "watch"}


def words_message(found):
    """found: {query: [new deals]} -> (title, body, data)."""
    deals = {d["id"]: d for ds in found.values() for d in ds}
    n, words = len(deals), list(found)
    line, top = summary_line(list(deals.values()))
    if len(words) == 1:
        title = f"{n} new deal{'s' if n != 1 else ''} for “{words[0]}”"
    else:
        title = f"{n} new deals for your words"
        line = ", ".join(f"“{w}”" for w in words[:3]) + " · " + line
    return title, line, {"type": "words"}


def daily_message(new_public):
    n = len(new_public)
    line, top = summary_line(new_public)
    return f"{n} new deal{'s' if n != 1 else ''} today", line, {"type": "daily"}


def plan(now, devices, member_docs, tiers, watch_status, rows, baseline, settings):
    """Decide the messages for this collection.

    now: aware datetime; devices: [{id, token, uid, prefs}]; member_docs: {uid: member data};
    watch_status: {watch doc id: last check}; baseline: {"ids": [...], "day": "YYYY-MM-DD"} or None;
    settings: {"mode": "test"|"all", "test_uids": [...]}.
    Returns (messages [{device, token, title, body, data}], member updates {uid: push_watch_sent}, new baseline or None).
    """
    local = now.astimezone(DUBLIN)
    quiet = local.hour >= QUIET_FROM or local.hour < QUIET_UNTIL
    today = local.date().isoformat()
    evening = local.hour >= EVENING_FROM and not quiet and (baseline or {}).get("day") != today
    seen, public = visible_deals(rows)
    new_baseline = {"ids": sorted(d["id"] for d in seen), "day": today} if evening or baseline is None else None
    old = set((baseline or {}).get("ids") or [])
    have_baseline = baseline is not None
    new_seen = [d for d in seen if d["id"] not in old] if have_baseline else []
    new_public = [d for d in public if d["id"] not in old] if have_baseline else []

    test_only = (settings or {}).get("mode", "test") != "all"
    allowed = set((settings or {}).get("test_uids") or [])

    # Watched products meeting their condition, per member (also updates what was last sent, even in quiet hours,
    # so a hit that ends is forgotten and can alert again later).
    watch_hits, updates = {}, {}
    for uid, data in member_docs.items():
        tier = members.member_tier(data, tiers)
        sent = dict(data.get("push_watch_sent") or {})
        new_sent, hits = {}, []
        for w in members.watch_states(data.get("watch", []), tier, now):
            if w["status"] == "expired":
                continue
            st = watch_status.get(members.watch_doc_id(w["site"], w["key"]))
            if not members.condition_met(w.get("condition"), st):
                continue
            price = st["price_minor"]
            last = sent.get(w["id"])
            new_sent[w["id"]] = min(price, last) if last is not None else price
            if last is None or price < last:
                hits.append((w, st))
        if new_sent != sent:
            updates[uid] = new_sent
        if hits:
            watch_hits[uid] = hits

    messages = []
    for dev in devices:
        uid, prefs = dev.get("uid"), {**DEFAULT_PREFS, **(dev.get("prefs") or {})}
        if test_only and uid not in allowed:
            continue
        if quiet:
            continue
        data = member_docs.get(uid) if uid else None
        msg = None
        if data is not None and prefs["watch"] and watch_hits.get(uid):
            msg = watch_message(watch_hits[uid])
        elif evening and data is not None and active_words(data, tiers, now):
            if prefs["words"]:
                found = {}
                for k in active_words(data, tiers, now):
                    mind = members.kw_min(k)
                    ds = [d for d in new_seen if (d.get("discount_percent") or 0) >= mind and members.keyword_matches(k["query"], d.get("title"))]
                    if ds:
                        found[k["query"]] = ds
                if found:
                    msg = words_message(found)
        elif evening and prefs["daily"] and new_public and (data is None or not has_watch(data, tiers, now)):
            msg = daily_message(new_public)
        if msg:
            title, body, extra = msg
            messages.append({"device": dev["id"], "token": dev["token"], "title": title, "body": body,
                             "data": {k: str(v) for k, v in extra.items()}})
    return messages, updates, new_baseline


def active_words(data, tiers, now):
    tier = members.member_tier(data, tiers)
    return [k for k in members.keyword_states(data.get("keywords", []), tier, now) if k["status"] != "expired"]


def has_watch(data, tiers, now):
    tier = members.member_tier(data, tiers)
    return any(w["status"] != "expired" for w in members.watch_states(data.get("watch", []), tier, now))


# ---------------------------------------------------------------- sending (called by the collection job)
def fcm_session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/firebase.messaging"])
    return AuthorizedSession(creds)


def send(session, token, title, body, data):
    """Returns "ok", "gone" (the app was removed or the token replaced) or an error text."""
    msg = {"message": {"token": token, "notification": {"title": title, "body": body}, "data": data,
                       "apns": {"payload": {"aps": {"sound": "default"}}}}}
    r = session.post(f"https://fcm.googleapis.com/v1/projects/{PROJECT}/messages:send", json=msg, timeout=20)
    if r.status_code == 200:
        return "ok"
    text = r.text[:300]
    if r.status_code == 404 or "UNREGISTERED" in text:
        return "gone"
    return f"HTTP {r.status_code}: {text}"


def after_collection(db, rows, now=None, session=None):
    """Work out and send this collection's pushes. Never raises: a push problem must not fail the collection."""
    try:
        now = now or datetime.now(timezone.utc)
        settings_snap = db.collection(SETTINGS_DOC[0]).document(SETTINGS_DOC[1]).get()
        settings = settings_snap.to_dict() if settings_snap.exists else {"mode": "test", "test_uids": []}
        devices = [dict(s.to_dict(), id=s.id) for s in db.collection(DEVICES).stream()]
        uids = {d.get("uid") for d in devices if d.get("uid")}
        member_docs = {}
        for s in db.collection("members").stream():
            d = s.to_dict()
            if s.id in uids or d.get("push_watch_sent") or d.get("watch"):
                member_docs[s.id] = d
        tiers = members.load_tiers(db, fresh=True)
        status = {}
        for d in member_docs.values():
            for w in d.get("watch", []):
                doc = members.watch_doc_id(w["site"], w["key"])
                if doc not in status:
                    snap = db.collection(members.WATCH_DOC).document(doc).get()
                    if snap.exists:
                        status[doc] = snap.to_dict()
        bsnap = db.collection(BASELINE_DOC[0]).document(BASELINE_DOC[1]).get()
        baseline = bsnap.to_dict() if bsnap.exists else None

        messages, updates, new_baseline = plan(now, devices, member_docs, tiers, status, rows, baseline, settings)
        sent, failed, removed = 0, [], 0
        if messages:
            session = session or fcm_session()
            for m in messages:
                result = send(session, m["token"], m["title"], m["body"], m["data"])
                if result == "ok":
                    sent += 1
                elif result == "gone":
                    db.collection(DEVICES).document(m["device"]).delete()
                    removed += 1
                else:
                    failed.append(result)
        for uid, sent_map in updates.items():
            db.collection("members").document(uid).update({"push_watch_sent": sent_map})
        if new_baseline:
            db.collection(BASELINE_DOC[0]).document(BASELINE_DOC[1]).set({**new_baseline, "at": now.isoformat(timespec="seconds")})
        report = {"devices": len(devices), "planned": len(messages), "sent": sent, "removed_tokens": removed,
                  "errors": failed[:5], "mode": settings.get("mode", "test"), "baseline_updated": bool(new_baseline)}
        print(json.dumps({"push": report}), flush=True)
        return report
    except Exception as exc:  # pragma: no cover - logged, never fatal
        print(json.dumps({"push": {"error": str(exc)[:300]}}), flush=True)
        return {"error": str(exc)[:300]}
