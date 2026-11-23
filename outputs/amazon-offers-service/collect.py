#!/usr/bin/env python3
"""Amazon.ie keyword request plans, tagged links and guarded Creators API integration."""
import argparse
import csv
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent
ASIN = re.compile(r"^[A-Z0-9]{10}$")
RESOURCES = ["itemInfo.title", "images.primary.medium", "offersV2.listings.price",
    "offersV2.listings.condition", "offersV2.listings.availability", "offersV2.listings.merchantInfo",
    "offersV2.listings.dealDetails", "offersV2.listings.type", "offersV2.listings.isBuyBoxWinner"]


def money(value):
    return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def affiliate_link(asin, tag, existing=None):
    asin = asin.upper()
    if not ASIN.fullmatch(asin) or not re.fullmatch(r"[A-Za-z0-9_-]+", tag):
        raise ValueError("Invalid ASIN or associate tag")
    if not existing:
        return "https://www.amazon.ie/dp/" + asin + "?" + urlencode({"tag":tag})
    p = urlparse(existing)
    if p.scheme != "https" or p.hostname not in ("www.amazon.ie", "amazon.ie") or p.username or p.password or p.port:
        raise ValueError("Expected an HTTPS Amazon.ie product URL")
    found = re.search(r"/(?:dp|gp/product)/([A-Za-z0-9]{10})(?:/|$)", p.path)
    if not found or found[1].upper() != asin:
        raise ValueError("Link ASIN differs from selected product")
    query = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True) if k.lower() != "tag"]
    query.append(("tag",tag))
    return urlunparse(p._replace(query=urlencode(query),fragment=""))


def validate_config(c):
    if c["marketplace"] != "www.amazon.ie" or c["currency"] != "EUR" or c["condition"] != "New":
        raise ValueError("This adapter only supports new Amazon.ie EUR offers")
    if not 1 <= c["pages_per_keyword"] <= 10 or not 1 <= c["items_per_page"] <= 10:
        raise ValueError("SearchItems page/count must be between 1 and 10")
    if not 0 < c["minimum_discount_percent"] < 100 or not 0 < c["api_cache_hours"] < 24:
        raise ValueError("Invalid discount or cache lifetime")
    keys = [k["id"] for k in c["keywords"]]
    if len(set(keys)) != len(keys) or any(not k["query"].strip() for k in c["keywords"]):
        raise ValueError("Keyword IDs must be unique; queries cannot be empty")
    affiliate_link("B0DJGCX6Q2", c["associate_tag"])


def request_plan(c):
    result = []
    for k in c["keywords"]:
        if not k["enabled"]:
            continue
        for page in range(1,c["pages_per_keyword"]+1):
            # API filter is a discovery hint; same-listing discount is checked again locally.
            payload = {"marketplace":c["marketplace"], "partnerTag":c["associate_tag"],
                "keywords":k["query"], "searchIndex":k["search_index"], "condition":"New",
                "availability":"Available", "currencyOfPreference":"EUR", "languagesOfPreference":[c["language"]],
                "itemCount":c["items_per_page"], "itemPage":page,
                "minSavingPercent":max(1,int(c["minimum_discount_percent"])-1), "resources":RESOURCES}
            result.append({"keyword_id":k["id"], "operation":"SearchItems", "endpoint":"https://creatorsapi.amazon/catalog/v1/searchItems", "payload":payload})
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CreatorsClient:
    """Credential version 3.2 only. Tokens stay in memory and are never logged."""
    def __init__(self, client_id, client_secret, version):
        if not client_id or not client_secret:
            raise ValueError("Missing Creators API credentials")
        if version != "3.2":
            raise ValueError("Adapter supports credential version 3.2; other versions require their documented endpoint")
        self.client_id, self.client_secret = client_id, client_secret
        self.token, self.expires_at = None, 0
        self.opener = build_opener(NoRedirect())
        self.calls = 0

    def post(self, url, payload, headers):
        req = Request(url, data=json.dumps(payload).encode(), headers={"Content-Type":"application/json", **headers}, method="POST")
        try:
            with self.opener.open(req,timeout=30) as response:
                raw = response.read(40961)
        except HTTPError as exc:
            # Avoid logging error bodies, tokens, request headers or secrets.
            raise RuntimeError("Amazon returned HTTP " + str(exc.code) + "; verify credentials, eligibility, quota and locale") from None
        except (URLError, TimeoutError):
            raise RuntimeError("Amazon connection failed; no fresh product data verified") from None
        if len(raw) > 40960:
            raise RuntimeError("Amazon response exceeds this adapter's 40KB limit; reduce itemCount/resources")
        return json.loads(raw)

    def access_token(self):
        if self.token and time.monotonic() < self.expires_at:
            return self.token
        r = self.post("https://api.amazon.co.uk/auth/o2/token", {"grant_type":"client_credentials",
            "client_id":self.client_id, "client_secret":self.client_secret, "scope":"creatorsapi::default"}, {})
        self.token = r["access_token"]
        self.expires_at = time.monotonic() + max(0,int(r["expires_in"])-60)
        return self.token

    def search(self, request):
        self.calls += 1
        return self.post(request["endpoint"], request["payload"],
            {"Authorization":"Bearer " + self.access_token(), "x-marketplace":"www.amazon.ie"})


