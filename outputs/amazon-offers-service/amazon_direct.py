#!/usr/bin/env python3
"""Amazon.ie direct collection: keyword search, then confirm each candidate on its Amazon product page.

Plain HTTPS requests (curl), spaced out, no JavaScript, no login, no basket, no proxies and no browser
disguise. If Amazon answers with a captcha, 503 or 429 the Amazon run stops; it is not retried or worked
around. Prices shown come from the Amazon product page at the recorded time.

Owner decision (2026-11-23): collect from Amazon.ie directly without the Creators API. Amazon's Conditions
of Use and the Associates Operating Agreement restrict automated access and price display; the owner
accepted that risk for the mobile123ie-21 account.

  python amazon_direct.py --cache DIR --out DIR [--leads web-data/offers.json]
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
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from collect import ROOT, affiliate_link, write_json

HOST = "https://www.amazon.ie"
NOT_NEW = re.compile(r"\b(renewed|refurbished|reconditioned|pre-?owned|used)\b", re.I)


class Blocked(Exception):
    pass


def price(text):
    m = re.search(r"€\s*([\d.,]+)", text or "")
    if not m:
        return None
    return int((Decimal(m[1].replace(",", "")) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def exact_discount(now, ref):
    if now is None or ref is None or ref <= now:
        return None, None
    saving = ref - now
    return saving, float((Decimal(saving) * 100 / ref).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class Fetcher:
    def __init__(self, cache, interval):
        self.cache, self.interval, self.last, self.count = Path(cache), interval, 0.0, 0
        self.cache.mkdir(parents=True, exist_ok=True)

    def get(self, url, key):
        wait = self.interval - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        path = self.cache / (key + ".html")
        p = subprocess.run(["curl", "-s", "-L", "--compressed", "--proto", "=https", "--max-time", "25",
                            "-o", str(path), "-w", "%{http_code}", url], capture_output=True, text=True, timeout=40)
        self.last, self.count = time.monotonic(), self.count + 1
        code = int(p.stdout) if p.stdout.isdigit() else 0
        raw = path.read_bytes().decode("utf-8", "replace") if path.exists() else ""
        if code in (429, 503) or "captcha" in raw.lower()[:200000] or (code == 200 and len(raw) < 5000):
            raise Blocked(f"HTTP {code} at {url}")
        if code != 200:
            raise RuntimeError(f"HTTP {code} at {url}")
        return raw, hashlib.sha256(raw.encode()).hexdigest()


def parse_search(raw):
    soup = BeautifulSoup(raw, "html.parser")
    rows = []
    for it in soup.select('div[data-component-type="s-search-result"][data-asin]'):
        asin = it.get("data-asin", "")
        if not re.fullmatch(r"[A-Z0-9]{10}", asin):
            continue
        titles = [t.get_text(" ", strip=True) for t in it.select('[data-cy="title-recipe"], h2')]
        title = max(titles, key=len) if titles else ""
        now = it.select_one(".a-price:not(.a-text-price) .a-offscreen")
        was = it.select_one(".a-price.a-text-price .a-offscreen")
        rows.append({"asin": asin, "title": title, "price_minor": price(now.get_text() if now else None),
                     "strike_minor": price(was.get_text() if was else None),
                     "sponsored": bool(it.select_one(".puis-sponsored-label-text, .s-sponsored-label-info-icon"))})
    nxt = soup.select_one("a.s-pagination-next")
    return rows, bool(nxt)


def parse_product(raw):
    s = BeautifulSoup(raw, "html.parser")

    def text(sel):
        n = s.select_one(sel)
        return n.get_text(" ", strip=True) if n else None

    now = text("#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen") or text("#corePrice_feature_div .a-offscreen") \
        or text(".apexPriceToPay .a-offscreen")
    basis_node = s.select_one("#corePriceDisplay_desktop_feature_div .basisPrice") or s.select_one(".basisPrice")
    basis = basis_node.select_one(".a-offscreen").get_text(strip=True) if basis_node and basis_node.select_one(".a-offscreen") else None
    label = ""
    if basis_node:
        label = re.sub(r"\s+", " ", basis_node.get_text(" ", strip=True).replace(basis or "", "")).strip(" :")
    return {"title": text("#productTitle"), "price_minor": price(now), "reference_minor": price(basis),
            "reference_label": label[:40] or None, "seller": text("#merchantInfoFeature_feature_div .offer-display-feature-text-message"),
            "availability": (text("#availability") or "")[:80], "price_hidden": "add this item to your basket" in raw}


def reference_type(label):
    l = (label or "").lower()
    if "rrp" in l or "list" in l:
        return "amazon_rrp"
    if "was" in l:
        return "amazon_was_price"
    if "median" in l:
        return "amazon_median_price"
    return "amazon_reference_unlabelled"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--leads", help="Optional third-party candidates (web_collect offers.json) to confirm as well")
    ap.add_argument("--previous", help="Previous amazon_direct offers.json: re-checked first, and kept until expiry if not reached")
    args = ap.parse_args()
    started, now = time.monotonic(), datetime.now(timezone.utc)
    c = json.loads((ROOT / "config.json").read_text())
    d = c.get("direct", {})
    max_searches = int(d.get("max_searches_per_run", 6))
    search_gap, page_gap = float(d.get("search_interval_seconds", 10)), float(d.get("request_interval_seconds", 3))
    max_checks, threshold, tag = int(d.get("max_product_checks", 150)), float(c["minimum_discount_percent"]), c["associate_tag"]
    valid_hours = float(d.get("price_valid_hours", 36))
    previous = []
    if args.previous and Path(args.previous).exists():
        previous = [o for o in json.loads(Path(args.previous).read_text()).get("offers", [])
                    if o.get("verification", {}).get("method") == "amazon_product_page"]
    fetch = Fetcher(args.cache, page_gap)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cursor_file = out / "search-cursor.json"
    cursor = json.loads(cursor_file.read_text()).get("next", 0) if cursor_file.exists() else 0
    report = {"generated_at": now.isoformat(), "mode": "amazon_direct", "searches": [], "blocked": None,
              "search_blocked": None, "errors": []}
    offers, rejected, checked = [], [], set()

    def confirm(h):
        """Read the Amazon product page for one candidate; keep it only if the page itself shows > threshold."""
        if h["asin"] in checked or len(checked) >= max_checks:
            return
        checked.add(h["asin"])
        try:
            raw, sha = fetch.get(f"{HOST}/dp/{h['asin']}", "dp-" + h["asin"])
        except RuntimeError as exc:
            checked.discard(h["asin"])  # a network error is not a verdict; a previous deal is carried over
            rejected.append({"asin": h["asin"], "reason": "product_page_error", "detail": str(exc)})
            return
        observed = datetime.now(timezone.utc)
        p = parse_product(raw)
        saving, pct = exact_discount(p["price_minor"], p["reference_minor"])
        title = p["title"] or h["title"]
        reason = None
        if p["price_minor"] is None:
            reason = "price_hidden_in_basket" if p["price_hidden"] else "no_price_on_product_page"
        elif pct is None or pct <= threshold:
            reason = "discount_not_above_threshold_on_product_page"
        elif NOT_NEW.search(title or ""):
            reason = "not_new"
        elif p["availability"] and re.search(r"unavailable|out of stock", p["availability"], re.I):
            reason = "unavailable"
        if reason == "price_hidden_in_basket":
            checked.discard(h["asin"])  # hidden from this network is not a verdict; an earlier confirmed deal is carried over
        if reason:
            rejected.append({"asin": h["asin"], "title": title, "reason": reason, "price_minor": p["price_minor"],
                             "reference_minor": p["reference_minor"], "discovered_via": h["origin"]})
            return
        offers.append({
            "id": "ie-amazon-" + h["asin"], "module_id": "amazon_promotions", "provider_id": "amazon_ie", "provider_name": "Amazon.ie",
            "country": "IE", "currency": "EUR", "offer_kind": "product_discount",
            "product": {"asin": h["asin"], "name": title, "condition": "new_inferred", "seller": p["seller"]},
            "pricing": {"device_price_minor": p["price_minor"], "reference_price_minor": p["reference_minor"],
                        "reference_type": reference_type(p["reference_label"]), "reference_label": p["reference_label"],
                        "discount_minor": saving, "discount_percent": pct, "includes_delivery": False, "delivery_cost_minor": None},
            "eligibility": {"prime_required": None, "coupon_required": None, "conditions": []},
            "stock_status": "in_stock" if "in stock" in p["availability"].lower() else "unknown",
            "availability_text": p["availability"], "status": "active", "matched_keyword_ids": sorted(h["keywords"]),
            "discovered_via": h["origin"], "validity": {"starts_on": None, "ends_on": None},
            "verification": {"method": "amazon_product_page", "verified_at": observed.isoformat(),
                             "fresh_until": (observed + timedelta(hours=valid_hours)).isoformat(), "page_sha256": sha,
                             "issues": ["price_from_amazon_page_at_verified_at;may_change"]},
            "links": {"landing_url": affiliate_link(h["asin"], tag), "affiliate_url": affiliate_link(h["asin"], tag), "tag": tag},
            "media": {"image_url": None, "publish_image": False},
            "distribution": {"can_list": True, "notification_ready": False}})

    keywords = [k for k in c["keywords"] if k.get("enabled", True)]
    try:
        # 0. Re-check every Amazon deal confirmed before, so earlier finds stay current.
        for o in previous:
            confirm({"asin": o["product"]["asin"], "title": o["product"]["name"],
                     "keywords": set(o.get("matched_keyword_ids") or []), "origin": o.get("discovered_via", "previous")})
        # 1. Third-party leads: confirm each on its Amazon product page.
        if args.leads and Path(args.leads).exists():
            for o in json.loads(Path(args.leads).read_text()).get("offers", []):
                confirm({"asin": o["product"]["asin"], "title": o["product"]["name"],
                         "keywords": set(o.get("matched_keyword_ids") or []), "origin": "third_party_lead"})
        # 2. A few Amazon searches per run, rotating through the keywords; then confirm their hits.
        hits = {}
        for i in range(min(max_searches, len(keywords))):
            kw = keywords[(cursor + i) % len(keywords)]
            fetch.interval = search_gap
            try:
                raw, _ = fetch.get(f"{HOST}/s?k={quote_plus(kw['query'])}", "search-" + kw["id"])
            except Blocked as exc:
                report["search_blocked"] = str(exc)
                break
            except RuntimeError as exc:
                report["errors"].append(str(exc))
                continue
            finally:
                fetch.interval = page_gap
            rows, _ = parse_search(raw)
            qualifying = 0
            for r in rows:
                _, pct = exact_discount(r["price_minor"], r["strike_minor"])
                if pct is not None and pct > threshold and not NOT_NEW.search(r["title"]):
                    qualifying += 1
                    hits.setdefault(r["asin"], {**r, "keywords": set(), "origin": "amazon_search"})["keywords"].add(kw["id"])
            report["searches"].append({"keyword_id": kw["id"], "results": len(rows), "qualifying": qualifying})
        cursor = (cursor + len(report["searches"])) % max(1, len(keywords))
        for h in hits.values():
            confirm(h)
    except Blocked as exc:
        report["blocked"] = str(exc)
    write_json(cursor_file, {"next": cursor, "keywords": len(keywords)})
    # Deals not reached this run (cut short) stay until their own expiry, with their original check time.
    carried = 0
    for o in previous:
        if o["product"]["asin"] not in checked and datetime.fromisoformat(o["verification"]["fresh_until"]) > now:
            o["verification"]["issues"] = sorted(set(o["verification"].get("issues", []) + ["not_rechecked_this_run"]))
            offers.append(o)
            carried += 1
    report["carried_over"] = carried
    offers.sort(key=lambda o: -o["pricing"]["discount_percent"])
    report.update(duration_seconds=round(time.monotonic() - started, 1), requests=fetch.count, candidates=len(checked),
                  confirmed=len(offers) - carried, rejected=len(rejected))
    write_json(out / "offers.json", {"schema_version": "1.0.0", "module_id": "amazon_promotions", "generated_at": now.isoformat(),
                                     "source": "amazon_direct", "offers": offers})
    write_json(out / "rejected.json", {"records": rejected})
    write_json(out / "run-report.json", report)
    print(json.dumps({k: report[k] for k in ("duration_seconds", "requests", "candidates", "confirmed", "carried_over", "rejected", "blocked", "search_blocked")}))
    return 2 if report["blocked"] or report["search_blocked"] or report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
