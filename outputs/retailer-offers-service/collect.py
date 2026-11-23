#!/usr/bin/env python3
"""Official IE retailer observations and review candidates. No LLM/API keys required."""
import argparse
import csv
import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent
PROVIDERS = {"powercity":"Powercity", "currys":"Currys", "harvey_norman":"Harvey Norman", "expert":"Expert Ireland"}
HOSTS = {"powercity.ie", "www.powercity.ie", "www.currys.ie", "www.harveynorman.ie", "www.expert.ie"}
# Paths each site's robots.txt disallows for all user agents (checked 2026-11-22). Never requested.
ROBOTS_DISALLOWED = {"www.expert.ie": ("/account.php", "/cart.php", "/checkout.php", "/search.php", "/blackfriday/",
    "/summer-sale/", "/autumn-sale/", "/remote.php", "/wishlist.php", "/admin/")}
NOT_NEW = re.compile(r"ex[ -]?display|displays? only|refurb|renewed|pre[ -]?owned|open[ -]?box|used condition|zexdisplay|grade [abc]\b", re.I)
HISTORY_DAYS = 90


def money(text):
    return int((Decimal(str(text).replace("€", "").replace(",", "").strip()) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def discount(price, reference):
    if reference is None:
        return None, None
    if reference <= price or price < 0:
        raise ValueError("Invalid price comparison")
    saving = reference - price
    return saving, float((Decimal(saving) * 100 / reference).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def checked_url(url):
    p = urlparse(url)
    if p.scheme != "https" or p.hostname not in HOSTS:
        raise ValueError("Not an approved public retailer URL")
    if p.path.startswith(ROBOTS_DISALLOWED.get(p.hostname, ())) or "_bc_fsnf" in p.query:
        raise ValueError("Path disallowed by the retailer's robots.txt")
    return url


def fetch(source, cache, offline, page_id=None, url=None):
    sid = page_id or source["id"]
    source = {**source, "url":url or source["url"]}
    path, meta_path = cache / (sid + ".html"), cache / (sid + ".json")
    if offline:
        meta = json.loads(meta_path.read_text())
        if meta["fetch_status"] == "ok":
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != meta["sha256"]:
                raise ValueError("Evidence hash mismatch: " + sid)
            return raw, {**meta, "replayed":True}
        return None, {**meta, "replayed":True}
    checked_url(source["url"])
    tmp = cache / (sid + ".tmp")
    result = subprocess.run(["curl", "--silent", "--show-error", "--location", "--max-redirs", "3",
        "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "10", "--max-time", "35",
        "--max-filesize", "12000000", "--output", str(tmp), "--write-out", "%{http_code}\n%{url_effective}", source["url"]],
        capture_output=True, text=True, timeout=40)
    parts = result.stdout.splitlines()
    code = int(parts[0]) if parts and parts[0].isdigit() else 0
    meta = {"id":sid, "provider_id":source["provider_id"], "url":source["url"],
        "checked_at":datetime.now(timezone.utc).isoformat(), "http_status":code,
        "fetch_status":"ok" if result.returncode == 0 and code == 200 else "blocked" if code in (401,403,429) else "failed"}
    if meta["fetch_status"] == "ok":
        checked_url(parts[1])
        raw = tmp.read_bytes()
        if not raw or b"<html" not in raw.lower():
            raise ValueError("Unexpected non-HTML response")
        tmp.replace(path)
        meta.update(fetched_at=meta["checked_at"], sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw))
    else:
        raw = None
        tmp.unlink(missing_ok=True)
        meta["error"] = (result.stderr.strip() or "HTTP " + str(code))[:250]
    write_json(meta_path, meta)
    return raw, meta


def observation(provider, sku, name, category, price, reference, reference_type, url, observed_at, source_id, method):
    saving, percent = discount(price, reference)
    return {"id":"ie-retail-" + provider + "-" + sku.lower(), "module_id":"retailer_promotions",
        "provider_id":provider, "provider_name":PROVIDERS[provider], "country":"IE", "currency":"EUR",
        "offer_kind":"handset_discount" if category == "phones" and reference else "product_discount" if reference else "price_observation",
        "product":{"sku":sku, "name":name, "category":category, "condition":"unconfirmed", "condition_basis":None,
                   "canonical_product_id":None},
        "pricing":{"device_price_minor":price, "reference_price_minor":reference, "reference_type":reference_type,
            "reference_period":None, "discount_minor":saving, "discount_percent":percent,
            "includes_delivery":False, "delivery_cost_minor":None, "cashback_minor":None},
        "eligibility":{"conditions":[], "coupon_code":None},
        "validity":{"starts_on":None, "ends_on":None, "timezone":"Europe/Dublin"},
        "stock_status":"unknown", "status":"needs_review" if reference else "price_only",
        "verification":{"method":method, "observed_at":observed_at, "origin_fetched_at":observed_at if method == "direct_official_html" else None,
            "fresh_until":None, "issues":[]},
        "source_ids":[source_id], "links":{"landing_url":checked_url(url), "affiliate_url":None},
        "media":{"image_url":None, "publish_image":False},
        "distribution":{"can_list":False, "notification_ready":False}}


def parse_powercity(raw, source, meta):
    soup = BeautifulSoup(raw, "html.parser")
    cards = soup.select("#product-list > .group.product")
    if not cards:
        raise ValueError("Product-card structure missing; adapter needs review")
    found, rejected = [], []
    for card in cards:
        name_node = card.select_one(".productname.desktop-only")
        link = name_node.select_one('a[href^="/product/"]') if name_node else None
        if not link:
            continue
        sku = link["href"].removeprefix("/product/")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", sku):
            continue
        title = link.get_text(" ", strip=True)
        text = name_node.get_text(" ", strip=True)
        image_markers = " ".join(str(i.get("src", "")) for i in card.select("img"))
        if NOT_NEW.search(text + " " + image_markers):
            rejected.append({"provider_id":"powercity", "sku":sku, "name":title, "reason":"not_new_or_display_stock"})
            continue
        stock = re.search(r"Stock\s+(\d+)\s+Available", text, re.I)
        if not stock or int(stock[1]) <= 0:
            rejected.append({"provider_id":"powercity", "sku":sku, "name":title, "reason":"no_confirmed_new_retail_stock"})
            continue
        price_node = card.select_one(".productprice > div")
        match = re.fullmatch(r"€\s*([\d,]+(?:\.\d{1,2})?)", price_node.get_text(" ", strip=True)) if price_node else None
        if not match:
            rejected.append({"provider_id":"powercity", "sku":sku, "name":title, "reason":"ambiguous_price"})
            continue
        row = observation("powercity", sku, title, source["category"], money(match[1]), None, None,
            urljoin(source["url"], link["href"]), meta["fetched_at"], source["id"], "direct_official_html")
        row["product"].update(condition="new_inferred", condition_basis="standard retail category, explicit available stock, display/refurb markers absent", title_truncated=title.endswith("..."))
        row["stock_status"] = "in_stock"
        img = card.select_one(".productimage a img[src]")
        if img and "/productimages/" in img["src"]:
            row["media"]["image_url"] = urljoin("https://powercity.ie/", img["src"])
        row["verification"]["issues"] = ["no_verified_reference_price;discount_not_computed", "delivery_cost_not_checked", "new_condition_inferred_from_catalogue"]
        row["verification"]["fresh_until"] = (datetime.fromisoformat(meta["fetched_at"]) + timedelta(hours=24)).isoformat()
        promos = name_node.select_one(".promotions")
        if promos and promos.get_text(" ", strip=True):
            row["eligibility"]["conditions"].append(promos.get_text(" ", strip=True))
            row["verification"]["issues"].append("promotion_terms_not_verified;not_added_to_savings")
        found.append(row)
    return found, rejected, len(cards)


def _text(node):
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)) if node else ""


