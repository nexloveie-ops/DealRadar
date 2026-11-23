#!/usr/bin/env python3
"""Harvey Norman Ireland discounts through the store's own UCP catalogue interface (https://ucp.dev).

harveynorman.ie blocks ordinary automated page requests (Cloudflare). The store (on Shopify) also publishes an
official interface for shopping agents at /.well-known/ucp. We call only its read-only `search_catalog` tool,
announcing our public agent profile (DealRadar /ucp/profile.json, catalogue search and lookup only).
No cart, checkout or order tools are ever called.

Each variant's discount is its `price` against the store's own `list_price` (the crossed-out "was" price).

  python harvey_norman.py --out DIR [--config harvey-norman.json] [--profile URL] [--offline]
"""
import argparse
import hashlib
import json
import os
import re
import time
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENDPOINT = "https://messagepool-hnie-prod.myshopify.com/api/ucp/mcp"
PROFILE = "https://dealradar-web-815864259513.europe-west1.run.app/ucp/profile.json"
STORE = "https://www.harveynorman.ie"
# Search results mix in other kinds of product, so the title decides the category where it clearly can;
# otherwise the search's own category is used. Large appliances and furniture are not tech deals: dropped.
TITLE_CATEGORY = [
    ("accessories", r"power ?bank|charger|charging (cable|pad|stand)|\bcable\b(?!.*starlink)|screen protector|\bcase\b"),
    ("monitors", r"\bmonitor\b"),
    ("cameras", r"camera|gopro|cctv|doorbell|dashcam"),
    ("wearables", r"smart ?watch|fitbit|fitness tracker|\bwatch\b|smart ring"),
    ("tablets", r"\btab\b|tablet|ipad"),
    ("computers", r"laptop|notebook|chromebook|macbook|omnibook|ideapad|vivobook|\bpc\b|desktop"),
    ("gaming", r"gaming|playstation|\bps5\b|xbox|nintendo|switch 2|controller"),
    ("headphones", r"headphone|earbud|earphone|headset|airpods"),
    ("speakers", r"speaker|soundbar|sound bar"),
    ("phones", r"smartphone|iphone|galaxy [asz]\d|pixel \d|mobile phone"),
    ("storage", r"hard drive|\bssd\b|memory card|usb (flash|stick)|\bnas\b"),
    ("networking", r"router|mesh|wi-?fi extender|starlink|ethernet"),
]
NOT_TECH = re.compile(r"washing machine|washer|dryer|dishwasher|fridge|freezer|\boven\b|\bhob\b|cooker|microwave|kettle|toaster|"
                      r"vacuum|air ?fryer|coffee|mattress|sofa|\bbed\b|chair|\btables?\b|hoover|\biron\b", re.I)


def classify(title, fallback):
    t = title.lower()
    for cat, pattern in TITLE_CATEGORY:
        if re.search(pattern, t):
            return cat
    return fallback


NOT_NEW = re.compile(r"\b(refurb|refurbished|pre-?owned|ex-?display|open box|renewed|second hand|used)\b", re.I)


class UcpError(Exception):
    pass


def rpc(name, arguments, timeout=40):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    p = subprocess.run(["curl", "-s", "--compressed", "--proto", "=https", "--max-time", str(timeout), "-X", "POST", ENDPOINT,
                        "-H", "Content-Type: application/json", "-H", "Accept: application/json, text/event-stream",
                        "-A", "DealRadarBot/1.0 (+https://dealradar-web-815864259513.europe-west1.run.app/)",
                        "--data-binary", "@-", "-w", "\n%{http_code} %{content_type}"],
                       input=body, capture_output=True, text=True, timeout=timeout + 15)
    text, _, status = p.stdout.rpartition("\n")
    code, _, ctype = status.partition(" ")
    if code != "200":
        raise UcpError(f"HTTP {code or 'error'}")
    if "text/event-stream" in ctype:
        text = "\n".join(l[5:].lstrip() for l in text.splitlines() if l.startswith("data:"))
    payload = json.loads(text)
    if payload.get("error"):
        e = payload["error"]
        raise UcpError(f"{e.get('message')}: {(e.get('data') or {}).get('code')}")
    result = payload["result"]
    if result.get("isError"):
        raise UcpError("tool error: " + str(result.get("content"))[:200])
    return json.loads(result["content"][0]["text"])


