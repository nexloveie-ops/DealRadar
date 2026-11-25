#!/usr/bin/env python3
"""DealRadar Cloud Run Job: run the three collectors, merge, store in Firestore and Cloud Storage.

State between runs (previous offers, price history) lives in gs://$STATE_BUCKET/state/. Raw evidence
goes to evidence/<run_id>/ and run outputs to runs/<run_id>/ (both expire via bucket lifecycle).
Amazon Creators API content is never archived: it lives only in Firestore and is replaced each run.

  python cloud/run_collect.py              # full run
  python cloud/run_collect.py --load-only  # rebuild Firestore from state/ without collecting
"""
import argparse
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.cloud import firestore, storage

APP = Path(__file__).resolve().parent.parent
OUTPUTS = APP / "outputs"
sys.path.insert(0, str(OUTPUTS))
import combine_deals  # noqa: E402
sys.path.insert(0, str(APP / "cloud"))
import settings as admin_settings  # noqa: E402
import members  # noqa: E402
import push  # noqa: E402

BATCH = 400
RUN_RECORD_DAYS = 7  # run records in Firestore; the bucket keeps runs/ and evidence/ for 7 days too


def amazon_mode():
    if os.environ.get("AMAZON_CREATORS_CLIENT_ID") and os.environ.get("AMAZON_CREATORS_CLIENT_SECRET"):
        return "api"
    # "local": Amazon.ie hides prices from Google Cloud addresses, so the Amazon step runs on the owner's Mac
    # (local/amazon_local.sh) and uploads to state/amazon/. The cloud job only reads that upload.
    return os.environ.get("AMAZON_MODE", "direct")


def modules(mode):
    m = {
        "prepay": {"dir": "prepay-offers-service", "script": "collect.py", "args": [], "state": ["offers.json"], "archive": True},
        "retailer": {"dir": "retailer-offers-service", "script": "collect.py", "args": [], "state": ["offers.json", "price-history.json"], "archive": True},
        "very": {"dir": "retailer-offers-service", "script": "very.py", "args": [], "state": ["offers.json"], "archive": True},
        "harvey_norman": {"dir": "retailer-offers-service", "script": "harvey_norman.py", "args": [], "state": ["offers.json"],
                          "archive": True, "no_cache": True},
        # Products members watch (Harvey Norman, Expert, Powercity, Mobile123); Amazon ones go to the Amazon step.
        "watch": {"dir": "watch-service", "script": "check.py", "args": [], "state": [], "archive": True, "no_cache": True},
        "mobile123": {"dir": "mobile123-offers-service", "script": "collect.py", "args": [], "state": ["offers.json"], "archive": True},
    }
    if mode == "local":
        m["amazon"] = {"dir": "amazon-offers-service", "skip": True, "state": ["offers.json", "run-report.json"], "archive": False}
    elif mode == "api":
        m["amazon"] = {"dir": "amazon-offers-service", "script": "collect.py", "args": ["--live"], "state": [], "archive": False, "no_cache": True}
    else:
        # Third-party deal sites as extra leads, then Amazon.ie search + product-page confirmation.
        m["amazon_leads"] = {"dir": "amazon-offers-service", "script": "web_collect.py", "args": [], "state": ["offers.json"], "archive": True}
        # Shares state/amazon/ with the Mac runner (local/amazon_local.sh): both re-check earlier deals and keep
        # what the other confirmed until it expires.
        m["amazon"] = {"dir": "amazon-offers-service", "script": "amazon_direct.py", "args": [], "state": ["offers.json", "search-cursor.json", "rejected.json"],
                       "archive": True, "leads_from": "amazon_leads", "previous_self": True}
    return m