def _price(node):
    match = re.fullmatch(r"€\s*([\d,]+(?:\.\d{1,2})?)", _text(node))
    return money(match[1]) if match else None


def parse_expert(raw, source, meta):
    """Expert.ie category cards (BigCommerce theme). Returns rows, rejected cards, cards seen, next-page URL."""
    soup = BeautifulSoup(raw, "html.parser")
    cards = soup.select("li.product article.card")
    if not cards and not soup.select(".productGrid, .page-content"):
        raise ValueError("Product-card structure missing; adapter needs review")
    found, rejected = [], []
    for card in cards:
        link = card.select_one(".card-title a[href]")
        if not link:
            continue
        url = link["href"]
        try:
            checked_url(url)
        except ValueError:
            continue
        sku = urlparse(url).path.strip("/").split("/")[-1]
        if not re.fullmatch(r"[a-z0-9-]{3,120}", sku):
            continue
        title = _text(link)
        base = {"provider_id":"expert", "sku":sku, "name":title}
        if NOT_NEW.search(title):
            rejected.append({**base, "reason":"not_new_or_display_stock"})
            continue
        button = _text(card.select_one(".card-figcaption-button")).lower()
        if button != "add to cart":
            rejected.append({**base, "reason":"not_available_to_order:" + (button or "no_button")})
            continue
        price = _price(card.select_one("[data-product-price-with-tax]"))
        if price is None:
            rejected.append({**base, "reason":"ambiguous_price"})
            continue
        was = _price(card.select_one("[data-product-non-sale-price-with-tax]"))
        rrp = _price(card.select_one("[data-product-rrp-with-tax]"))
        issues = ["delivery_cost_not_checked", "new_condition_inferred_from_catalogue", "stock_level_not_published;add_to_cart_only"]
        reference, reference_type = None, None
        if was is not None and was > price:
            reference, reference_type = was, "retailer_was_price"
        elif rrp is not None and rrp > price:
            reference, reference_type = rrp, "manufacturer_rrp"
        elif was is not None or rrp is not None:
            issues.append("reference_not_above_current_price;discount_not_computed")
        if reference is None:
            issues.append("no_verified_reference_price;discount_not_computed")
        else:
            issues.append("was_price_30_day_basis_not_verified")
        row = observation("expert", sku, title, source["category"], price, reference, reference_type,
            url, meta["fetched_at"], source["id"], "direct_official_html")
        row["product"].update(condition="new_inferred", condition_basis="standard retail category, Add to Cart offered, display/refurb markers absent",
            model_code=title.rsplit("|", 1)[1].strip() if "|" in title else None, brand=_text(card.select_one(".card-text--brand")) or None)
        row["stock_status"] = "available_to_order"
        img = card.select_one("img.card-image[src]")
        if img and img["src"].startswith("https://"):
            row["media"]["image_url"] = img["src"]
        row["verification"]["issues"] = issues
        row["verification"]["fresh_until"] = (datetime.fromisoformat(meta["fetched_at"]) + timedelta(hours=24)).isoformat()
        found.append(row)
    nxt = soup.select_one('link[rel="next"]') or soup.select_one(".pagination-item--next a[href]")
    next_url = urljoin(source["url"], nxt["href"]) if nxt and nxt.get("href") else None
    return found, rejected, len(cards), next_url