def api_items(response, keyword_id, c, observed_at):
    accepted, rejected = [], []
    for item in response.get("searchResult",{}).get("items",[]):
        asin = item.get("asin","").upper()
        if not ASIN.fullmatch(asin):
            rejected.append({"asin":asin, "reason":"invalid_asin"})
            continue
        title = item.get("itemInfo",{}).get("title",{}).get("displayValue","")
        if not title or re.search(r"\brenewed\b|\brefurbished\b|\bpre-owned\b|\bused\b",title,re.I):
            rejected.append({"asin":asin, "reason":"missing_title_or_not_new"})
            continue
        for listing in item.get("offersV2",{}).get("listings",[]):
            try:
                if listing.get("condition",{}).get("value") != "New":
                    raise ValueError("listing_not_explicitly_new")
                if listing.get("availability",{}).get("type") != "IN_STOCK":
                    raise ValueError("listing_not_explicitly_in_stock")
                # Only use the current featured listing: a lower third-party price may not be the landing-page price.
                if listing.get("isBuyBoxWinner") is not True or listing.get("violatesMAP") is True:
                    raise ValueError("not_featured_listing_or_map_restricted")
                p = listing["price"]
                basis = p["savingBasis"]
                if p["money"]["currency"] != "EUR" or basis["money"]["currency"] != "EUR":
                    raise ValueError("non_eur_price")
                price, reference = money(p["money"]["amount"]), money(basis["money"]["amount"])
                if price < 0 or reference <= price:
                    raise ValueError("invalid_reference_price")
                exact = Decimal(reference-price)*100/reference
                if exact <= Decimal(str(c["minimum_discount_percent"])):
                    raise ValueError("below_discount_threshold")
                deal = listing.get("dealDetails") or {}
                for key, lower in [("startTime",True),("endTime",False)]:
                    if deal.get(key):
                        d = datetime.fromisoformat(deal[key].replace("Z","+00:00"))
                        if d.tzinfo is None or (lower and d > observed_at) or (not lower and d <= observed_at):
                            raise ValueError("deal_not_current")
                access = deal.get("accessType")
                restricted = bool(access and access != "ALL")
                url = affiliate_link(asin,c["associate_tag"],item.get("detailPageURL"))
                basis_type = basis.get("savingBasisType")
                if not basis_type:
                    raise ValueError("reference_type_missing")
                image = item.get("images",{}).get("primary",{}).get("medium",{}).get("url")
                row = {"id":"ie-amazon-"+asin, "module_id":"amazon_promotions", "provider_id":"amazon_ie", "provider_name":"Amazon.ie",
                    "country":"IE", "currency":"EUR", "offer_kind":"product_discount",
                    "product":{"asin":asin,"name":title,"condition":"new","seller":listing.get("merchantInfo",{}).get("name")},
                    "pricing":{"device_price_minor":price,"reference_price_minor":reference,"reference_type":basis_type,
                        "reference_label":basis.get("savingBasisTypeLabel"),"discount_minor":reference-price,
                        "discount_percent":float(exact.quantize(Decimal("0.1"),rounding=ROUND_HALF_UP)),
                        "includes_delivery":False,"delivery_cost_minor":None},
                    "eligibility":{"prime_required":True if access and access.startswith("PRIME") else False if access == "ALL" else None, "access_type":access, "coupon_required":None,
                        "conditions":["Delivery address, seller and final checkout price can change eligibility."]},
                    "stock_status":"in_stock", "validity":{"starts_on":deal.get("startTime"),"ends_on":deal.get("endTime")},
                    "status":"needs_review", "matched_keyword_ids":[keyword_id],
                    "verification":{"method":"creators_api", "verified_at":observed_at.isoformat(),
                        "expires_at":(observed_at+timedelta(hours=c["api_cache_hours"])).isoformat(),
                        "issues":["editorial_review_pending","delivery_and_coupon_terms_not_verified"] + (["restricted_access_offer"] if restricted else [])},
                    "links":{"landing_url":url,"affiliate_url":url,"tag":c["associate_tag"]},
                    "media":{"image_url":image,"store_image_bytes":False,"publish_image":False},
                    "distribution":{"can_list":False,"notification_ready":False}}
                accepted.append(row)
            except (ValueError, KeyError, TypeError) as exc:
                rejected.append({"asin":asin,"reason":str(exc)[:150]})
    return accepted, rejected