def download_state(bucket, key, files, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for name in files:
        blob = bucket.blob(f"state/{key}/{name}")
        if blob.exists():
            blob.download_to_filename(dest / name)


def run_collector(key, spec, work):
    out, cache = work / "out" / key, work / "cache" / key
    cache.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, spec["script"], "--out", str(out)] + ([] if spec.get("no_cache") else ["--cache", str(cache)]) + spec["args"]
    if spec.get("leads_from"):
        cmd += ["--leads", str(work / "out" / spec["leads_from"] / "offers.json")]
    if spec.get("previous_self") and (out / "offers.json").exists():
        prev = work / f"{key}-previous.json"
        prev.write_bytes((out / "offers.json").read_bytes())
        cmd += ["--previous", str(prev)]
    started = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=OUTPUTS / spec["dir"], capture_output=True, text=True, timeout=1500, env=os.environ.copy())
        code, log = p.returncode, (p.stdout + "\n" + p.stderr)
    except subprocess.TimeoutExpired as exc:
        code, log = -1, "timeout: " + str(exc)
    duration = round(time.monotonic() - started, 1)
    print(f"[{key}] exit={code} {duration}s\n" + log[-3000:], flush=True)
    # 0 = all sources fine; 2 = some sources blocked/failed but results saved.
    return {"exit_code": code, "duration_seconds": duration, "ok": code in (0, 2), "log_tail": log[-1500:]}


def upload_dir(bucket, src, prefix):
    for f in sorted(Path(src).glob("*")):
        if f.is_file():
            bucket.blob(f"{prefix}/{f.name}").upload_from_filename(f)


def upload_evidence(bucket, cache, prefix):
    if not cache.exists() or not any(cache.iterdir()):
        return
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in sorted(cache.iterdir()):
            if f.is_file() and "cookie" not in f.name.lower():
                tar.add(f, arcname=f.name)
    buf.seek(0)
    bucket.blob(prefix + ".tar.gz").upload_from_file(buf, content_type="application/gzip")


def doc_id(record_id):
    return record_id.replace("/", "_")[:400]


def write_firestore(db, rows, raw_by_id, history, run_doc, generated):
    deals = db.collection("deals")
    current = {doc_id(r["id"]) for r in rows}
    batch, n, written = db.batch(), 0, 0

    def flush(force=False):
        nonlocal batch, n
        if n and (force or n >= BATCH):
            batch.commit()
            batch, n = db.batch(), 0

    for r in rows:
        data = dict(r)
        data["source_record_json"] = json.dumps(raw_by_id.get(r["id"]), ensure_ascii=False) if r["id"] in raw_by_id else None
        data["updated_at"] = generated
        batch.set(deals.document(doc_id(r["id"])), data)
        n += 1
        written += 1
        flush()
    removed = 0
    for snap in deals.select([]).stream():
        if snap.id not in current:
            batch.delete(snap.reference)
            n += 1
            removed += 1
            flush()
    hist = db.collection("price_history")
    for oid, points in history.items():
        batch.set(hist.document(doc_id(oid)), {"offer_id": oid, "points": points, "updated_at": generated})
        n += 1
        flush()
    # Old data is not kept: price histories of items no longer tracked, and run records older than RUN_RECORD_DAYS.
    keep = {doc_id(oid) for oid in history}
    for snap in hist.select([]).stream():
        if snap.id not in keep:
            batch.delete(snap.reference)
            n += 1
            flush()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RUN_RECORD_DAYS)).strftime("%Y%m%dT%H%M%SZ")
    for snap in db.collection("runs").select([]).stream():
        if snap.id < cutoff:
            batch.delete(snap.reference)
            n += 1
            flush()
    flush(force=True)
    db.collection("runs").document(run_doc["run_id"]).set(run_doc)
    db.collection("meta").document("latest").set({k: run_doc[k] for k in ("run_id", "finished_at", "counts", "amazon_mode", "modules_ok")})
    return written, removed


def member_view(o):
    """The public fields the member page needs, for an Amazon offer found through a member's private keyword."""
    p, v = o["pricing"], o["verification"]
    return {"id": o["id"], "module": "amazon", "store": "Amazon.ie", "title": o["product"]["name"], "asin": o["product"]["asin"],
            "price_minor": p["device_price_minor"], "reference_minor": p["reference_price_minor"], "discount_percent": p["discount_percent"],
            "image_url": (o.get("media") or {}).get("image_url"), "buy_url": o["links"]["affiliate_url"], "affiliate": True,
            "price_source": "amazon_page", "checked_at": v.get("verified_at"), "category": "", "reference_kind": "reference"}


