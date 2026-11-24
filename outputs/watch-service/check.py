#!/usr/bin/env python3
"""Check the products members watch, one request per product, at Harvey Norman, Expert, Powercity and Mobile123.
(Amazon products are checked by the Amazon collector, which shares Amazon's small request budget.)

  python check.py --items items.json --out DIR

items.json: [{"site": "Expert", "key": "...", "url": "https://...", "product_id": optional}]
Writes DIR/status.json: {"items": [{site, key, status, title, image_url, price_minor, reference_minor,
discount_percent, available, buy_url, checked_at, product_id?, error?}]}
status: "checked" (price read), "unavailable" (out of stock / no longer sold), "not_found", "error".
Whether it is on offer is decided afterwards, from the price and the reference (shop's was price, or for shops
without one the highest price seen recently).
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "retailer-offers-service"))
UA = "DealRadarBot/1.0 (+https://dealradar-web-815864259513.europe-west1.run.app/)"
GAP = {"Expert": 10.0, "Powercity": 1.0, "Mobile123": 1.0, "Harvey Norman": 1.5, "Amazon.ie": 5.0}  # Expert asks for 10 s between requests


def money(v):
    return None if v is None else int((Decimal(str(v)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def pct(price, ref):
    if price is None or ref is None or ref <= price or price <= 0:
        return None
    return float((Decimal(ref - price) * 100 / ref).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def get(url, timeout=30, retries=1):
    """One polite request; on 429 (too many requests) wait and try once more."""
    for attempt in range(retries + 1):
        p = subprocess.run(["curl", "-s", "-L", "--compressed", "--proto", "=https", "--max-time", str(timeout), "-A", UA,
                            "-w", "\n%{http_code}", url], capture_output=True, text=True, timeout=timeout + 15)
        body, _, code = p.stdout.rpartition("\n")
        code = int(code) if code.isdigit() else 0
        if code != 429 or attempt == retries:
            return code, body
        time.sleep(20)
    return code, body


def check_expert(item):
    code, html = get(item["url"])
    if code == 404:
        return {"status": "not_found"}
    if code != 200:
        return {"status": "error", "error": f"HTTP {code}"}
    m = re.search(r'"price":\{"with_tax":\{[^}]*"value":([\d.]+)', html)
    was = re.search(r'"non_sale_price_with_tax":\{[^}]*"value":([\d.]+)', html)
    rrp = re.search(r'"rrp_with_tax":\{[^}]*"value":([\d.]+)', html)
    if not m:
        return {"status": "error", "error": "price not found on the product page"}
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.select_one("h1") or soup.select_one("title")).get_text(" ", strip=True)
    img = soup.select_one('meta[property="og:image"]')
    price = money(m[1])
    ref = money((was or rrp)[1]) if (was or rrp) else None
    stock = re.search(r'"instock":(true|false)', html)
    buyable = re.search(r'"purchasable":(true|false)', html)
    available = (stock[1] == "true" if stock else True) and (buyable[1] == "true" if buyable else True)
    return {"status": "checked" if available else "unavailable", "title": title, "image_url": img.get("content") if img else None,
            "price_minor": price, "reference_minor": ref if ref and ref > price else None, "available": available}


def check_powercity(item):
    code, html = get(item["url"])
    if code == 404:
        return {"status": "not_found"}
    if code != 200:
        return {"status": "error", "error": f"HTTP {code}"}
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.select_one("#pricetag")
    m = re.search(r"€\s*([\d,]+(?:\.\d{1,2})?)", tag.get_text(" ", strip=True)) if tag else None
    if not m:
        return {"status": "error", "error": "price not found on the product page"}
    text = soup.get_text(" ", strip=True)
    stock = re.search(r"Stock\s+(\d+)\s+pcs\s+available", text, re.I)
    title = (soup.select_one("title").get_text(" ", strip=True) if soup.select_one("title") else "").replace("POWERCITY - ", "")
    available = bool(stock and int(stock[1]) > 0)
    sku = item["key"]
    return {"status": "checked" if available else "unavailable", "title": title,
            "image_url": f"https://powercity.cdn.webangel.ie/productimages/thumb/{sku}.jpg" if re.fullmatch(r"[A-Za-z0-9_-]+", sku) else None,
            "price_minor": money(m[1].replace(",", "")), "reference_minor": None, "available": available}


def check_mobile123(item):
    code, body = get(f"https://www.mobile123.ie/products/{item['key']}.js")
    if code == 404:
        return {"status": "not_found"}
    if code != 200:
        return {"status": "error", "error": f"HTTP {code}"}
    d = json.loads(body)
    vid = re.search(r"variant=(\d+)", item["url"])
    vs = d.get("variants") or []
    v = next((x for x in vs if vid and str(x["id"]) == vid[1]), None) or next((x for x in vs if x.get("available")), None) or (vs[0] if vs else None)
    if not v:
        return {"status": "not_found"}
    img = d.get("featured_image")
    price, ref = v["price"], v.get("compare_at_price")
    return {"status": "checked" if v.get("available") else "unavailable", "title": d.get("title"),
            "image_url": ("https:" + img) if img and img.startswith("//") else img, "price_minor": price,
            "reference_minor": ref if ref and ref > price else None, "available": bool(v.get("available"))}


def check_harvey_norman(items):
    """Harvey Norman through its official catalogue interface: products seen before by ID (10 at a time),
    new ones by searching their name and matching the link."""
    import harvey_norman as hn
    out = {}
    meta = {"meta": {"ucp-agent": {"profile": hn.PROFILE}}}
    known = [i for i in items if i.get("product_id")]
    for n in range(0, len(known), 10):
        chunk = known[n:n + 10]
        try:
            res = hn.rpc("lookup_catalog", {**meta, "catalog": {"ids": [i["product_id"] for i in chunk],
                                                                 "context": {"address_country": "IE", "currency": "EUR"}}})
        except Exception as exc:
            for i in chunk:
                out[i["key"]] = {"status": "error", "error": str(exc)[:150]}
            continue
        by_id = {p.get("id"): p for p in res.get("products") or []}
        for i in chunk:
            out[i["key"]] = hn_result(by_id.get(i["product_id"]), i)
        time.sleep(GAP["Harvey Norman"])
    for i in items:
        if i["key"] in out and out[i["key"]]["status"] != "not_found":
            continue
        try:
            products, _ = hn.search(i["key"].replace("-", " "), hn.PROFILE, 1, 10, GAP["Harvey Norman"])
        except Exception as exc:
            out[i["key"]] = {"status": "error", "error": str(exc)[:150]}
            continue
        out[i["key"]] = hn_result(next((p for p in products if p.get("handle") == i["key"]), None), i)
        time.sleep(GAP["Harvey Norman"])
    return out


def hn_result(p, item):
    if not p:
        return {"status": "not_found"}
    vs = p.get("variants") or []
    vid = re.search(r"variant=(\d+)", item.get("url", ""))
    v = next((x for x in vs if vid and x.get("id", "").endswith("/" + vid[1])), None) \
        or next((x for x in vs if (x.get("availability") or {}).get("available")), None) or (vs[0] if vs else None)
    if not v:
        return {"status": "not_found"}
    price, ref = (v.get("price") or {}).get("amount"), (v.get("list_price") or {}).get("amount")
    available = bool((v.get("availability") or {}).get("available"))
    img = next((m.get("url") for m in (v.get("media") or []) + (p.get("media") or []) if m.get("type") == "image"), None)
    return {"status": "checked" if available else "unavailable", "title": p.get("title"), "image_url": img,
            "price_minor": price, "reference_minor": ref if ref and price and ref > price else None, "available": available,
            "product_id": p.get("id")}


def check_amazon_page(item, allow_search=False):
    """Amazon instant check when a member adds a product: the product page, and if Amazon hides the price there
    (common from cloud servers) and allow_search is set, one Amazon search for the ASIN, whose result shows
    Amazon's price and crossed-out price. Otherwise the next collection reads it."""
    sys.path.insert(0, str(ROOT.parent / "amazon-offers-service"))
    import amazon_direct as ad
    if not re.fullmatch(r"[A-Z0-9]{10}", item["key"]):
        return {"status": "not_found"}
    code, html = get(f"https://www.amazon.ie/dp/{item['key']}", retries=0)
    if code in (429, 503) or (code == 200 and len(html) < 5000):
        return {"status": "not_checked", "error": "Amazon refused the request; read at the next collection"}
    if code == 404:
        return {"status": "not_found"}
    if code != 200:
        return {"status": "error", "error": f"HTTP {code}"}
    pp = ad.parse_product(html)
    if pp["price_minor"] is None:
        if allow_search:
            time.sleep(GAP["Amazon.ie"])
            scode, shtml = get(f"https://www.amazon.ie/s?k={item['key']}", retries=0)
            if scode == 200 and len(shtml) > 5000:
                row = next((x for x in ad.parse_search(shtml)[0] if x["asin"] == item["key"]), None)
                if row and row["price_minor"] is not None:
                    return {"status": "checked", "title": pp["title"] or row["title"], "image_url": pp.get("image_url") or row.get("image_url"),
                            "price_minor": row["price_minor"], "reference_minor": row["strike_minor"], "available": True,
                            "price_basis": "amazon_search_listing", "searched": True}
            return {"status": "not_checked", "title": pp["title"], "image_url": pp.get("image_url"), "searched": True,
                    "error": "Amazon hid the price from our server; it's read at the next collection"}
        return {"status": "not_checked", "title": pp["title"], "image_url": pp.get("image_url"),
                "error": "Amazon hid the price from our server; it's read at the next collection"}
    unavailable = bool(pp["availability"] and re.search(r"unavailable|out of stock", pp["availability"], re.I))
    return {"status": "unavailable" if unavailable else "checked", "title": pp["title"], "image_url": pp.get("image_url"),
            "price_minor": pp["price_minor"], "reference_minor": pp["reference_minor"], "available": not unavailable}


