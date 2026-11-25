"""DealRadar web service (Cloud Run): UI page plus a read-only JSON API over Firestore.

  GET /                      public page: automatically published deals only
  GET /internal?key=...      internal view (needs INTERNAL_KEY on the service): every record and its publish decision
  GET /api/deals             published deals (public fields). ?module=operator|retailer|amazon ?discount=1
  GET /api/deals/<id>        one published deal
  GET /api/runs/latest       latest collection run summary
  POST /api/click            count a click on a deal's shop button (no IP or cookie stored; a signed-in member's
                             Firebase token, if sent, also counts it for that member)
  GET /my-deals              a signed-in member's deals, with the home page's filters
  GET /account               member page: sign in, follow channels, keywords and products, "My deals"
  /api/me/*                  member settings and matching deals (Firebase ID token required)
  GET /admin                 admin panel (Firebase sign-in; admins only)
  /api/admin/*               settings, clicks and admin list (Firebase ID token required)
  GET /health
"""
import hmac
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request
from google.cloud import firestore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "outputs"))
import combine_deals  # noqa: E402

app = Flask(__name__)
db = firestore.Client(database=os.environ.get("FIRESTORE_DATABASE", "(default)"))
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "300"))
_cache = {"at": None, "rows": [], "latest": None}
_lock = threading.Lock()


def snapshot():
    with _lock:
        if _cache["at"] is None or time.monotonic() - _cache["at"] > CACHE_SECONDS:
            rows = []
            for d in db.collection("deals").stream():
                r = d.to_dict()
                r.pop("source_record_json", None)
                rows.append(r)
            latest = db.collection("meta").document("latest").get()
            _cache.update(at=time.monotonic(), rows=rows, latest=latest.to_dict() if latest.exists else None)
        return _cache["rows"], _cache["latest"]


def generated(latest):
    return (latest or {}).get("finished_at", "")


@app.get("/health")
def health():
    return {"ok": True}


# UCP agent profile (https://ucp.dev). Shops that offer a UCP catalogue (e.g. Harvey Norman, on Shopify) fetch it before
# answering our read-only catalogue searches. We declare catalogue search and lookup only: no cart, checkout or orders.
UCP_PROFILE = {"ucp": {"version": "2026-08-25", "services": {}, "payment_handlers": {}, "capabilities": {
    "dev.ucp.shopping.catalog.search": [{"version": "2026-08-25", "spec": "https://ucp.dev/2026-08-25/specification/shopping/catalog/",
                                         "schema": "https://ucp.dev/2026-08-25/schemas/shopping/catalog_search.json"}],
    "dev.ucp.shopping.catalog.lookup": [{"version": "2026-08-25", "spec": "https://ucp.dev/2026-08-25/specification/shopping/catalog/",
                                         "schema": "https://ucp.dev/2026-08-25/schemas/shopping/catalog_lookup.json"}]}}}


@app.get("/ucp/profile.json")
def ucp_profile():
    r = Response(json.dumps(UCP_PROFILE), mimetype="application/json")
    r.headers["Cache-Control"] = "public, max-age=3600"
    r.headers["Last-Modified"] = "Mon, 23 Nov 2026 00:00:00 GMT"
    return r


@app.get("/")
def public_page():
    rows, latest = snapshot()
    words, times = public_schedule()
    return Response(combine_deals.render_page(rows, generated(latest), public=True, schedule=(words, times)), mimetype="text/html")


@app.get("/internal")
def internal_page():
    # The service is public; the internal view needs the INTERNAL_KEY set on the service (?key=...).
    key = os.environ.get("INTERNAL_KEY")
    if not key or not hmac.compare_digest(request.args.get("key", ""), key):
        abort(404)
    rows, latest = snapshot()
    return Response(combine_deals.render_page(rows, generated(latest)), mimetype="text/html")


@app.get("/api/deals")
def deals():
    rows, latest = snapshot()
    out = [r["public_view"] for r in rows if r.get("publish") and r.get("public_view")]
    module = request.args.get("module")
    if module:
        out = [r for r in out if r["module"] == module]
    if request.args.get("discount") == "1":
        out = [r for r in out if r.get("discount_percent") is not None]
    out.sort(key=lambda r: -(r.get("discount_percent") or 0))
    return jsonify({"generated_at": generated(latest), "count": len(out), "deals": out})


@app.get("/api/deals/<deal_id>")
def deal(deal_id):
    rows, _ = snapshot()
    for r in rows:
        if r["id"] == deal_id and r.get("publish"):
            return jsonify(r["public_view"])
    abort(404)


@app.get("/api/runs/latest")
def latest_run():
    _, latest = snapshot()
    return jsonify(latest or {})