def historical_candidates(seed, c):
    rows = []
    for p in seed["products"]:
        asin = p["asin"]
        price, reference = money(p["price"]), money(p["reference"])
        if reference <= price or Decimal(reference-price)*100/reference <= Decimal(str(c["minimum_discount_percent"])):
            continue
        matches = []
        for k in c["keywords"]:
            q = k["query"].lower()
            name = p["name"].lower()
            if k["enabled"] and (q in name or k["id"] == "fire-tv" and "fire tv stick" in name):
                matches.append(k["id"])
        if not matches:
            continue
        url = affiliate_link(asin,c["associate_tag"],p.get("link"))
        rows.append({"id":"ie-amazon-"+asin, "module_id":"amazon_promotions", "provider_id":"amazon_ie", "provider_name":"Amazon.ie",
            "country":"IE","currency":"EUR","offer_kind":"product_discount",
            "product":{"asin":asin,"name":p["name"],"condition":"unconfirmed","seller":None},
            "pricing":{"device_price_minor":price,"reference_price_minor":reference,
                "reference_type":"rrp_claimed_by_third_party" if "RRP" in p["basis"] else "unspecified_reference",
                "reference_label":p["basis"],"discount_minor":reference-price,
                "discount_percent":float((Decimal(reference-price)*100/reference).quantize(Decimal("0.1"),rounding=ROUND_HALF_UP)),
                "includes_delivery":False,"delivery_cost_minor":None},
            "eligibility":{"prime_required":None,"coupon_required":None,"conditions":[]},
            "stock_status":"unknown","status":"historical_candidate", "matched_keyword_ids":matches,
            "validity":{"starts_on":None,"ends_on":None},
            "verification":{"method":"historical_third_party_research","research_date":seed["research_date"],
                "verified_at":None,"expires_at":None,"source_url":p["source"],
                "issues":["not_a_live_amazon_price","new_condition_not_verified","asin_variant_not_reverified"]},
            "links":{"landing_url":url,"affiliate_url":url,"tag":c["associate_tag"]},
            "media":{"image_url":None,"store_image_bytes":False,"publish_image":False},
            "distribution":{"can_list":False,"notification_ready":False}})
    return sorted(rows,key=lambda o:-o["pricing"]["discount_percent"])


def merge_keyword_hits(rows):
    result = {}
    for row in rows:
        old = result.get(row["id"])
        if not old:
            result[row["id"]] = row
        else:
            old["matched_keyword_ids"] = sorted(set(old["matched_keyword_ids"]+row["matched_keyword_ids"]))
            if old["pricing"] != row["pricing"] or old["product"]["seller"] != row["product"]["seller"]:
                old["verification"]["issues"].append("conflicting_observations;recheck_before_publication")
    return sorted(result.values(),key=lambda o:-o["pricing"]["discount_percent"])


def blog_draft(seed, c):
    # Evergreen, original editorial draft. No stale prices, claimed savings, API text or unlicensed images.
    esc = html.escape
    sections = []
    eligible_asins = {o["product"]["asin"] for o in historical_candidates(seed,c)}
    for p in seed["products"]:
        if p["asin"] not in eligible_asins:
            continue
        url = affiliate_link(p["asin"],c["associate_tag"],p.get("link"))
        sections.append('<section><h2>'+esc(p["name"])+'</h2><p>'+esc(p["intro"])+'</p><p><a href="'+esc(url,quote=True)+'" target="_blank" rel="sponsored nofollow noopener noreferrer">View this product on Amazon.ie</a></p></section>')
    return '<article><h1>Tech to explore for your screen, sound and setup</h1><p>As an Amazon Associate I earn from qualifying purchases.</p><p>A product shortlist for further research. Check the exact model, new condition, seller, delivery options and current offer on Amazon.ie.</p>'+''.join(sections)+'</article>\n'