CHECKERS = {"Expert": check_expert, "Powercity": check_powercity, "Mobile123": check_mobile123}
_lock = __import__("threading").Lock()
_last = {}


def check_one(item, amazon_search=False):
    """One product right now (used when a member adds it). Requests to the same shop are spaced by its gap,
    even when several members add products at once."""
    site = item["site"]
    with _lock:
        wait = GAP.get(site, 3.0) - (time.monotonic() - _last.get(site, -1e9))
        _last[site] = time.monotonic() + max(0, wait)
    if wait > 0:
        time.sleep(wait)
    try:
        if site == "Harvey Norman":
            r = check_harvey_norman([item]).get(item["key"], {"status": "error", "error": "not checked"})
        elif site == "Amazon.ie":
            r = check_amazon_page(item, allow_search=amazon_search)
        else:
            r = CHECKERS[site](item)
    except Exception as exc:
        r = {"status": "error", "error": str(exc)[:150]}
    r.setdefault("discount_percent", pct(r.get("price_minor"), r.get("reference_minor")))
    buy = item["url"]
    if site == "Amazon.ie":
        tag = json.loads((ROOT.parent / "amazon-offers-service/config.json").read_text())["associate_tag"]
        buy = f"https://www.amazon.ie/dp/{item['key']}?tag={tag}"
    return {"site": site, "key": item["key"], "buy_url": buy, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **r}