def update_history(history, offers, now):
    """Keep one direct observation per item per day (last price seen) for HISTORY_DAYS; snapshots never enter it."""
    cutoff = (now - timedelta(days=HISTORY_DAYS)).date().isoformat()
    for o in offers:
        if o["verification"]["method"] != "direct_official_html" or o["status"] in ("unconfirmed", "stale"):
            continue
        day = o["verification"]["observed_at"][:10]
        points = [p for p in history.get(o["id"], []) if p["date"] != day]
        points.append({"date":day, "price_minor":o["pricing"]["device_price_minor"]})
        history[o["id"]] = sorted(points, key=lambda p:p["date"])
    for oid in list(history):
        history[oid] = [p for p in history[oid] if p["date"] >= cutoff]
        if not history[oid]:
            del history[oid]
    return history


def apply_history(offers, history, now):
    """Attach what this collector has itself observed. It never replaces the retailer's reference price."""
    since = (now - timedelta(days=30)).date().isoformat()
    for o in offers:
        points = history.get(o["id"])
        if not points or o["verification"]["method"] != "direct_official_html":
            continue
        today = o["verification"]["observed_at"][:10] if o["verification"]["observed_at"] else None
        prior = [p["price_minor"] for p in points if since <= p["date"] < (today or "9999")]
        o["pricing"]["observed_history"] = {"first_observed_on":points[0]["date"], "days_observed":len(points),
            "prior_30d_low_minor":min(prior) if prior else None, "prior_30d_days_observed":len(prior)}
        # No Was price from the shop (Powercity never shows one): a real price drop that this collector saw itself
        # counts as a discount. The reference is the highest price seen on at least two separate days in the previous
        # 30 days, so a one-day blip can't make a fake "was" price. It is labelled as our own observation.
        if o["pricing"]["reference_price_minor"] is None and prior:
            counts = {}
            for p in points:
                if since <= p["date"] < (today or "9999"):
                    counts[p["price_minor"]] = counts.get(p["price_minor"], 0) + 1
            held = [price for price, n in counts.items() if n >= 2]
            now_price = o["pricing"]["device_price_minor"]
            if held and max(held) > now_price:
                ref = max(held)
                seen = [p["date"] for p in points if p["price_minor"] == ref and since <= p["date"] < (today or "9999")]
                saving, percent = discount(now_price, ref)
                o["pricing"].update(reference_price_minor=ref, reference_type="observed_price_drop", discount_minor=saving,
                                    discount_percent=percent,
                                    reference_period={"starts_on": min(seen), "ends_on": max(seen)})
                o["offer_kind"] = "handset_discount" if o["product"]["category"] == "phones" else "product_discount"
                o["verification"]["issues"].append("reference_is_price_observed_by_dealradar_not_a_shop_was_price")
        # An ongoing sale (same price on earlier days) is fine. Only a strictly lower price seen in the previous
        # 30 days means today's "discount" is worse than what this collector already saw.
        if prior and o["pricing"]["reference_price_minor"] is not None and min(prior) < o["pricing"]["device_price_minor"]:
            o["verification"]["issues"].append("observed_lower_price_in_prior_30d;was_price_claim_needs_review")
    return offers


