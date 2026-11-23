"""DealRadar web service (Cloud Run): UI page plus a read-only JSON API over Firestore.

  GET /                      public page: automatically published deals only
  GET /internal              internal view: every record with its publish decision
  GET /api/deals             published deals (public fields). ?module=operator|retailer|amazon ?discount=1
  GET /api/deals/<id>        one published deal
  GET /api/runs/latest       latest collection run summary
  GET /health
"""
import os
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


@app.get("/")
def public_page():
    rows, latest = snapshot()
    return Response(combine_deals.render_page(rows, generated(latest), public=True), mimetype="text/html")


@app.get("/internal")
def internal_page():
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