def run(items):
    results, last = [], {}
    hn_items = [i for i in items if i["site"] == "Harvey Norman"]
    hn_out = check_harvey_norman(hn_items) if hn_items else {}
    for i in items:
        if i["site"] == "Harvey Norman":
            r = hn_out.get(i["key"], {"status": "error", "error": "not checked"})
        elif i["site"] in CHECKERS:
            wait = GAP[i["site"]] - (time.monotonic() - last.get(i["site"], 0))
            if wait > 0:
                time.sleep(wait)
            try:
                r = CHECKERS[i["site"]](i)
            except Exception as exc:
                r = {"status": "error", "error": str(exc)[:150]}
            last[i["site"]] = time.monotonic()
        else:
            continue
        r.setdefault("discount_percent", pct(r.get("price_minor"), r.get("reference_minor")))
        results.append({"site": i["site"], "key": i["key"], "buy_url": i["url"], "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **r})
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--items", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache")  # accepted for the collection job's standard arguments; not used
    args = ap.parse_args()
    items = json.loads(Path(args.items).read_text()) if Path(args.items).exists() else []
    started = time.monotonic()
    results = run([i for i in items if i.get("site") != "Amazon.ie"])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "status.json").write_text(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "items": results},
                                                ensure_ascii=False, indent=1) + "\n")
    report = {"checked": len(results), "errors": [f"{r['site']} {r['key']}: {r.get('error')}" for r in results if r["status"] == "error"],
              "duration_seconds": round(time.monotonic() - started, 1)}
    (out / "run-report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report))
    return 2 if report["errors"] and len(report["errors"]) == len(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