def import_snapshots(data):
    result = []
    for p in data["products"]:
        row = observation(p["provider_id"], p["sku"], p["name"], p["category"], p["current_price_minor"], p["reference_price_minor"],
            p["reference_type"], p["url"], None, "reference-snapshots", data["method"])
        row["verification"].update(retrieved_on=data["retrieved_on"], origin_fetched_at=data["origin_fetched_at"],
            evidence=p["evidence"], issues=["web_tool_snapshot;exact_origin_fetch_time_unknown", "live_stock_and_new_condition_need_review", "not_automatically_refreshable"])
        row["pricing"]["reference_period"] = p.get("reference_period")
        result.append(row)
    return result


def reconcile(current, previous, now):
    old = {o["id"]:o for o in previous}
    rows, events = {}, []
    for o in current:
        existing = rows.get(o["id"])
        if existing:
            if existing["pricing"] != o["pricing"]:
                existing["status"] = "needs_review"
                existing["verification"]["issues"].append("conflicting_category_prices")
            existing["source_ids"] = sorted(set(existing["source_ids"] + o["source_ids"]))
            continue
        if o["verification"]["fresh_until"] and datetime.fromisoformat(o["verification"]["fresh_until"]) < now:
            o["status"] = "stale"
        rows[o["id"]] = o
        was = old.get(o["id"])
        # Only direct observations are a price history. Snapshots cannot generate alerts.
        if was and o["verification"]["method"] == "direct_official_html" and was["verification"]["method"] == "direct_official_html":
            before, after = was["pricing"]["device_price_minor"], o["pricing"]["device_price_minor"]
            if before != after:
                events.append({"offer_id":o["id"], "event":"observed_price_decrease" if after < before else "observed_price_increase",
                    "old_price_minor":before, "new_price_minor":after, "old_observed_at":was["verification"]["observed_at"],
                    "new_observed_at":o["verification"]["observed_at"], "send_notification":False})
    for oid, was in old.items():
        if oid not in rows:
            was["status"] = "unconfirmed"
            was["distribution"].update(can_list=False, notification_ready=False)
            was["verification"]["issues"] = sorted(set(was["verification"]["issues"] + ["not_observed_this_run"]))
            rows[oid] = was
    return sorted(rows.values(), key=lambda o:(-(o["pricing"]["discount_percent"] or 0),o["provider_id"],o["id"])), events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--out", default=str(ROOT / "data"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--as-of", help="Timezone-aware ISO timestamp; primarily for replay")
    args = parser.parse_args()
    started = time.monotonic()
    now = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if now.tzinfo is None:
        parser.error("--as-of must include a timezone offset")
    cache, out = Path(args.cache), Path(args.out)
    cache.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "sources.json").read_text())
    snapshots = json.loads((ROOT / "reference-snapshots.json").read_text())
    previous = json.loads((out / "offers.json").read_text())["offers"] if (out / "offers.json").exists() else []
    history_path = out / "price-history.json"
    history = json.loads(history_path.read_text())["items"] if history_path.exists() else {}
    sources, rows, rejected = [], [], []
    last_request = {}
    for source in config["sources"]:
        host = urlparse(source["url"]).hostname
        delay = float(source.get("request_delay_seconds", 0.7))
        url, page, pages_meta = source["url"], 1, []
        while url and page <= int(source.get("max_pages", 1)):
            page_id = source["id"] if page == 1 else source["id"] + "-p" + str(page)
            if args.offline and page > 1 and not (cache / (page_id + ".json")).exists():
                break
            if not args.offline and host in last_request:
                time.sleep(max(0, delay - (time.monotonic() - last_request[host])))
            next_url = None
            try:
                raw, meta = fetch(source, cache, args.offline, page_id, url)
                if raw and source["adapter"] == "powercity_cards":
                    parsed, excluded, seen = parse_powercity(raw, source, meta)
                elif raw and source["adapter"] == "expert_cards":
                    parsed, excluded, seen, next_url = parse_expert(raw, source, meta)
                if raw and source["adapter"] in ("powercity_cards", "expert_cards"):
                    rows.extend(parsed)
                    rejected.extend(excluded)
                    meta.update(adapter_status="ok", cards_seen=seen, observations=len(parsed))
                elif raw:
                    meta["adapter_status"] = "not_implemented;requires_verified_page_structure"
                else:
                    meta["adapter_status"] = "unavailable"
            except Exception as exc:
                meta = {"id":page_id, "provider_id":source["provider_id"], "url":url,
                    "fetch_status":"failed", "adapter_status":"failed", "error":str(exc)[:250]}
            last_request[host] = time.monotonic()
            meta["page"] = page
            pages_meta.append(meta)
            print(page_id + ": " + meta["fetch_status"], flush=True)
            if meta["adapter_status"] != "ok" or not meta.get("cards_seen"):
                break
            url, page = next_url, page + 1
        sources.extend(pages_meta)
    rows.extend(import_snapshots(snapshots))
    offers, changes = reconcile(rows, previous, now)
    if not args.offline:
        history = update_history(history, offers, now)
    apply_history(offers, history, now)
    result = {"schema_version":"1.0.0", "module_id":config["module_id"], "module_name":config["module_name"],
        "generated_at":now.isoformat(), "scope":{"country":"IE", "device_condition":"new_only;unconfirmed items held for review", "full_catalogue":False},
        "offers":offers, "sources":sources}
    schema = json.loads((ROOT / "offers.schema.json").read_text())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(result)
    assert len({o["id"] for o in offers}) == len(offers)
    live_discounts = [o for o in offers if o["pricing"]["discount_percent"] is not None
        and o["verification"]["method"] == "direct_official_html" and o["status"] == "needs_review"]
    counts = {"total_records":len(offers), "reference_candidates":sum(o["pricing"]["reference_price_minor"] is not None for o in offers),
        "live_discount_candidates":len(live_discounts),
        "by_provider":{p:sum(o["provider_id"] == p for o in offers) for p in PROVIDERS if any(o["provider_id"] == p for o in offers)},
        "price_observations":sum(o["offer_kind"] == "price_observation" for o in offers), "listable_discounts":sum(o["distribution"]["can_list"] for o in offers),
        "excluded_cards":len(rejected), "history_items":len(history)}
    outputs = {"offers.json":result, "changes.json":{"events":changes}, "excluded.json":{"records":rejected},
        "run-report.json":{"generated_at":now.isoformat(), "duration_seconds":round(time.monotonic()-started,2), "offline":args.offline,
            "counts":counts, "sources":sources, "cloud_ready":False, "llm_calls":0, "llm_tokens":0,
            "blockers":["Currys and Harvey Norman direct fetch returned 403 (Cloudflare bot protection) in this environment; not bypassed.",
                "DID Electrical rate-limits automated reads (429 local_rate_limited); not used.",
                "Powercity category prices do not supply a verified Was/RRP price; only this collector's own price history is available.",
                "Expert Was/RRP prices are the retailer's own claims; the 30-day lowest-price basis is not verified until enough history exists.",
                "Reference snapshots cannot refresh automatically or trigger notifications."]}}
    if not args.offline:
        outputs["price-history.json"] = {"note":"This collector's own direct observations: one price per item per day, kept "
            + str(HISTORY_DAYS) + " days. Not a retailer price history.", "updated_at":now.isoformat(), "items":history}
    for name, payload in outputs.items():
        write_json(out / name, payload)
    with (out / "offers.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id","provider","product","category","price_eur","reference_eur","reference_type","discount_percent","status","can_list","url"])
        for o in offers:
            p = o["pricing"]
            writer.writerow([o["id"],o["provider_name"],o["product"]["name"],o["product"]["category"],f'{p["device_price_minor"]/100:.2f}',
                "" if p["reference_price_minor"] is None else f'{p["reference_price_minor"]/100:.2f}',p["reference_type"],p["discount_percent"],o["status"],o["distribution"]["can_list"],o["links"]["landing_url"]])
    print(json.dumps(counts))
    return 2 if any(s.get("adapter_status") != "ok" for s in sources) else 0


if __name__ == "__main__":
    raise SystemExit(main())
