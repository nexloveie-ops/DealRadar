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
from datetime import datetime, timezone
from pathlib import Path

from google.cloud import firestore, storage

APP = Path(__file__).resolve().parent.parent
OUTPUTS = APP / "outputs"
sys.path.insert(0, str(OUTPUTS))
import combine_deals  # noqa: E402

BATCH = 400


def amazon_mode():
    return "api" if os.environ.get("AMAZON_CREATORS_CLIENT_ID") and os.environ.get("AMAZON_CREATORS_CLIENT_SECRET") else "direct"


def modules(mode):
    m = {
        "prepay": {"dir": "prepay-offers-service", "script": "collect.py", "args": [], "state": ["offers.json"], "archive": True},
        "retailer": {"dir": "retailer-offers-service", "script": "collect.py", "args": [], "state": ["offers.json", "price-history.json"], "archive": True},
    }
    if mode == "api":
        m["amazon"] = {"dir": "amazon-offers-service", "script": "collect.py", "args": ["--live"], "state": [], "archive": False, "no_cache": True}
    else:
        # Third-party deal sites as extra leads, then Amazon.ie search + product-page confirmation.
        m["amazon_leads"] = {"dir": "amazon-offers-service", "script": "web_collect.py", "args": [], "state": ["offers.json"], "archive": True}
        m["amazon"] = {"dir": "amazon-offers-service", "script": "amazon_direct.py", "args": [], "state": ["search-cursor.json"], "archive": True, "leads_from": "amazon_leads"}
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
    flush(force=True)
    db.collection("runs").document(run_doc["run_id"]).set(run_doc)
    db.collection("meta").document("latest").set({k: run_doc[k] for k in ("run_id", "finished_at", "counts", "amazon_mode", "modules_ok")})
    return written, removed


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
    work = Path(tempfile.mkdtemp(prefix="dealradar-"))
    results = {}
    for key, spec in specs.items():
        files = spec["state"] + (["offers.json", "run-report.json"] if args.load_only else [])
        download_state(bucket, key, sorted(set(files)), work / "out" / key)
        if args.load_only:
            results[key] = {"exit_code": None, "ok": True, "duration_seconds": 0, "log_tail": "load-only"}
        else:
            results[key] = run_collector(key, spec, work)

    out = {k: work / "out" / k for k in specs}
    combine_deals.INPUTS.update({
        "prepay-offers-service/data/offers.json": out["prepay"] / "offers.json",
        "retailer-offers-service/data/offers.json": out["retailer"] / "offers.json",
        "amazon-offers-service/web-data/offers.json": out["amazon"] / "offers.json",
    })
    combine_deals.AMAZON_SOURCE = mode
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

    counts = {m: sum(r["module"] == m for r in rows) for m in ("operator", "retailer", "amazon")}
    counts.update(total=len(rows), with_discount=sum(r["discount_percent"] is not None for r in rows),
                  operator_listable=sum(r["module"] == "operator" and r["can_list"] for r in rows))
    reports = {}
    for key in specs:
        f = out[key] / "run-report.json"
        if f.exists():
            rep = json.loads(f.read_text())
            reports[key] = {k: rep.get(k) for k in ("generated_at", "counts", "duration_seconds", "providers", "api_status", "errors") if k in rep}
    run_doc = {"run_id": run_id, "started_at": started_at.isoformat(timespec="seconds"), "finished_at": generated,
               "load_only": args.load_only, "amazon_mode": mode, "modules": results,
               "modules_ok": all(r["ok"] for r in results.values()), "counts": counts, "module_reports": reports}

    written, removed = write_firestore(db, rows, raw_by_id, history, run_doc, generated)
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
