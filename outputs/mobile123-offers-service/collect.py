#!/usr/bin/env python3
"""Mobile123's own discounted products, from the store's public Shopify feed (mobile123.ie/products.json).

A product counts as discounted when an in-stock variant's price is below its "compare at" price.
For each product the variant with the biggest discount is listed. No login, no API key.

  python collect.py --cache DIR --out DIR [--offline]
"""
import argparse
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

STORE = "https://www.mobile123.ie"
PAGE_SIZE = 250
MAX_PAGES = 20

CATEGORY_RULES = [
    ("phones", r"unlocked mobile phones|^mobile phones$|smartphone"),
    ("headphones", r"headphone|earbud|earphone|headset"),
    ("speakers", r"speaker"),
    ("wearables", r"watch|wearable|fitness"),
    ("tablets", r"tablet|ipad"),
    ("computers", r"laptop(?! charger)|computer|keyboard|mouse|monitor"),
    ("gaming", r"gaming|console|controller"),
    ("toys", r"lego|toy|trading card|pok[eé]mon|tcg|puzzle|game(s)?$"),
    ("accessories", r"charger|cable|adapter|case|stand|protector|power bank|mount|holder|accessor"),
]


def money(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def category(product):
    text = " ".join([product.get("product_type") or "", " ".join(product.get("tags") or []) if isinstance(product.get("tags"), list) else str(product.get("tags") or ""), product.get("title") or ""]).lower()
    for cat, pattern in CATEGORY_RULES:
        if re.search(pattern, (product.get("product_type") or "").lower()):
            return cat
    for cat, pattern in CATEGORY_RULES:
        if re.search(pattern, text):
            return cat
    return "other"


def fetch(page, cache, offline):
    path = Path(cache) / f"products-p{page}.json"
    if offline:
        return json.loads(path.read_text()), json.loads((Path(cache) / f"products-p{page}.meta.json").read_text())
    url = f"{STORE}/products.json?limit={PAGE_SIZE}&page={page}"
    p = subprocess.run(["curl", "-s", "--compressed", "--proto", "=https", "--max-time", "30", "-o", str(path), "-w", "%{http_code}", url],
                       capture_output=True, text=True, timeout=45)
    if p.stdout != "200":
        raise RuntimeError(f"HTTP {p.stdout} at {url}")
    raw = path.read_bytes()
    meta = {"url": url, "fetched_at": datetime.now(timezone.utc).isoformat(), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    (Path(cache) / f"products-p{page}.meta.json").write_text(json.dumps(meta))
    return json.loads(raw), meta


def offers_from(products, fetched_at, freshness_hours=24):
    rows, skipped = [], []
    for p in products:
        best = None
        for v in p.get("variants", []):
            if not v.get("available") or not v.get("compare_at_price"):
                continue
            price, ref = money(v["price"]), money(v["compare_at_price"])
            if ref <= price or price <= 0:
                continue
            pct = float((Decimal(ref - price) * 100 / ref).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
            if best is None or pct > best[2]:
                best = (v, price, pct, ref)
        if best is None:
            skipped.append({"handle": p.get("handle"), "reason": "no_in_stock_discounted_variant"})
            continue
        v, price, pct, ref = best
        image = (v.get("featured_image") or {}).get("src") or ((p.get("images") or [{}])[0]).get("src")
        title = p["title"]
        if v.get("title") and v["title"] != "Default Title" and v["title"].lower() not in title.lower():
            title = f"{title} ({v['title']})"
        rows.append({
            "id": f"ie-mobile123-{p['id']}", "module_id": "mobile123_promotions", "provider_id": "mobile123", "provider_name": "Mobile123",
            "country": "IE", "currency": "EUR", "offer_kind": "product_discount",
            "product": {"sku": v.get("sku") or str(v["id"]), "name": title, "category": category(p), "product_type": p.get("product_type"),
                        "condition": "refurbished" if re.search(r"refurb|pre-?owned|renewed", title, re.I) else "as_listed"},
            "pricing": {"device_price_minor": price, "reference_price_minor": ref, "reference_type": "store_compare_at_price",
                        "discount_minor": ref - price, "discount_percent": pct},
            "stock_status": "in_stock", "status": "active",
            "verification": {"method": "own_store_feed", "observed_at": fetched_at,
                             "fresh_until": (datetime.fromisoformat(fetched_at) + timedelta(hours=freshness_hours)).isoformat(), "issues": []},
            "links": {"landing_url": f"{STORE}/products/{p['handle']}?variant={v['id']}", "affiliate_url": None},
            "media": {"image_url": image, "publish_image": bool(image)},
            "distribution": {"can_list": True, "notification_ready": False},
        })
    rows.sort(key=lambda r: -r["pricing"]["discount_percent"])
    return rows, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    started = time.monotonic()
    Path(args.cache).mkdir(parents=True, exist_ok=True)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    products, fetched_at, errors = [], None, []
    try:
        for page in range(1, MAX_PAGES + 1):
            data, meta = fetch(page, args.cache, args.offline)
            fetched_at = fetched_at or meta["fetched_at"]
            batch = data.get("products", [])
            products.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            if not args.offline:
                time.sleep(1)
    except (RuntimeError, FileNotFoundError, json.JSONDecodeError) as exc:
        errors.append(str(exc)[:250])
    rows, skipped = offers_from(products, fetched_at or datetime.now(timezone.utc).isoformat()) if products else ([], [])
    now = datetime.now(timezone.utc).isoformat()
    (out / "offers.json").write_text(json.dumps({"schema_version": "1.0.0", "module_id": "mobile123_promotions", "generated_at": now,
                                                 "offers": rows}, ensure_ascii=False, indent=1) + "\n")
    report = {"generated_at": now, "duration_seconds": round(time.monotonic() - started, 2), "products_seen": len(products),
              "discounted_in_stock": len(rows), "skipped": len(skipped), "errors": errors, "offline": args.offline}
    (out / "run-report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report))
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