def write_member_finds(db, path, member_kws, generated):
    """member_finds/{keyword}: replaced for each private keyword searched this run; others keep their last results."""
    if not path.exists():
        return 0
    data = json.loads(path.read_text())
    queries = {k["id"]: k["query"] for k in member_kws}
    n = 0
    for kid in data.get("searched_keyword_ids", []):
        deals = sorted((member_view(o) for o in data.get("offers", []) if "m:" + kid in (o.get("matched_keyword_ids") or [])),
                       key=lambda d: -(d["discount_percent"] or 0))
        db.collection("member_finds").document(kid).set({"keyword_id": kid, "query": queries.get(kid, kid), "searched_at": generated,
                                                         "deals": deals[:60]})
        n += len(deals)
    return n


def write_watch_status(db, paths, keep):
    """watch_status/{site}:{key}: the last check of each watched product (see members.merge_watch_result).
    Results for products nobody watches any more are removed."""
    coll = db.collection(members.WATCH_DOC)
    now = datetime.now(timezone.utc)
    n = 0
    for path in paths:
        if not path.exists():
            continue
        for r in json.loads(path.read_text()).get("items", []):
            doc = members.watch_doc_id(r["site"], r["key"])
            prev_snap = coll.document(doc).get()
            prev = prev_snap.to_dict() if prev_snap.exists else {}
            rec = members.merge_watch_result(prev, r, now)
            if rec is None:  # keep the last good check; just note this run didn't reach it
                coll.document(doc).update({"last_attempt_at": now.isoformat(timespec="seconds"), "last_error": r.get("error")})
                continue
            coll.document(doc).set(rec)
            n += 1
    if keep is not None:
        for snap in coll.select([]).stream():
            if snap.id not in keep:
                snap.reference.delete()
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--load-only", action="store_true")
    args = ap.parse_args()
    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("%Y%m%dT%H%M%SZ") + ("-load" if args.load_only else "")
    gcs = storage.Client()
    bucket = gcs.bucket(os.environ["STATE_BUCKET"])
    db = firestore.Client(database=os.environ.get("FIRESTORE_DATABASE", "(default)"))
    mode = amazon_mode()
    specs = modules(mode)
    # Admin settings (Firestore settings/sources) decide which pages and keywords the collectors use.
    current, settings_at, _ = admin_settings.load(db)
    applied = admin_settings.apply(current)
    if "very" in applied["disabled_shops"]:
        specs["very"]["args"] = ["--disabled"]
    combine_deals.DISABLED_OPERATORS = set(applied["disabled_operators"])
    combine_deals.DISABLED_SHOPS = set(applied["disabled_shops"])
    combine_deals.PUBLIC_MIN_DISCOUNT = float(applied["retail_min_discount"])
    work = Path(tempfile.mkdtemp(prefix="dealradar-"))
    # Members' searching words (each member's first N words that have not expired) join the search pools:
    # Amazon gets the ones not already on the admin keyword list; Harvey Norman gets all of them.
    member_kws, searching = [], []
    if not args.load_only:
        try:
            searching = members.searching_words_for_collector(db, lambda: current)
        except Exception as exc:
            print("member search words not loaded:", exc, flush=True)
        member_kws = [w for w in searching if not w["on_whitelist"]]
        if specs.get("amazon", {}).get("script") == "amazon_direct.py":
            (work / "member-keywords.json").write_text(json.dumps(member_kws))
            specs["amazon"]["args"] = specs["amazon"]["args"] + ["--member-keywords", str(work / "member-keywords.json")]
        if "harvey_norman" in specs:
            (work / "member-words-hn.json").write_text(json.dumps(searching))
            specs["harvey_norman"]["args"] = specs["harvey_norman"]["args"] + ["--member-keywords", str(work / "member-words-hn.json")]
    watch_items, watch_keep = [], None  # None: not loaded, so existing check results are left alone
    if not args.load_only:
        try:
            watch_items, watch_keep = members.watched_items_for_collector(db)
        except Exception as exc:
            print("watched products not loaded:", exc, flush=True)
        (work / "watch-items.json").write_text(json.dumps(watch_items))
        specs["watch"]["args"] = ["--items", str(work / "watch-items.json")]
        if specs.get("amazon", {}).get("script") == "amazon_direct.py":
            specs["amazon"]["args"] = specs["amazon"]["args"] + ["--watch", str(work / "watch-items.json")]
    elif "watch" in specs:
        specs["watch"]["skip"] = True
    results = {}
    for key, spec in specs.items():
        files = spec["state"] + (["offers.json", "run-report.json"] if args.load_only else [])
        download_state(bucket, key, sorted(set(files)), work / "out" / key)
        if args.load_only or spec.get("skip"):
            results[key] = {"exit_code": None, "ok": True, "duration_seconds": 0,
                            "log_tail": "load-only" if args.load_only else "collected on the owner's Mac; read from state/"}
        else:
            results[key] = run_collector(key, spec, work)

    out = {k: work / "out" / k for k in specs}
    combine_deals.INPUTS.update({
        "prepay-offers-service/data/offers.json": out["prepay"] / "offers.json",
        "retailer-offers-service/data/offers.json": out["retailer"] / "offers.json",
        "harvey-norman": out["harvey_norman"] / "offers.json",
        "very": out["very"] / "offers.json",
        "mobile123-offers-service/data/offers.json": out["mobile123"] / "offers.json",
        "amazon-offers-service/web-data/offers.json": out["amazon"] / "offers.json",
        "amazon-leads": out["amazon_leads"] / "offers.json" if "amazon_leads" in out else Path("/nonexistent"),
        "amazon-direct-rejected": out["amazon"] / "rejected.json",
    })
    combine_deals.AMAZON_SOURCE = "direct" if mode == "local" else mode
    rows = combine_deals.build_rows()
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    raw_by_id = {}
    for key in specs:
        f = out[key] / "offers.json"
        if f.exists():
            for o in json.loads(f.read_text()).get("offers", []):
                raw_by_id[o["id"]] = o
    hist_file = out["retailer"] / "price-history.json"
    history = json.loads(hist_file.read_text())["items"] if hist_file.exists() else {}

    counts = {m: sum(r["module"] == m for r in rows) for m in ("mobile123", "operator", "retailer", "amazon")}
    counts.update(total=len(rows), with_discount=sum(r["discount_percent"] is not None for r in rows),
                  operator_listable=sum(r["module"] == "operator" and r["can_list"] for r in rows))
    reports = {}
    for key in specs:
        f = out[key] / "run-report.json"
        if f.exists():
            rep = json.loads(f.read_text())
            reports[key] = {k: rep.get(k) for k in ("generated_at", "counts", "duration_seconds", "providers", "api_status", "errors") if k in rep}
    run_doc = {"run_id": run_id, "started_at": started_at.isoformat(timespec="seconds"), "finished_at": generated,
               "load_only": args.load_only, "amazon_mode": mode, "modules": results, "settings_updated_at": settings_at,
               "disabled_operators": applied["disabled_operators"],
               "modules_ok": all(r["ok"] for r in results.values()), "counts": counts, "module_reports": reports}

    written, removed = write_firestore(db, rows, raw_by_id, history, run_doc, generated)
    if member_kws:
        run_doc["member_finds"] = write_member_finds(db, out["amazon"] / "member-offers.json", member_kws, generated)
    if not args.load_only:
        run_doc["watch_checked"] = write_watch_status(db, [out["watch"] / "status.json", out["amazon"] / "watch-status.json"], watch_keep)
        # Push notifications to the app: watched products at their target, and in the evening new deals.
        run_doc["push"] = push.after_collection(db, rows)
    print(json.dumps({"firestore_written": written, "firestore_removed": removed, **counts}), flush=True)

    # Cloud Storage: state for the next run, archive of this run, raw evidence, merged page.
    for key, spec in specs.items():
        if not spec["archive"]:
            continue
        if not args.load_only and results[key]["ok"]:
            for name in spec["state"]:
                if (out[key] / name).exists():
                    bucket.blob(f"state/{key}/{name}").upload_from_filename(out[key] / name)
            upload_dir(bucket, out[key], f"runs/{run_id}/{key}")
            upload_evidence(bucket, work / "cache" / key, f"evidence/{run_id}/{key}")
    page = combine_deals.render_page(rows, generated)
    bucket.blob(f"runs/{run_id}/run.json").upload_from_string(json.dumps(run_doc, ensure_ascii=False, indent=1), content_type="application/json")
    if mode != "api":
        bucket.blob(f"runs/{run_id}/combined/index.html").upload_from_string(page, content_type="text/html; charset=utf-8")
    return 0 if run_doc["modules_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