# ---------------------------------------------------------------- click tracking
BOT = re.compile(r"bot|crawl|spider|slurp|preview|monitor|headless", re.I)


@app.post("/api/click")
def click():
    """Count one click on a deal's shop button. No IP address or cookie is stored."""
    if BOT.search(request.headers.get("User-Agent", "")):
        return ("", 204)
    payload = request.get_json(silent=True) or {}
    deal_id = str(payload.get("id", ""))[:200]
    rows, _ = snapshot()
    row = next((r for r in rows if r.get("id") == deal_id and r.get("publish")), None)
    if not row:
        return ("", 204)
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    inc = firestore.Increment(1)
    view = row.get("public_view") or {}
    store = view.get("store") or row.get("provider") or "Unknown"
    batch = db.batch()
    batch.set(db.collection("click_deals").document(combine_deals_doc_id(deal_id)),
              {"deal_id": deal_id, "title": view.get("title") or row.get("title"), "store": store, "module": row.get("module"),
               "buy_url": view.get("buy_url"), "total": inc, "days": {day: inc}, "last_click_at": firestore.SERVER_TIMESTAMP}, merge=True)
    batch.set(db.collection("click_days").document(day), {"day": day, "total": inc, "stores": {store: inc}}, merge=True)
    batch.commit()
    try:
        members.record_member_click(str(payload.get("token") or "")[:4096], deal_id, view)
    except Exception as exc:  # a member counter must never break the public click count
        print("member click not recorded:", exc, flush=True)
    return ("", 204)


def combine_deals_doc_id(deal_id):
    return deal_id.replace("/", "_")[:400]


# ---------------------------------------------------------------- admin
from google.auth.transport import requests as google_requests  # noqa: E402
from google.oauth2 import id_token  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import settings as admin_settings  # noqa: E402

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "dealradar-515523")
FIXED_ADMINS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
_greq = google_requests.Request()


def admin_emails():
    snap = db.collection("settings").document("admins").get()
    extra = {e.lower() for e in (snap.to_dict() or {}).get("emails", [])} if snap.exists else set()
    return FIXED_ADMINS | extra


_settings_cache = {"at": 0.0, "value": None}


def cached_settings():
    """Admin source settings, re-read at most once a minute (members' channel list and keyword whitelist use them)."""
    if _settings_cache["value"] is None or time.monotonic() - _settings_cache["at"] > 60:
        _settings_cache.update(at=time.monotonic(), value=admin_settings.load(db)[0])
    return _settings_cache["value"]


def verify_token(token):
    return id_token.verify_firebase_token(token, _greq, audience=PROJECT)


def current_admin():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        abort(401)
    try:
        claims = verify_token(auth[7:])
    except Exception:
        abort(401)
    email = (claims.get("email") or "").lower()
    if not claims.get("email_verified") or email not in admin_emails():
        abort(403)
    return email


import members  # noqa: E402

members.init(db, verify_token, snapshot, cached_settings)
members._ctx["collection_info"] = lambda: collection_info()
members.admin_members(current_admin)
app.register_blueprint(members.bp)
import push  # noqa: E402
app.register_blueprint(push.bp)  # the app registers phones for push notifications
import account  # noqa: E402
app.register_blueprint(account.bp)  # members delete their account
import legal  # noqa: E402
app.register_blueprint(legal.bp)  # /privacy and /support