def purge_expired(out, now):
    path = out / "offers.json"
    if not path.exists():
        return 0
    data = json.loads(path.read_text())
    before = len(data["offers"])
    data["offers"] = [o for o in data["offers"] if o["verification"]["method"] != "creators_api" or
        datetime.fromisoformat(o["verification"]["expires_at"]) > now]
    removed = before-len(data["offers"])
    if removed:
        write_json(path,data)
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config",default=str(ROOT/"config.json"))
    parser.add_argument("--out",default=str(ROOT/"data"))
    parser.add_argument("--live",action="store_true",help="Requires eligible, configured Creators API credentials")
    parser.add_argument("--purge-expired",action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    now = datetime.now(timezone.utc)
    c = json.loads(Path(args.config).read_text())
    validate_config(c)
    seed = json.loads((ROOT/"research-seeds.json").read_text())
    out = Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    purged = purge_expired(out,now)
    if args.purge_expired:
        print(json.dumps({"expired_records_removed":purged}))
        return 0
    plan = request_plan(c)
    candidates = historical_candidates(seed,c)
    report = {"module_name":c["module_name"],"generated_at":now.isoformat(),"mode":"live" if args.live else "prepare",
        "api_status":"not_called", "api_search_calls":0,"llm_calls":0,"llm_tokens":0,"live_connection_tested":False,
        "registered_site":c["registered_site"], "associate_tag":c["associate_tag"],
        "planned_max_requests":len(plan),"theoretical_result_slots":len(plan)*c["items_per_page"],
        "exhaustive_catalogue":False,"warnings":[],"errors":[]}
    live_rows, rejected, client = [], [], None
    if args.live:
        try:
            client = CreatorsClient(os.environ.get("AMAZON_CREATORS_CLIENT_ID"),os.environ.get("AMAZON_CREATORS_CLIENT_SECRET"),os.environ.get("AMAZON_CREATORS_CREDENTIAL_VERSION"))
            done = set()
            for req in plan:
                if req["keyword_id"] in done:
                    continue
                response = client.search(req)
                observed = datetime.now(timezone.utc)
                rows, excluded = api_items(response,req["keyword_id"],c,observed)
                live_rows.extend(rows)
                rejected.extend(excluded)
                if response.get("errors"):
                    report["warnings"].append("Amazon reported partial errors; results are incomplete")
                if len(response.get("searchResult",{}).get("items",[])) < c["items_per_page"]:
                    done.add(req["keyword_id"])
                time.sleep(max(1.1,c["request_interval_seconds"]))
            report.update(api_status="completed",live_connection_tested=True)
        except (ValueError, RuntimeError, KeyError, json.JSONDecodeError) as exc:
            report["api_status"] = "blocked_or_failed"
            report["errors"].append(str(exc)[:250])
        report["api_search_calls"] = client.calls if client else 0
    else:
        report["warnings"].append("Prepared only: no API credentials used and no current Amazon offers retrieved")
    live_rows = merge_keyword_hits(live_rows)
    fresh_ids = {o["id"] for o in live_rows}
    offers = live_rows + [o for o in candidates if o["id"] not in fresh_ids]
    result = {"schema_version":"1.0.0","module_id":"amazon_promotions","module_name":c["module_name"],
        "generated_at":now.isoformat(),"scope":{"marketplace":c["marketplace"],"condition":"new_only;unconfirmed held for review","minimum_discount_percent":c["minimum_discount_percent"]},"offers":offers}
    Draft202012Validator(json.loads((ROOT/"offers.schema.json").read_text()),format_checker=FormatChecker()).validate(result)
    report.update(duration_seconds=round(time.monotonic()-started,2),historical_candidates=len(candidates),api_candidates=len(live_rows),
        listable_discounts=0,blog_published=False,notifications_sent=0,images_downloaded=0)
    for filename,data in [("offers.json",result),("request-plan.json",{"requests":plan}),("rejected.json",{"records":rejected}),("run-report.json",report)]:
        write_json(out/filename,data)
    (out/"blog-draft-no-prices.html").write_text(blog_draft(seed,c))
    # Export only identifiers/links; API-derived prices/titles must not create an unexpiring CSV copy.
    with (out/"affiliate-links.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["asin","affiliate_url","tag"])
        for o in offers:
            writer.writerow([o["product"]["asin"],o["links"]["affiliate_url"],c["associate_tag"]])
    print(json.dumps(report,ensure_ascii=False))
    return 2 if report["api_status"] == "blocked_or_failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
