"""Amazon search queue: one Amazon search every N minutes, through a list of search words, around the clock.

Amazon refuses most searches from our servers, apparently at random. So instead of a few searches per collection,
the words wait in a queue and are searched one at a time:
  - every N minutes (settings/amazon_queue.interval_minutes) the word at the head of the queue is searched;
  - a search that works is saved (amazon_search_pool/{word id}) and the queue moves to the next word;
  - a search Amazon refuses is tried again next time (the queue does not move);
  - when every word has been searched, the list is built again and a new round starts.
Every attempt is logged (amazon_queue_log) so different values of N can be compared.
The 07:00 and 19:00 collections use the saved results instead of searching themselves.

Cloud Scheduler calls POST /internal/amazon-search-tick every minute; the tick does nothing until N minutes have
passed since the last attempt, so N can be changed in Firestore without touching the schedule.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

from flask import Blueprint, jsonify, request

ROOT = Path(__file__).resolve().parent.parent
AMAZON_DIR = ROOT / "outputs" / "amazon-offers-service"
SETTINGS_DOC = ("settings", "amazon_queue")   # enabled, interval_minutes
STATE_DOC = ("meta", "amazon_queue")          # items, index, attempts, last_attempt_at, round, round_started_at
POOL = "amazon_search_pool"                   # {word id}: query, searched_at, rows
LOG = "amazon_queue_log"
DEFAULT_INTERVAL = 2
LOG_DAYS = 7
SERVICE_ACCOUNT_DOMAIN = "@dealradar-515523.iam.gserviceaccount.com"

bp = Blueprint("amazon_queue", __name__)


def amazon_direct():
    """The Amazon collector's own search parser and fetcher, so both read Amazon the same way."""
    if str(AMAZON_DIR) not in sys.path:
        sys.path.insert(0, str(AMAZON_DIR))
    import amazon_direct as ad  # noqa: WPS433
    return ad


def search_url(query, discount_filter_percent):
    f = f"&rh=p_8%3A{discount_filter_percent}-" if discount_filter_percent > 0 else ""
    # Electronics department only, as in the collector: a bare "jbl" otherwise returns books and pet food.
    return f"https://www.amazon.ie/s?k={quote_plus(query)}&i=electronics{f}"


def build_list(settings, member_words):
    """The admin's Amazon keywords (in the admin's order), then members' searching words that aren't already on it."""
    items = [{"id": k["id"], "query": k["query"]} for k in settings.get("amazon_keywords", []) if k.get("enabled", True)]
    items += [{"id": "m:" + w["id"], "query": w["query"]} for w in member_words if not w.get("on_whitelist")]
    return items