@app.get("/admin")
def admin_page():
    return Response((Path(__file__).resolve().parent / "admin.html").read_text(), mimetype="text/html",
                    headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


@app.get("/my-deals")
def my_deals_page():
    """The home page and its filters over a signed-in member's own deals (loaded by the page with the member's token)."""
    _, latest = snapshot()
    words, times = public_schedule()
    page = combine_deals.render_page([], generated(latest), public=True, schedule=(words, times), mode="mine")
    return Response(page, mimetype="text/html", headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


@app.get("/account")
def account_page():
    return Response((Path(__file__).resolve().parent / "account.html").read_text(), mimetype="text/html",
                    headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


@app.get("/api/auth-config")
def auth_config():
    import json
    cfg = json.loads(os.environ.get("FIREBASE_WEB_CONFIG", "{}"))
    return jsonify({k: cfg.get(k) for k in ("apiKey", "authDomain", "projectId", "appId")})


@app.get("/api/admin/me")
def admin_me():
    return jsonify({"email": current_admin(), "admin": True})


@app.get("/api/admin/settings")
def admin_get_settings():
    current_admin()
    s, updated_at, updated_by = admin_settings.load(db)
    return jsonify({"settings": s, "updated_at": updated_at, "updated_by": updated_by,
                    "options": {"operators": admin_settings.OPERATORS, "unavailable_shops": admin_settings.UNAVAILABLE_SHOPS,
                                "shops": {k: v[1] for k, v in admin_settings.RETAIL_ADAPTERS.items()},
                                "categories": admin_settings.RETAIL_CATEGORIES}})


@app.put("/api/admin/settings")
def admin_put_settings():
    email = current_admin()
    try:
        clean = admin_settings.save(db, request.get_json(force=True), email)
    except (ValueError, TypeError, KeyError) as exc:
        return jsonify({"error": str(exc)}), 400
    _settings_cache["value"] = None
    return jsonify({"settings": clean, "saved": True})


@app.get("/api/admin/clicks")
def admin_clicks():
    current_admin()
    from datetime import datetime, timedelta, timezone
    days = max(1, min(int(request.args.get("days", 30)), 365))
    since = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    by_day = sorted((d.to_dict() for d in db.collection("click_days").where("day", ">=", since).stream()), key=lambda d: d["day"])
    stores = {}
    for d in by_day:
        for k, v in (d.get("stores") or {}).items():
            stores[k] = stores.get(k, 0) + v
    deals = []
    for d in db.collection("click_deals").stream():
        x = d.to_dict()
        n = sum(v for k, v in (x.get("days") or {}).items() if k >= since)
        if n:
            deals.append({"deal_id": x.get("deal_id"), "title": x.get("title"), "store": x.get("store"), "module": x.get("module"),
                          "buy_url": x.get("buy_url"), "clicks": n, "total": x.get("total", n)})
    deals.sort(key=lambda r: -r["clicks"])
    return jsonify({"days": days, "since": since, "total": sum(d.get("total", 0) for d in by_day),
                    "by_day": [{"day": d["day"], "clicks": d.get("total", 0)} for d in by_day],
                    "by_store": sorted(({"store": k, "clicks": v} for k, v in stores.items()), key=lambda r: -r["clicks"]),
                    "top_deals": deals[:100]})


@app.get("/api/admin/admins")
def admin_list():
    current_admin()
    snap = db.collection("settings").document("admins").get()
    return jsonify({"fixed": sorted(FIXED_ADMINS), "emails": sorted((snap.to_dict() or {}).get("emails", [])) if snap.exists else []})


@app.put("/api/admin/admins")
def admin_put_list():
    email = current_admin()
    import re
    emails = sorted({e.strip().lower() for e in (request.get_json(force=True) or {}).get("emails", []) if e and e.strip()})
    bad = [e for e in emails if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e)]
    if bad:
        return jsonify({"error": "Not an email address: " + ", ".join(bad)}), 400
    db.collection("settings").document("admins").set({"emails": emails, "updated_by": email})
    return jsonify({"emails": emails, "saved": True})


# ---------------------------------------------------------------- manual collection run
JOB = f"projects/{PROJECT}/locations/{os.environ.get('REGION', 'europe-west1')}/jobs/dealradar-collect"
_run_session = None


def run_api():
    global _run_session
    if _run_session is None:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _run_session = AuthorizedSession(creds)
    return _run_session


def executions(limit=5):
    r = run_api().get(f"https://run.googleapis.com/v2/{JOB}/executions", params={"pageSize": limit}, timeout=20)
    r.raise_for_status()
    out = []
    for e in r.json().get("executions", []):
        out.append({"name": e["name"].rsplit("/", 1)[-1], "started_at": e.get("startTime") or e.get("createTime"),
                    "finished_at": e.get("completionTime"), "running": not e.get("completionTime"),
                    "succeeded": e.get("succeededCount", 0) > 0, "failed": e.get("failedCount", 0) > 0,
                    "cancelled": e.get("cancelledCount", 0) > 0})
    return out


@app.get("/api/admin/run")
def admin_run_status():
    current_admin()
    latest = db.collection("meta").document("latest").get()
    return jsonify({"executions": executions(), "latest": latest.to_dict() if latest.exists else None,
                    "schedule": "07:00 and 19:00 Irish time"})


@app.post("/api/admin/run")
def admin_run_now():
    email = current_admin()
    running = [e for e in executions() if e["running"]]
    if running:
        return jsonify({"error": "A collection is already running. It started at " + (running[0]["started_at"] or "")[11:16] + " UTC."}), 409
    r = run_api().post(f"https://run.googleapis.com/v2/{JOB}:run", json={}, timeout=30)
    if r.status_code >= 300:
        return jsonify({"error": f"Couldn't start the collection (HTTP {r.status_code})."}), 502
    db.collection("admin_log").add({"action": "run_collection", "by": email, "at": firestore.SERVER_TIMESTAMP})
    return jsonify({"started": True})


# ---------------------------------------------------------------- collection schedule
SCHED = f"projects/{PROJECT}/locations/{os.environ.get('REGION', 'europe-west1')}/jobs/dealradar-collect"
MAX_RUNS, MIN_GAP_HOURS = 6, 2


def hour_label(h):
    return "midnight" if h == 0 else "noon" if h == 12 else f"{h % 12}{'am' if h < 12 else 'pm'}"


def schedule_words(hours):
    hours = sorted(hours)
    count = {1: "once a day", 2: "twice a day", 3: "three times a day"}.get(len(hours), f"{len(hours)} times a day")
    labels = [hour_label(h) for h in hours]
    times = labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
    return count, times


def validate_hours(hours):
    try:
        hours = sorted({int(h) for h in hours})
    except (TypeError, ValueError):
        raise ValueError("Times must be whole hours from 0 to 23.")
    if not hours:
        raise ValueError("Keep at least one run a day. Use the switch to stop automatic runs.")
    if len(hours) > MAX_RUNS:
        raise ValueError(f"At most {MAX_RUNS} runs a day.")
    if any(h < 0 or h > 23 for h in hours):
        raise ValueError("Times must be whole hours from 0 to 23.")
    gaps = [(hours[(i + 1) % len(hours)] - h) % 24 or 24 for i, h in enumerate(hours)]
    if len(hours) > 1 and min(gaps) < MIN_GAP_HOURS:
        raise ValueError(f"Runs need to be at least {MIN_GAP_HOURS} hours apart.")
    return hours


def read_schedule():
    r = run_api().get(f"https://cloudscheduler.googleapis.com/v1/{SCHED}", timeout=20)
    r.raise_for_status()
    job = r.json()
    parts = job.get("schedule", "0 7,19 * * *").split()
    hours = [int(h) for h in parts[1].split(",")] if len(parts) == 5 and parts[0] == "0" and re.fullmatch(r"[\d,]+", parts[1]) else []
    return {"hours": sorted(hours), "enabled": job.get("state") == "ENABLED", "timezone": job.get("timeZone"), "cron": job.get("schedule")}


@app.get("/api/admin/schedule")
def admin_get_schedule():
    current_admin()
    s = read_schedule()
    s["words"], s["times"] = schedule_words(s["hours"] or [7, 19])
    return jsonify(s)


@app.put("/api/admin/schedule")
def admin_put_schedule():
    email = current_admin()
    body = request.get_json(force=True) or {}
    try:
        hours = validate_hours(body.get("hours", []))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    enabled = bool(body.get("enabled", True))
    cron = "0 " + ",".join(str(h) for h in hours) + " * * *"
    r = run_api().patch(f"https://cloudscheduler.googleapis.com/v1/{SCHED}", params={"updateMask": "schedule,timeZone"},
                        json={"schedule": cron, "timeZone": "Europe/Dublin"}, timeout=20)
    if r.status_code >= 300:
        return jsonify({"error": f"Couldn't update the schedule (HTTP {r.status_code})."}), 502
    state = read_schedule()
    if state["enabled"] != enabled:
        run_api().post(f"https://cloudscheduler.googleapis.com/v1/{SCHED}:{'resume' if enabled else 'pause'}", json={}, timeout=20).raise_for_status()
    db.collection("settings").document("schedule").set({"hours": hours, "enabled": enabled, "updated_by": email,
                                                        "updated_at": firestore.SERVER_TIMESTAMP})
    _cache["at"] = None  # public page text follows the new schedule
    s = read_schedule()
    s["words"], s["times"] = schedule_words(s["hours"])
    return jsonify({**s, "saved": True})


def collection_info():
    """When deals were last collected and when the next scheduled collection starts (Irish time), for members."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    snap = db.collection("settings").document("schedule").get()
    cfg = (snap.to_dict() or {}) if snap.exists else {}
    hours, enabled = sorted(cfg.get("hours") or [7, 19]), cfg.get("enabled", True)
    now = datetime.now(ZoneInfo("Europe/Dublin"))
    nxt = None
    if enabled:
        for add in range(0, 3):
            day = (now + timedelta(days=add)).date()
            for h in hours:
                t = datetime(day.year, day.month, day.day, h, tzinfo=ZoneInfo("Europe/Dublin"))
                if t > now:
                    nxt = t
                    break
            if nxt:
                break
    _, latest = snapshot()
    return {"last": generated(latest) or None, "next": nxt.isoformat() if nxt else None, "automatic": bool(enabled)}


def public_schedule():
    snap = db.collection("settings").document("schedule").get()
    hours = (snap.to_dict() or {}).get("hours") if snap.exists else None
    return schedule_words(hours or [7, 19])