def search(query, profile, max_pages, page_size, gap):
    """All pages of one catalogue search (bounded). Returns (products, complete)."""
    products, cursor, seen = [], None, set()
    for page in range(max_pages):
        pagination = {"limit": page_size}
        if cursor:
            pagination["cursor"] = cursor
        res = rpc("search_catalog", {"meta": {"ucp-agent": {"profile": profile}},
                                     "catalog": {"query": query, "context": {"address_country": "IE", "currency": "EUR"},
                                                 "pagination": pagination}})
        if (res.get("ucp") or {}).get("status") == "error":
            raise UcpError("catalogue error: " + json.dumps(res.get("messages"))[:200])
        products.extend(res.get("products") or [])
        pg = res.get("pagination") or {}
        if not pg.get("has_next_page"):
            return products, True
        cursor = pg.get("cursor")
        if not cursor or cursor in seen:
            return products, False
        seen.add(cursor)
        time.sleep(gap)
    return products, False


def pct(price, ref):
    if price is None or ref is None or ref <= price or price <= 0:
        return None
    return float((Decimal(ref - price) * 100 / ref).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def offers_from(products, search_cfg, fetched_at, threshold, freshness_hours=24):
    """One offer per product: its in-stock variant with the biggest discount, kept only at >= threshold %."""
    rows, skipped = [], []
    for p in products:
        best = None
        for v in p.get("variants") or []:
            if not (v.get("availability") or {}).get("available"):
                continue
            price, ref = (v.get("price") or {}).get("amount"), (v.get("list_price") or {}).get("amount")
            if (v.get("price") or {}).get("currency", "EUR") != "EUR":
                continue
            d = pct(price, ref)
            if d is not None and (best is None or d > best[2]):
                best = (v, price, d, ref)
        title = p.get("title") or ""
        if best is None:
            skipped.append({"id": p.get("id"), "reason": "no_in_stock_discounted_variant"})
            continue
        v, price, d, ref = best
        if d < threshold:
            skipped.append({"id": p.get("id"), "reason": f"discount_below_{threshold:g}_percent", "discount_percent": d})
            continue
        if NOT_NEW.search(title):
            skipped.append({"id": p.get("id"), "reason": "not_new"})
            continue
        if NOT_TECH.search(title):
            skipped.append({"id": p.get("id"), "reason": "not_a_tech_product"})
            continue
        if v.get("title") and v["title"] != "Default Title" and v["title"].lower() not in title.lower():
            title = f"{title} ({v['title']})"
        image = next((m.get("url") for m in (v.get("media") or []) + (p.get("media") or []) if m.get("type") == "image" and m.get("url")), None)
        url = p.get("url") or f"{STORE}/products/{p.get('handle')}"
        if not url.startswith(STORE + "/"):
            skipped.append({"id": p.get("id"), "reason": "unexpected_url"})
            continue
        vid = (v.get("id") or "").rsplit("/", 1)[-1]
        sku = v.get("sku") or vid
        rows.append({
            "id": f"ie-retail-harvey-norman-{p.get('handle') or vid}"[:180], "module_id": "retailer_promotions",
            "provider_id": "harvey_norman", "provider_name": "Harvey Norman", "country": "IE", "currency": "EUR",
            "offer_kind": "product_discount",
            "product": {"sku": sku, "name": title, "category": classify(title, search_cfg["category"]), "condition": "new_inferred",
                        "condition_basis": "store catalogue, in stock, no refurbished/display markers", "canonical_product_id": None,
                        "model_code": v.get("sku"), "brand": None},
            "pricing": {"device_price_minor": price, "reference_price_minor": ref, "reference_type": "retailer_was_price",
                        "reference_period": None, "discount_minor": ref - price, "discount_percent": d,
                        "includes_delivery": False, "delivery_cost_minor": None, "cashback_minor": None},
            "eligibility": {"conditions": [], "coupon_code": None},
            "validity": {"starts_on": None, "ends_on": None, "timezone": "Europe/Dublin"},
            "stock_status": "available_to_order", "status": "active",
            "verification": {"method": "official_ucp_catalog", "observed_at": fetched_at, "origin_fetched_at": fetched_at,
                             "fresh_until": (datetime.fromisoformat(fetched_at) + timedelta(hours=freshness_hours)).isoformat(),
                             "issues": ["delivery_cost_not_checked", "was_price_is_store_list_price"]},
            "source_ids": [search_cfg["id"]],
            "links": {"landing_url": url + (f"?variant={vid}" if vid and len(p.get("variants") or []) > 1 else ""), "affiliate_url": None},
            "media": {"image_url": image, "publish_image": bool(image)},
            "distribution": {"can_list": True, "notification_ready": False}})
    return rows, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "hn-data"))
    ap.add_argument("--config", default=str(ROOT / "harvey-norman.json"))
    ap.add_argument("--profile", default=os.environ.get("UCP_PROFILE_URL", PROFILE))
    args = ap.parse_args()
    started = time.monotonic()
    cfg = json.loads(Path(args.config).read_text())
    threshold = float(cfg.get("minimum_discount_percent", 30))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    by_id, skipped, report = {}, [], {"searches": [], "errors": []}
    for s in [s for s in cfg["searches"] if s.get("enabled", True)]:
        fetched_at = datetime.now(timezone.utc).isoformat()
        try:
            products, complete = search(s["query"], args.profile, int(cfg.get("max_pages", 4)), int(cfg.get("page_size", 30)),
                                        float(cfg.get("request_interval_seconds", 1.5)))
        except (UcpError, OSError, ValueError, KeyError) as exc:
            report["errors"].append(f"{s['id']}: {str(exc)[:200]}")
            continue
        rows, sk = offers_from(products, s, fetched_at, threshold)
        skipped += sk
        new = 0
        for r in rows:
            if r["id"] not in by_id:
                by_id[r["id"]] = r
                new += 1
            else:
                by_id[r["id"]]["source_ids"].append(s["id"])
        report["searches"].append({"id": s["id"], "products": len(products), "complete": complete, "kept": len(rows), "new": new})
        time.sleep(float(cfg.get("request_interval_seconds", 1.5)))
    offers = sorted(by_id.values(), key=lambda o: -o["pricing"]["discount_percent"])
    now = datetime.now(timezone.utc).isoformat()
    if report["errors"] and not report["searches"] and (out / "offers.json").exists():
        # Every search failed (interface down): keep the last results until they expire rather than emptying the list.
        prev = json.loads((out / "offers.json").read_text()).get("offers", [])
        offers = [o for o in prev if datetime.fromisoformat(o["verification"]["fresh_until"]) > datetime.now(timezone.utc)]
        report["kept_previous"] = len(offers)
    body = json.dumps({"schema_version": "1.0.0", "module_id": "retailer_promotions", "provider_id": "harvey_norman",
                       "generated_at": now, "offers": offers}, ensure_ascii=False, indent=1) + "\n"
    (out / "offers.json").write_text(body)
    (out / "excluded.json").write_text(json.dumps(skipped[:2000], ensure_ascii=False) + "\n")
    report.update(generated_at=now, duration_seconds=round(time.monotonic() - started, 1), offers=len(offers),
                  minimum_discount_percent=threshold, sha256=hashlib.sha256(body.encode()).hexdigest())
    (out / "run-report.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: report[k] for k in ("offers", "duration_seconds", "errors")} | {"searches": len(report["searches"])}))
    # Every search failing is an error; some failing is reported but the rest is kept.
    return 2 if report["errors"] and not report["searches"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