def parse_time(s):
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def tick(db, now, fetch, load_settings, member_words_fn):
    """One step of the queue. fetch(url) returns the page HTML or raises Blocked/RuntimeError (amazon_direct's)."""
    ad = amazon_direct()
    cfg_snap = db.collection(SETTINGS_DOC[0]).document(SETTINGS_DOC[1]).get()
    cfg = cfg_snap.to_dict() if cfg_snap.exists else {}
    if not cfg.get("enabled"):
        return {"skipped": "disabled"}
    interval = max(1, int(cfg.get("interval_minutes", DEFAULT_INTERVAL)))
    ref = db.collection(STATE_DOC[0]).document(STATE_DOC[1])
    snap = ref.get()
    state = snap.to_dict() if snap.exists else {}
    last = parse_time(state.get("last_attempt_at"))
    # A few seconds' slack: the scheduler fires every minute, not on the exact second.
    if last and now - last < timedelta(minutes=interval) - timedelta(seconds=20):
        return {"skipped": "not_due", "next_in_seconds": int((last + timedelta(minutes=interval) - now).total_seconds())}

    items, index = state.get("items") or [], int(state.get("index", 0))
    if index >= len(items):
        items = build_list(load_settings(), member_words_fn())
        index = 0
        state.update(round=int(state.get("round", 0)) + 1, round_started_at=now.isoformat(timespec="seconds"), attempts=0)
        if not items:
            ref.set({**state, "items": [], "index": 0, "last_attempt_at": now.isoformat(timespec="seconds")})
            return {"skipped": "no_words"}
    word = items[index]
    # Claim this slot first, so an overlapping call can't search at the same time.
    state.update(items=items, index=index, last_attempt_at=now.isoformat(timespec="seconds"))
    ref.set(state)

    c = json.loads((AMAZON_DIR / "config.json").read_text())
    pct_filter = int(c.get("direct", {}).get("search_discount_filter_percent", 20))
    threshold = float(c["minimum_discount_percent"])
    entry = {"at": now.isoformat(timespec="seconds"), "word_id": word["id"], "query": word["query"],
             "interval_minutes": interval, "attempt": int(state.get("attempts", 0)) + 1, "round": state.get("round", 1)}
    try:
        raw = fetch(search_url(word["query"], pct_filter))
        rows, _ = ad.parse_search(raw)
        qualifying = sum(1 for r in rows if (ad.exact_discount(r["price_minor"], r["strike_minor"])[1] or 0) > threshold)
        db.collection(POOL).document(word["id"].replace("/", "_")).set(
            {"word_id": word["id"], "query": word["query"], "searched_at": now.isoformat(timespec="seconds"), "rows": rows})
        entry.update(ok=True, results=len(rows), qualifying=qualifying)
        state.update(index=index + 1, attempts=0)
    except ad.Blocked as exc:
        entry.update(ok=False, error="blocked", detail=str(exc)[:120])
        state["attempts"] = entry["attempt"]
    except RuntimeError as exc:
        entry.update(ok=False, error="http_error", detail=str(exc)[:120])
        state["attempts"] = entry["attempt"]
    state["last_result"] = entry
    ref.set(state)
    db.collection(LOG).add(entry)
    return entry


def pool_for_collector(db, now, valid_hours):
    """Saved search results still fresh enough to list, for the 07:00 and 19:00 collections."""
    out = []
    for s in db.collection(POOL).stream():
        d = s.to_dict()
        at = parse_time(d.get("searched_at"))
        if at and now - at <= timedelta(hours=valid_hours):
            out.append(d)
    return out


def prune_log(db, now):
    cutoff = (now - timedelta(days=LOG_DAYS)).isoformat(timespec="seconds")
    from google.cloud.firestore_v1.base_query import FieldFilter
    for s in db.collection(LOG).where(filter=FieldFilter("at", "<", cutoff)).limit(200).stream():
        s.reference.delete()


# ---------------------------------------------------------------- endpoint for Cloud Scheduler
_ctx = {}


def init(db, load_settings, member_words_fn):
    _ctx.update(db=db, load_settings=load_settings, member_words=member_words_fn)


def caller_allowed():
    """Only Cloud Scheduler, calling with a Google-signed token for one of this project's service accounts."""
    from google.auth.transport import requests as greq
    from google.oauth2 import id_token
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    try:
        claims = id_token.verify_oauth2_token(auth[7:], greq.Request())
    except Exception:
        return False
    return bool(claims.get("email_verified")) and str(claims.get("email", "")).endswith(SERVICE_ACCOUNT_DOMAIN)


@bp.post("/internal/amazon-search-tick")
def search_tick():
    if not caller_allowed():
        return jsonify({"error": "forbidden"}), 403
    ad = amazon_direct()
    fetcher = ad.Fetcher(os.environ.get("AMAZON_QUEUE_CACHE", "/tmp/amazon-queue"), 0)
    now = datetime.now(timezone.utc)
    result = tick(_ctx["db"], now, lambda url: fetcher.get(url, "queue-search")[0], _ctx["load_settings"], _ctx["member_words"])
    if result.get("ok") is not None and now.minute < 5:
        prune_log(_ctx["db"], now)
    print(json.dumps({"amazon_queue": result}), flush=True)
    return jsonify(result)
