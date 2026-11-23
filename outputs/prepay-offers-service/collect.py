#!/usr/bin/env python3
"""Read-only Prepay collection. No publishing, push messages, accounts or cloud writes.

Public HTML/PDF adapters + explicitly reviewed, hash-pinned campaign rules.
Unknown campaigns are discovered for review, never silently interpreted by an LLM.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from jsonschema import Draft202012Validator, FormatChecker
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent
PROVIDERS = {"vodafone": "Vodafone", "three": "Three", "eir": "eir", "tesco_mobile": "Tesco Mobile"}
ALLOWED_HOSTS = {"www.vodafone.ie", "vodafone.ie", "n.vodafone.ie", "binaries.vodafone.co.uk",
                 "www.three.ie", "three.ie", "www.eir.ie", "eir.ie", "www.tescomobile.ie", "tescomobile.ie"}


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(value).hexdigest()


def compact(text):
    return re.sub(r"\s+", " ", text).strip()


def money(text):
    return int((Decimal(str(text).replace(",", "").replace("€", "").strip()) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def read_components(soup):
    """eir publishes CMS JSON inside script elements; decode it without executing JS."""
    result = []
    for node in soup.find_all("script"):
        text = node.get_text()
        for match in re.finditer(r"var component\s*=\s*", text):
            try:
                obj, _ = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
                result.append(obj)
            except ValueError:
                pass
    return result


def expanded_soup(raw):
    soup = BeautifulSoup(raw, "html.parser")
    components = read_components(soup)
    strings = []

    def walk(obj):
        if isinstance(obj, dict):
            for val in obj.values():
                walk(val)
        elif isinstance(obj, list):
            for val in obj:
                walk(val)
        elif isinstance(obj, str) and ("<" in obj or obj.startswith("http")):
            strings.append(obj)

    for obj in components:
        # Header/footer repeats are not evidence for an offer on the actual page.
        if obj.get("type") not in ("eir-header", "eir-footer"):
            walk(obj.get("cmsContent", {}))
    for node in soup(["script", "style", "noscript", "nav", "footer"]):
        node.decompose()
    soup.append(BeautifulSoup("\n".join(strings), "html.parser"))
    return soup


class Fetcher:
    def __init__(self, cache, offline=False):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self.records = {}
        self.documents = {}
        self.last_host_request = {}

    def fetch(self, source):
        sid, url = source["id"], source["url"]
        host = urlparse(url).hostname
        if urlparse(url).scheme != "https" or host not in ALLOWED_HOSTS:
            raise ValueError("URL is not an approved public operator source")
        key = digest(url.encode())[:24]
        raw_path = self.cache / (key + ".raw")
        meta_path = self.cache / (key + ".json")
        record = dict(source)
        record["raw_file"] = str(raw_path.resolve())
        try:
            if self.offline:
                meta = json.loads(meta_path.read_text())
                if not raw_path.exists():
                    raise ValueError("offline snapshot unavailable")
                record.update(meta)
                record["replayed"] = True
            else:
                delay = max(0, 0.65 - (time.monotonic() - self.last_host_request.get(host, 0)))
                time.sleep(delay)
                cookies = ["--cookie-jar", str(self.cache / "vodafone-anonymous.cookies"), "--cookie", str(self.cache / "vodafone-anonymous.cookies")] if host == "www.vodafone.ie" else []
                result = subprocess.run([
                    "curl", "--silent", "--show-error", "--fail", "--location", "--max-redirs", "3",
                    "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "15",
                    "--max-time", "50", "--retry", "2", "--retry-delay", "2", "--max-filesize", "12000000",
                    *cookies, "--output", str(raw_path) + ".tmp", "--write-out", "%{http_code}\n%{url_effective}\n%{content_type}", url
                ], capture_output=True, text=True, timeout=165)
                self.last_host_request[host] = time.monotonic()
                if result.returncode:
                    raise ValueError(result.stderr.strip()[:300])
                lines = result.stdout.splitlines()
                if len(lines) < 2 or urlparse(lines[1]).hostname not in ALLOWED_HOSTS:
                    raise ValueError("Unexpected redirect target")
                Path(str(raw_path) + ".tmp").replace(raw_path)
                meta = {"http_status": int(lines[0]), "final_url": lines[1],
                        "content_type": lines[2] if len(lines) > 2 else "",
                        "fetched_at": datetime.now(timezone.utc).isoformat()}
                meta_path.write_text(json.dumps(meta, indent=2))
                record.update(meta)
            raw = raw_path.read_bytes()
            record["sha256"] = digest(raw)
            record["bytes"] = len(raw)
            if raw.startswith(b"%PDF"):
                pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(raw)).pages]
                text = compact("\n".join(pages))
                document = {"text": text, "pages": pages, "soup": None}
            elif raw.lstrip().startswith((b"{", b"[")) and "json" in record.get("content_type", ""):
                obj=json.loads(raw)
                document={"json":obj,"text":json.dumps(obj,ensure_ascii=False),"soup":None}
            else:
                soup = expanded_soup(raw.decode("utf-8", errors="replace"))
                document = {"text": compact(soup.get_text(" ", strip=True)), "soup": soup, "raw": raw}
                if len(document["text"]) < 150 or "captcha" in (soup.title.get_text().lower() if soup.title else ""):
                    raise ValueError("Page is empty, blocked, or requires a new adapter")
            record["fetch_status"] = "ok"
            self.documents[sid] = document
        except Exception as exc:
            record["fetch_status"] = "failed"
            record["error"] = str(exc)[:400]
            # Do not relabel an old successful snapshot as a fresh observation.
        self.records[sid] = record
        return self.documents.get(sid)


def base_offer(provider, key, title, source_ids, sources, asof, kind="handset_discount"):
    return {
        "id": "ie-prepay-" + slug(provider) + "-" + slug(key), "schema_version": "1.0.0",
        "country": "IE", "currency": "EUR", "service_type": "prepay", "provider_id": provider,
        "provider_name": PROVIDERS[provider], "offer_kind": kind,
        "title": {"en_IE": title, "zh_CN": ""}, "summary": {"en_IE": "", "zh_CN": ""},
        "campaign_id": None, "product": None,
        "eligibility": {"customer_groups": ["not_specified"], "port_in_required": None,
                        "allowed_origin_networks": [], "excluded_origin_networks": [], "clubcard_required": False,
                        "minimum_top_up_minor": None, "conditions": []},
        "pricing": {"device_price_minor": None, "reference_price_minor": None, "reference_type": None,
                    "discount_minor": None, "discount_percent": None,
                    "includes_required_top_up": None},
        "benefits": [], "validity": {"starts_on": None, "ends_on": None, "claim_by": None, "redeem_by": None, "support_query_by": None, "timezone": "Europe/Dublin"},
        "channels": ["online"], "stock_status": "unknown", "device_condition": "not_applicable",
        "status": "needs_review", "verification": {"level": "listing_only", "issues": [],
                    "verified_at": min((sources[x]["fetched_at"] for x in source_ids), default=asof.isoformat()),
                    "field_evidence": {}},
        "source_ids": source_ids,
        "links": {"landing_url": sources[source_ids[0]]["url"], "terms_url": None, "affiliate_url": None},
        "media": {"image_url": None, "usage_rights": "not_verified", "publish_image": False},
        "lifecycle": {"first_seen_at": None, "last_seen_at": None, "content_hash": None},
        "distribution": {"can_list": False, "notification_ready": False,
                         "notification_blockers": ["Editorial approval and subscriber notification rules not configured."]}
    }


def discount(offer, price, reference, reference_type):
    if reference <= 0 or price < 0 or price >= reference:
        raise ValueError("Invalid reference/offer price pair")
    offer["pricing"].update(device_price_minor=price, reference_price_minor=reference,
                           reference_type=reference_type, discount_minor=reference-price,
                           discount_percent=float((Decimal(reference-price) * 100 / reference).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)))


def pin_review(offer, pins, docs, records):
    """Changing T&C means the conditions we paraphrased must be checked again."""
    for sid in offer["source_ids"]:
        if records[sid].get("role") in ("terms", "candidate_terms"):
            pin = pins.get(sid)
            if not pin or pin != records[sid].get("sha256"):
                offer["verification"]["issues"].append("terms_changed_or_unreviewed:" + sid)
                offer["status"] = "needs_review"


def parse_three(fetcher, asof, pins):
    sid = "three-november-2026"
    if sid not in fetcher.documents:
        raise ValueError("Three current campaign PDF unavailable")
    text = fetcher.documents[sid]["text"]
    dates = re.search(r"Offer Dates:\s*(\d+)(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})\s*[–−-]\s*(\d+)(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})", text)
    if not dates:
        raise ValueError("Three campaign date header changed")
    start = datetime.strptime(" ".join(dates.group(1,2,3)), "%d %B %Y").date().isoformat()
    end = datetime.strptime(" ".join(dates.group(4,5,6)), "%d %B %Y").date().isoformat()
    rows = re.findall(r"(Google Pixel\s*10A\s*128GB|HONOR 600 Smart|TCL K70|Samsung A\d+\s*5G(?:\s*256GB)?|iPhone \d+ Refurbished)\s*€([\d,.]+)\s*€([\d,.]+)\s*€([\d,.]+)", text, re.I)
    if len(rows) < 6:
        raise ValueError("Three eligible device table missing rows; review parser")
    offers, rejected = [], []
    for name, regular, sale, saving in rows:
        if "Refurbished" in name:
            rejected.append({"provider_id":"three", "name":name, "reason":"refurbished_not_new", "source_id":sid})
            continue
        offer = base_offer("three", name + " port-in", name + " — switch to Three Prepay", [sid, "three-phones"], fetcher.records, asof)
        offer["product"] = {"name":name, "brand": "Samsung" if name.startswith("Samsung") else name.split()[0], "variant_label": name}
        offer["campaign_id"] = "three-prepay-november-2026"
        offer["device_condition"] = "new"
        discount(offer, money(sale), money(regular), "same_provider_non_port_in_price")
        if offer["pricing"]["discount_minor"] != money(saving):
            raise ValueError("Three stated saving differs from price calculation")
        offer["eligibility"].update(customer_groups=["port_in"], port_in_required=True)
        offer["eligibility"]["allowed_origin_networks"] = ["GoMo", "Vodafone", "eir", "Tesco Mobile", "An Post Mobile", "Clear Mobile", "Virgin Mobile", "Sky Mobile", "48"]
        offer["eligibility"]["conditions"] = ["Port an eligible number from another listed Irish network and purchase the eligible handset.", "Maximum two eligible handsets per order/address; cannot combine with another promotion."]
        offer["validity"].update(starts_on=start, ends_on=end)
        offer["channels"] = ["online", "retail_store"]
        offer["links"]["terms_url"] = fetcher.records[sid]["url"]
        offer["links"]["landing_url"] = fetcher.records["three-phones"]["url"]
        offer["verification"].update(level="terms_verified", field_evidence={"pricing":sid+":eligible-handset-table", "eligibility":sid+":port-in-eligibility", "validity":sid+":offer-date-header"})
        # The footer repeats a conflicting 2025 start year; preserve and expose this source issue.
        if "11/11/2025" in text:
            offer["verification"]["issues"].append("source_start_year_typo:header_2026_body_2025;header_used")
        offer["summary"]["en_IE"] = "Save €" + saving + " compared with Three's non-port-in handset price; number transfer required."
        offer["summary"]["zh_CN"] = "携号转网至 Three Prepay，相比同运营商非转网手机价节省 €" + saving + "。"
        offer["status"] = "active"
        pin_review(offer, pins, fetcher.documents, fetcher.records)
        offers.append(offer)
    return offers, rejected


def parse_tesco(fetcher, asof, pins):
    sid = "tesco-phones"
    soup = fetcher.documents[sid]["soup"]
    cards = soup.select("article.phoneItem")
    if len(cards) < 5:
        raise ValueError("Tesco structured product card selector changed")
    offers, rejected = [], []
    for card in cards:
        name_node = card.select_one("h2")
        if not name_node:
            raise ValueError("Tesco card is missing a name")
        name = compact(name_node.get_text(" ", strip=True))
        cardtext = compact(card.get_text(" ", strip=True))
        if re.search(r"nearly new|mint\+|refurbished|\(good\)|\(very good\)", name + " " + cardtext, re.I):
            rejected.append({"provider_id":"tesco_mobile", "name":name, "reason":"not_new", "source_id":sid})
            continue
        badge = card.select_one(".phone-list__special-offer")
        if not badge:
            continue  # A regular price alone is not a promotion.
        badge_text = compact(badge.get_text(" ", strip=True))
        price_node = card.select_one(".phone-list__phone-title p")
        regular_match = re.search(r"€([\d,.]+)", price_node.get_text() if price_node else "")
        club = card.select_one(".phone-list__clubcard-text")
        club_match = re.search(r"€([\d,.]+)", club.get_text() if club else "")
        saving_match = re.search(r"Save\s*€([\d,.]+)", badge_text, re.I)
        gift_match = re.search(r"(?:Includes|Claim)\s+Free\s+(.+)", badge_text, re.I)
        if not saving_match and not gift_match:
            rejected.append({"provider_id":"tesco_mobile", "name":name, "reason":"unparsed_promotion_badge", "source_id":sid})
            continue
        offer = base_offer("tesco_mobile", name + " prepay-clubcard", name + " — Tesco Mobile Prepay", [sid], fetcher.records, asof, "handset_discount" if saving_match else "gift")
        info = json.loads(card.get("data-ga4-select", "{}"))
        item = info.get("ecommerce", {}).get("items", [{}])[0]
        offer["product"] = {"name":name, "brand":item.get("item_brand"), "variant_label":item.get("item_variant"), "provider_sku":item.get("item_id")}
        offer["device_condition"] = "new"
        offer["campaign_id"] = "tesco-prepay-current-clubcard"
        offer["eligibility"]["clubcard_required"] = bool(club_match)
        offer["eligibility"]["conditions"] = ["Clubcard price requires Tesco Mobile registration and is available at point of sale."] if club_match else []
        offer["eligibility"]["conditions"].append("No port-in-only restriction stated on the listing; customer-group eligibility is not independently confirmed.")
        if saving_match:
            if not regular_match or not club_match:
                raise ValueError("Tesco saving badge is missing price pair: " + name)
            discount(offer, money(club_match.group(1)), money(regular_match.group(1)), "same_provider_current_non_clubcard_price")
            if offer["pricing"]["discount_minor"] != money(saving_match.group(1)):
                raise ValueError("Tesco saving inconsistent: " + name)
        elif regular_match:
            offer["pricing"]["device_price_minor"] = money(regular_match.group(1))
        if gift_match:
            offer["benefits"].append({"type":"gift", "description":gift_match.group(1), "value_minor":None, "fulfilment":"claim_required" if "Claim Free" in badge_text else "not_confirmed"})
            offer["verification"]["issues"].append("gift_claim_terms_not_verified")
            offer["verification"]["field_evidence"]["gift"] = sid + ":promotion-badge-only"
        link = card.select_one("a.phone-link[href]")
        offer["links"]["landing_url"] = urljoin(fetcher.records[sid]["url"], link["href"])
        img = card.select_one(".phone-list__image img[src]")
        if img:
            offer["media"]["image_url"] = urljoin(fetcher.records[sid]["url"], img["src"])
        offer["links"]["terms_url"] = "https://www.tescomobile.ie/terms-and-conditions"
        offer["verification"]["field_evidence"].update({"pricing":sid+":article.phoneItem[sku="+str(item.get("item_id"))+"]", "condition":sid+":new-product-listing;used-badges-excluded"})
        offer["status"] = "active_end_unknown"
        if offer["offer_kind"] == "gift":
            offer["status"] = "needs_review"
        if "tesco-home" in fetcher.documents:
            # A dedicated Prepay promo panel also appears inside the site's navigation.
            # Read only an exact-model local panel, including its prices, never a full-page date.
            home = BeautifulSoup(fetcher.documents["tesco-home"]["raw"], "html.parser")
            for heading in home.find_all(["h1", "h2", "h3", "h4"]):
                if compact(heading.get_text(" ", strip=True)).casefold() != name.casefold():
                    continue
                parent = heading
                for _ in range(4):
                    parent = parent.parent
                    if parent is None:
                        break
                    scoped = compact(parent.get_text(" ", strip=True))
                    ends = re.search(r"Offer ends\s+(\d{2}\.\d{2}\.\d{4})", scoped)
                    known_prices = all(f"€{v/100:.2f}" in scoped for v in (offer["pricing"]["device_price_minor"], offer["pricing"]["reference_price_minor"]) if v is not None)
                    if ends and known_prices and len(scoped) < 750 and scoped.count("Offer ends")==1:
                        offer["validity"]["ends_on"] = datetime.strptime(ends.group(1), "%d.%m.%Y").date().isoformat()
                        offer["source_ids"].append("tesco-home")
                        offer["verification"]["field_evidence"]["validity"] = "tesco-home:exact-product-heading-and-local-banner"
                        if offer["status"] != "needs_review":
                            offer["status"] = "active"
                        break
        offer["summary"]["en_IE"] = ("Clubcard handset price; save €"+saving_match.group(1)+" versus the current non-Clubcard price." if saving_match else "Prepay handset with a gift advertised on the current listing.")
        offer["summary"]["zh_CN"] = ("Clubcard 会员手机价，相比当前非会员价节省 €"+saving_match.group(1)+"。" if saving_match else "官网当前列出的 Prepay 手机赠品优惠。")
        offers.append(offer)
    if not offers:
        raise ValueError("Tesco parsed zero promotions; do not treat this as no offers")
    return offers, rejected


def parse_vodafone(fetcher, asof, pins):
    sid = "vodafone-christmas-2026"
    text = fetcher.documents[sid]["text"]
    # Numeric/date guards complement hash review: fail closed when a campaign is replaced.
    if not all(x in text for x in ["Christmas Porting Offer 1", "Christmas Porting Offer 4", "€249.99", "€200", "2027"]):
        raise ValueError("Vodafone Christmas campaign structure changed")
    profiles = [
        ("general-handset-50", "Switch to Vodafone Prepay: €50 off eligible handsets", "handset_discount", 5000, None, ["Eligible handset priced above €89.99; check specific handset promotions before applying."], None),
        ("samsung-xiaomi-special", "Vodafone Prepay: selected Samsung and Xiaomi handset savings", "handset_discount", None, None, ["€100 off Samsung A17, A17 5G and A18; €200 off Samsung A27 5G 128GB, A37 and A57; €100 off Xiaomi Redmi 17 5G."], ["Samsung A17", "Samsung A17 5G", "Samsung A18", "Samsung A27 5G 128GB", "Samsung A37", "Samsung A57", "Xiaomi Redmi 17 5G"]),
        ("pixel-10a-249", "Google Pixel 10a for €249.99 when switching to Vodafone Prepay", "handset_discount", 20000, 24999, [], ["Google Pixel 10a"]),
        ("second-topup-free", "Vodafone Prepay: a free second top-up when you switch", "bonus_credit", None, None, ["Voucher matches the selected plan's top-up amount; online delivery by SMS after eligibility checks.", "Voucher/code must be redeemed by 28 February 2027; contact support by 15 February 2027 if missing."], None)
    ]
    offers = []
    for key, title, kind, saving, price, conditions, models in profiles:
        offer = base_offer("vodafone", key, title, [sid, "vodafone-terms-index", "vodafone-phones"], fetcher.records, asof, kind)
        offer["campaign_id"] = "vodafone-payg-christmas-2026"
        offer["product"] = {"name":models[0] if models and len(models)==1 else "Eligible Prepay handsets", "brand":None, "variant_label":None, "eligible_models":models} if kind=="handset_discount" else None
        offer["device_condition"] = "new" if kind=="handset_discount" else "not_applicable"
        offer["eligibility"].update(customer_groups=["port_in"], port_in_required=True, excluded_origin_networks=["Lyca Mobile"], minimum_top_up_minor=2000)
        offer["eligibility"]["conditions"] = ["New number connections, existing Vodafone PAYG customers and deferred ports are excluded.", "Choose an eligible PAYG plan. Online: activate with the required €20/€30-or-more top-up within 28 days and make an outgoing call, text or data use within 14 days of top-up."] + conditions
        offer["validity"].update(starts_on="2026-11-13", ends_on="2027-01-05")
        offer["channels"] = ["online", "retail_store", "franchise_store"]
        offer["pricing"].update(device_price_minor=price, discount_minor=saving, includes_required_top_up=False if price else None)
        offer["links"].update(landing_url=fetcher.records["vodafone-phones"]["url"], terms_url=fetcher.records[sid]["url"])
        offer["verification"].update(level="terms_verified", field_evidence={"eligibility":sid+":offer-specific-terms", "validity":sid+":offer-period", "pricing":sid+":"+key})
        offer["summary"]["en_IE"] = "Port-in promotion; excludes Lyca Mobile. The required activation top-up is separate from the advertised handset price."
        offer["summary"]["zh_CN"] = "携号转网优惠，排除 Lyca Mobile；所需激活充值与手机价格分开。"
        offer["combinability"] = {"handset_offers":"choose_one", "can_combine_with_free_second_topup":True}
        if key=="samsung-xiaomi-special":
            offer["benefits"] = [
                {"type":"handset_discount", "description":"Samsung A17 / A17 5G / A18", "value_minor":10000, "fulfilment":"at_purchase"},
                {"type":"handset_discount", "description":"Samsung A27 5G 128GB / A37 / A57", "value_minor":20000, "fulfilment":"at_purchase"},
                {"type":"handset_discount", "description":"Xiaomi Redmi 17 5G", "value_minor":10000, "fulfilment":"at_purchase"}]
        if kind=="bonus_credit":
            offer["benefits"] = [{"type":"top_up_voucher", "description":"One voucher matching the qualifying plan top-up amount; not cash.", "value_minor":None, "fulfilment":"sms_or_store_voucher"}]
            offer["validity"]["redeem_by"] = "2027-02-28"
            offer["validity"]["support_query_by"] = "2027-02-15"
        # Do not invent a reference price for the Pixel from the advertised saving.
        offer["status"] = "active"
        pin_review(offer, pins, fetcher.documents, fetcher.records)
        offers.append(offer)
    if "vodafone-catalogue" not in fetcher.documents:
        return offers, []
    catalogue=fetcher.documents["vodafone-catalogue"]["json"]
    if catalogue.get("success") is not True:
        raise ValueError("Vodafone public catalogue request failed")
    rows=catalogue["data"]["productOffering"]
    if len(rows)<int(catalogue["data"]["xTotalCount"]):
        raise ValueError("Vodafone catalogue pagination incomplete; increase/follow page limit")
    excluded=[]
    for row in rows:
        attributes={a["name"]:a["productSpecCharacteristicValue"][0].get("value") for a in row.get("prodSpecCharValueUse",[]) if a.get("productSpecCharacteristicValue")}
        brand=attributes.get("brand","")
        name=brand+" "+row["name"]
        if re.search(r"refurbished|nearly new|mint\+",name,re.I):
            excluded.append({"provider_id":"vodafone","name":name,"reason":"refurbished_not_new","source_id":"vodafone-catalogue"})
            continue
        base=next((p["price"]["value"] for p in row.get("productOfferingPrice",[]) if p.get("isDefault")),None)
        sale=next((p["price"]["value"] for p in row.get("productOfferingPrice",[]) if p.get("priceType")=="discount"),None)
        if base is None or sale is None or money(sale)>=money(base):
            continue
        if attributes.get("inStock") is False:
            excluded.append({"provider_id":"vodafone","name":name,"reason":"catalogue_out_of_stock","source_id":"vodafone-catalogue"})
            continue
        # The website's listing labels prices as Switch & Save, not manufacturer's RRP.
        if not re.search(r"switch.*save",attributes.get("outerCardLabelPayG", ""),re.I):
            continue
        item=base_offer("vodafone","handset-"+row["id"],name+" — Vodafone Prepay switch price",["vodafone-catalogue",sid],fetcher.records,asof)
        item["product"]={"name":name,"brand":brand,"variant_label":"128GB" if row["id"]=="galaxy-a27-5g" else "Catalogue starting variant; confirm storage on product page", "provider_sku":row["id"]}
        item["campaign_id"]="vodafone-payg-christmas-2026"
        item["device_condition"]="new"
        item["stock_status"]="in_stock"
        item["eligibility"]=dict(offers[0]["eligibility"])
        item["eligibility"]["conditions"]=list(offers[0]["eligibility"]["conditions"][:2])
        discount(item,money(sale),money(base),"same_provider_non_port_in_price")
        item["pricing"]["includes_required_top_up"]=False
        item["validity"]=dict(offers[0]["validity"])
        item["channels"]=["online"]
        item["links"].update(landing_url="https://www.vodafone.ie/mobile/"+row["id"]+"?mobiletype=pay-as-you-go",terms_url=fetcher.records[sid]["url"])
        item["verification"].update(level="terms_verified",field_evidence={"pricing":"vodafone-catalogue:productOffering[id="+row["id"]+"].productOfferingPrice", "eligibility":sid+":offer-specific-terms", "storage":sid+":offer-2;A27-128GB" if row["id"]=="galaxy-a27-5g" else "not_confirmed_for_starting_variant"})
        item["summary"]={"en_IE":"Official Prepay catalogue Switch & Save price; minimum activation top-up is additional.","zh_CN":"官网 Prepay 商品列表转网价；最低激活充值另计。"}
        item["status"]="active"
        if money(base)-money(sale)==10000 and row["id"]=="galaxy-a16":
            item["status"]="needs_review"
            item["verification"]["issues"].append("catalogue_100_discount_not_in_current_pdf_eligible_A_series;check_terms")
        image=attributes.get("baseImageUri")
        if image:item["media"]["image_url"]=urljoin("https://www.vodafone.ie",image)
        pin_review(item,pins,fetcher.documents,fetcher.records)
        if row["id"]=="pixel-10a":
            item["id"]=next(o["id"] for o in offers if "pixel-10a-249" in o["id"])
            offers=[o for o in offers if o["id"]!=item["id"]]
        offers.append(item)
    return offers, excluded


def parse_eir(fetcher, asof, pins):
    sid = "eir-50-off"
    text = fetcher.documents[sid]["text"]
    landing = fetcher.documents["eir-phones"]["text"]
    if not re.search(r"€\s*50", text) or "€50 off" not in landing:
        raise ValueError("eir handset campaign not corroborated on current landing page")
    offer = base_offer("eir", "switch-handset-50", "Switch to eir Prepay: €50 off eligible handsets", [sid, "eir-phones", "eir-terms-index"], fetcher.records, asof)
    offer["product"] = {"name":"Eligible Prepay handsets priced over €89.99", "brand":None, "variant_label":None}
    offer["device_condition"] = "new"
    offer["campaign_id"] = "eir-prepay-switch-50"
    offer["eligibility"].update(customer_groups=["port_in"], port_in_required=True, minimum_top_up_minor=2000)
    offer["eligibility"]["conditions"] = ["Switch to eir Prepay at purchase and buy an eligible handset above €89.99.", "A minimum €20 top-up is required at purchase; the €50 benefit is not cash."]
    offer["pricing"]["discount_minor"] = 5000
    offer["channels"] = ["not_specified"]
    offer["links"].update(landing_url=fetcher.records["eir-phones"]["url"], terms_url=fetcher.records[sid]["url"])
    offer["verification"].update(level="terms_verified", field_evidence={"eligibility":sid+":clause-4", "pricing":sid+":clause-4", "current_listing":"eir-phones:prepay-page-introduction"})
    offer["summary"] = {"en_IE":"€50 handset saving for eligible switchers; a separate €20 minimum top-up is required.", "zh_CN":"符合条件的携号转网客户购机减 €50；另需至少充值 €20。"}
    offer["status"] = "active_end_unknown"
    pin_review(offer, pins, fetcher.documents, fetcher.records)
    offers = [offer]
    # Live banner has a €20 claim, but the located credit PDF is historical.
    if "€20 free credit" in fetcher.documents["eir-prepay"]["text"]:
        credit = base_offer("eir", "switch-free-credit-20", "eir advertises €20 free credit when switching", ["eir-prepay"], fetcher.records, asof, "bonus_credit")
        credit["benefits"] = [{"type":"bonus_credit", "description":"Advertised free credit; current full eligibility not verified.", "value_minor":2000, "fulfilment":"not_confirmed"}]
        credit["eligibility"].update(customer_groups=["port_in"], port_in_required=True)
        credit["verification"]["issues"].append("current_credit_terms_not_found;do_not_apply_2025_terms")
        credit["summary"] = {"en_IE":"The current landing page shows this promotion, but current full terms need confirmation before publication or alerts.", "zh_CN":"官网当前展示赠 €20 话费，但未找到匹配的现行完整条款；暂不发布或提醒。"}
        offers.append(credit)
    return offers, []


def discovery(fetcher, year):
    candidates = []
    configured = {r["url"] for r in fetcher.records.values()}
    for sid, doc in fetcher.documents.items():
        if fetcher.records[sid].get("role") != "discovery" or not doc.get("soup"):
            continue
        for a in doc["soup"].find_all("a", href=True):
            label = compact(a.get_text(" ", strip=True))
            href = urljoin(fetcher.records[sid]["url"], a["href"])
            if href in configured or not re.search(r"prepay|pay.as.you.go|redemption|cashback|gift", label, re.I):
                continue
            years = [int(y) for y in re.findall(r"\b20\d{2}\b", label + " " + href)]
            # Discover current-year pages, undated terms and gifts, not the whole historical archive.
            if years and max(years) < year:
                continue
            if re.search(r"bill.?pay|broadband|insurance|competition|pricing sheet|general terms", label, re.I):
                continue
            if fetcher.records[sid]["provider"] == "vodafone":
                nearest_heading = a.find_previous(["h2", "h3", "h4"])
                if nearest_heading is None or compact(nearest_heading.get_text()) != str(year):
                    continue
            if not href.lower().split("?")[0].endswith((".pdf", ".html")) and "getContentAsset" not in href:
                continue
            if urlparse(href).hostname not in ALLOWED_HOSTS:
                continue
            candidates.append({"provider_id":fetcher.records[sid]["provider"], "title":label,
                               "url":href, "discovered_from":sid, "status":"not_yet_verified"})
    return list({x["url"]:x for x in candidates}.values())


def excluded_campaigns(fetcher):
    result = []
    if "three-dunnes-2026" in fetcher.documents:
        text = fetcher.documents["three-dunnes-2026"]["text"]
        if "October 2026" in text and "15" in text:
            result.append({"provider_id":"three", "name":"€20 Dunnes Stores e-Gift Card", "reason":"new_entries_closed_2026-10-15;later_topup_deadline_is_not_joining_deadline", "source_id":"three-dunnes-2026"})
    if "eir-old-free-credit" in fetcher.documents:
        if "30/06/25" in fetcher.documents["eir-old-free-credit"]["text"]:
            result.append({"provider_id":"eir", "name":"Historical €20 credit PDF", "reason":"2025_offer_not_current_terms", "source_id":"eir-old-free-credit"})
    if "vodafone-xiaomi-gift" in fetcher.documents:
        result.append({"provider_id":"vodafone", "name":"Xiaomi 17T / 17T Pro Watch + Buds gift", "reason":"current_manufacturer_promotion_but_prepay_device_availability_not_confirmed", "source_id":"vodafone-xiaomi-gift"})
    return result


def finalize(offers, previous, asof, freshness):
    old = {o["id"]:o for o in previous.get("offers", [])}
    changes = []
    merged = {}
    for o in offers:
        if o["id"] in merged:
            raise ValueError("Duplicate identity from adapters: " + o["id"])
        ends = o["validity"]["ends_on"]
        starts = o["validity"]["starts_on"]
        if ends and ends < asof.date().isoformat():
            o["status"] = "expired"
        elif starts and starts > asof.date().isoformat():
            o["status"] = "scheduled"
        verified = datetime.fromisoformat(o["verification"]["verified_at"])
        if o["product"]:
            variant = o["product"].get("variant_label") or ""
            named = o["product"]["name"] + " " + variant
            capacity = re.search(r"\b(\d{2,4})\s*GB\b", named, re.I)
            o["product"]["storage_gb"] = int(capacity.group(1)) if capacity else None
            o["product"]["network_generation"] = "5G" if re.search(r"\b5G\b", named, re.I) else ("4G" if re.search(r"\b4G\b", named, re.I) else None)
        fresh = (asof - verified).total_seconds() <= freshness*3600
        if not fresh and o["status"].startswith("active"):
            o["status"] = "stale"
        for benefit in o["benefits"]:
            benefit["can_publish"] = benefit["type"] != "gift" and o["status"] in ("active", "active_end_unknown")
            benefit["verification_status"] = "terms_verified" if benefit["can_publish"] and o["verification"]["level"] == "terms_verified" else "needs_review"
        o["verification"]["fresh_until"] = (verified + timedelta(hours=freshness)).isoformat()
        o["distribution"]["can_list"] = o["status"] in ("active", "active_end_unknown")
        if o["status"] == "active_end_unknown":
            o["verification"]["issues"].append("offer_end_not_published;requires_daily_recheck")
        material = {k:v for k,v in o.items() if k not in ("verification", "lifecycle", "source_ids", "distribution")}
        content_hash = digest(material)
        was = old.get(o["id"], {})
        o["lifecycle"] = {"first_seen_at":was.get("lifecycle",{}).get("first_seen_at") or o["verification"]["verified_at"], "last_seen_at":o["verification"]["verified_at"], "content_hash":content_hash}
        merged[o["id"]] = o
        event = "new" if not was else ("changed" if was.get("lifecycle",{}).get("content_hash") != content_hash else None)
        if event:
            changes.append({"offer_id":o["id"], "event":event, "old_hash":was.get("lifecycle",{}).get("content_hash"), "new_hash":content_hash, "at":asof.isoformat(), "send_notification":False})
    for oid, was in old.items():
        if oid not in merged:
            # A failed fetch/missing row must never silently expire or delete an offer.
            previous_status = was["status"]
            was["status"] = "unconfirmed"
            was["distribution"]["can_list"] = False
            was["distribution"]["notification_ready"] = False
            was["verification"]["issues"] = sorted(set(was["verification"]["issues"] + ["not_observed_in_this_run"]))
            merged[oid] = was
            if previous_status != "unconfirmed":
                changes.append({"offer_id":oid, "event":"not_observed", "send_notification":False, "at":asof.isoformat()})
    return sorted(merged.values(), key=lambda x:(x["provider_id"], x["id"])), changes


def csv_export(path, offers):
    fields = ["id","provider","title","status","customer_groups","kind","device_price_eur","reference_price_eur","reference_type","discount_eur","discount_percent","benefits","conditions","ends_on","verified_at","can_list","landing_url","terms_url"]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for o in offers:
            p=o["pricing"]
            eur=lambda x:"" if x is None else f"{x/100:.2f}"
            writer.writerow(dict(zip(fields,[o["id"],o["provider_name"],o["title"]["en_IE"],o["status"],"|".join(o["eligibility"]["customer_groups"]),o["offer_kind"],eur(p["device_price_minor"]),eur(p["reference_price_minor"]),p["reference_type"],eur(p["discount_minor"]),p["discount_percent"],"; ".join(b["description"] for b in o["benefits"]),"; ".join(o["eligibility"]["conditions"]),o["validity"]["ends_on"],o["verification"]["verified_at"],o["distribution"]["can_list"],o["links"]["landing_url"],o["links"]["terms_url"]])))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, help="Raw evidence directory outside the public app data")
    parser.add_argument("--out", default=str(ROOT/"data"))
    parser.add_argument("--offline", action="store_true", help="Replay saved raw sources; original fetch times retained")
    parser.add_argument("--as-of", help="ISO timestamp for reproducible evaluation, e.g. 2026-11-22T22:00:00+00:00")
    args=parser.parse_args()
    asof=datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(ZoneInfo("Europe/Dublin"))
    if asof.tzinfo is None:parser.error("--as-of needs a timezone offset")
    asof=asof.astimezone(ZoneInfo("Europe/Dublin"))
    config=json.loads((ROOT/"sources.json").read_text())
    pins=json.loads((ROOT/"reviewed-terms.json").read_text()) if (ROOT/"reviewed-terms.json").exists() else {}
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    previous=json.loads((out/"offers.json").read_text()) if (out/"offers.json").exists() else {}
    fetcher=Fetcher(args.cache,args.offline)
    for source in config["sources"]:
        fetcher.fetch(source)
        print(source["id"]+": "+fetcher.records[source["id"]]["fetch_status"], file=sys.stderr)
    offers,rejected,health=[],[],[]
    for provider,adapter in [("vodafone",parse_vodafone),("three",parse_three),("eir",parse_eir),("tesco_mobile",parse_tesco)]:
        try:
            rows,excluded=adapter(fetcher,asof,pins)
            offers.extend(rows);rejected.extend(excluded)
            health.append({"provider_id":provider,"adapter_status":"ok","observed_offers":len(rows),"coverage":"campaign_and_landing_pages;not_complete_catalog","error":None})
        except Exception as exc:
            health.append({"provider_id":provider,"adapter_status":"failed","observed_offers":0,"coverage":"unconfirmed","error":str(exc)})
    offers,changes=finalize(offers,previous,asof,config["freshness_hours"])
    rejected.extend(excluded_campaigns(fetcher))
    public_sources=[{k:v for k,v in r.items() if k not in ("raw_file",)} for r in fetcher.records.values()]
    result={"schema_version":"1.0.0","dataset":"mobile123-ie-prepay-promotions","generated_at":asof.isoformat(),"scope":{"country":"IE","service_type":"prepay","providers":list(PROVIDERS),"device_condition":"new_only","plan_comparison":False},"offers":offers,"sources":public_sources,"coverage":health}
    schema=json.loads((ROOT/"offers.schema.json").read_text())
    Draft202012Validator(schema,format_checker=FormatChecker()).validate(result)
    products={o["id"] for o in offers}
    if len(products)!=len(offers):raise ValueError("Duplicate offer IDs")
    payloads={"offers.json":result,"discovery-review.json":{"generated_at":asof.isoformat(),"candidates":discovery(fetcher,asof.year)},"excluded.json":{"generated_at":asof.isoformat(),"records":rejected},"changes.json":{"generated_at":asof.isoformat(),"events":changes},"run-report.json":{"generated_at":asof.isoformat(),"providers":health,"sources":public_sources,"counts":{"total_records":len(offers),"listable":sum(o["distribution"]["can_list"] for o in offers),"needs_review":sum(o["status"]=="needs_review" for o in offers),"excluded":len(rejected)}}}
    payloads["summary.json"] = {
        "generated_at": asof.isoformat(),
        "provider_counts": [{"provider_id": provider,
            "total": sum(o["provider_id"] == provider for o in offers),
            "listable": sum(o["provider_id"] == provider and o["distribution"]["can_list"] for o in offers),
            "needs_review": sum(o["provider_id"] == provider and o["status"] == "needs_review" for o in offers)} for provider in PROVIDERS],
        "validation": "JSON Schema validated",
        "scope_note": "Verified campaign/listing coverage, not a promise of an exhaustive catalogue. No blog publication, cloud deployment, scheduled jobs or notifications performed."
    }
    for name,data in payloads.items():
        tmp=out/(name+".tmp");tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n");tmp.replace(out/name)
    csv_export(out/"offers.csv",offers)
    print(json.dumps(payloads["run-report.json"]["counts"]))
    return 2 if any(h["adapter_status"]=="failed" for h in health) else 0


if __name__=="__main__":
    raise SystemExit(main())
