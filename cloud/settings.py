"""Admin-editable monitoring settings (Firestore settings/sources), applied to the collectors before each run.

Operators: on/off per operator (their parsers are code; a new operator needs a new adapter).
Retailers: category pages for shops that have an adapter (Powercity, Expert) can be added, paused or removed.
Amazon: search keywords, with optional aliases used to match deal titles.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

OUTPUTS = Path(__file__).resolve().parent.parent / "outputs"
OPERATORS = {"vodafone": "Vodafone", "three": "Three", "eir": "eir", "tesco_mobile": "Tesco Mobile"}
RETAIL_ADAPTERS = {"powercity": ("powercity_cards", "powercity.ie", 0.7, 1), "expert": ("expert_cards", "www.expert.ie", 10, 3)}
RETAIL_CATEGORIES = ["headphones", "speakers", "computers", "tablets", "phones", "storage", "wearables", "cameras",
                     "gaming", "networking", "accessories", "monitors"]
DOC = ("settings", "sources")
# Shops checked but not collected, shown read-only in the admin panel with the reason.
UNAVAILABLE_SHOPS = [
    {"name": "Currys", "site": "currys.ie", "reason": "Blocks automated access (Cloudflare, HTTP 403). Needs a product feed or affiliate programme."},
    {"name": "Harvey Norman", "site": "harveynorman.ie", "reason": "Blocks automated access (Cloudflare challenge, HTTP 403). Needs a product feed or affiliate programme."},
    {"name": "DID Electrical", "site": "did.ie", "reason": "Rate-limits after a few requests (HTTP 429). Needs a product feed or permission."},
]


def defaults():
    retail = json.loads((OUTPUTS / "retailer-offers-service/sources.json").read_text())
    amazon = json.loads((OUTPUTS / "amazon-offers-service/config.json").read_text())
    return {
        "operators": [{"id": k, "name": v, "enabled": True} for k, v in OPERATORS.items()],
        "retail_pages": [{"id": s["id"], "provider_id": s["provider_id"], "category": s["category"], "url": s["url"], "enabled": True}
                         for s in retail["sources"] if s["provider_id"] in RETAIL_ADAPTERS],
        "amazon_keywords": [{"id": k["id"], "query": k["query"], "aliases": k.get("aliases", []), "enabled": k.get("enabled", True)}
                            for k in amazon["keywords"]],
    }


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "item"


def validate(s):
    """Return a cleaned settings dict or raise ValueError with a message an admin can act on."""
    ops = []
    for o in s.get("operators", []):
        if o.get("id") not in OPERATORS:
            raise ValueError(f"Unknown operator {o.get('id')!r}; new operators need a code adapter")
        ops.append({"id": o["id"], "name": OPERATORS[o["id"]], "enabled": bool(o.get("enabled", True))})
    pages, seen = [], set()
    for p in s.get("retail_pages", []):
        prov, url, cat = p.get("provider_id"), (p.get("url") or "").strip(), p.get("category")
        if prov not in RETAIL_ADAPTERS:
            raise ValueError(f"Unknown shop {prov!r}; only Powercity and Expert have adapters")
        host = RETAIL_ADAPTERS[prov][1]
        if not re.match(rf"^https://{re.escape(host)}/", url):
            raise ValueError(f"{url or 'Empty URL'}: {prov} pages must start with https://{host}/")
        if cat not in RETAIL_CATEGORIES:
            raise ValueError(f"Unknown category {cat!r}")
        pid = p.get("id") or f"{prov}-{slug(url.split('/', 3)[-1])}"
        if pid in seen:
            raise ValueError(f"Duplicate page id {pid}")
        seen.add(pid)
        pages.append({"id": pid, "provider_id": prov, "category": cat, "url": url, "enabled": bool(p.get("enabled", True))})
    kws, seen = [], set()
    for k in s.get("amazon_keywords", []):
        q = (k.get("query") or "").strip()
        if not q:
            raise ValueError("Amazon keywords cannot be empty")
        kid = k.get("id") or slug(q)
        if kid in seen:
            raise ValueError(f"Duplicate keyword {q!r}")
        seen.add(kid)
        aliases = [a.strip() for a in (k.get("aliases") or []) if a and a.strip()][:20]
        kws.append({"id": kid, "query": q, "aliases": aliases, "enabled": bool(k.get("enabled", True))})
    return {"operators": ops, "retail_pages": pages, "amazon_keywords": kws}


def load(db):
    snap = db.collection(DOC[0]).document(DOC[1]).get()
    if snap.exists:
        data = snap.to_dict()
        return validate(data), data.get("updated_at"), data.get("updated_by")
    return defaults(), None, None


def save(db, settings, user):
    clean = validate(settings)
    db.collection(DOC[0]).document(DOC[1]).set({**clean, "updated_at": datetime.now(timezone.utc).isoformat(), "updated_by": user})
    return clean


def apply(settings):
    """Rewrite the collectors' config files inside this container from the admin settings."""
    rpath = OUTPUTS / "retailer-offers-service/sources.json"
    retail = json.loads(rpath.read_text())
    fixed = [s for s in retail["sources"] if s["provider_id"] not in RETAIL_ADAPTERS]  # e.g. blocked shops kept as probes
    pages = []
    for p in settings["retail_pages"]:
        if not p["enabled"]:
            continue
        adapter, _, delay, pages_max = RETAIL_ADAPTERS[p["provider_id"]]
        entry = {"id": p["id"], "provider_id": p["provider_id"], "category": p["category"], "adapter": adapter, "url": p["url"],
                 "request_delay_seconds": delay}
        if pages_max > 1:
            entry["max_pages"] = pages_max
        pages.append(entry)
    retail["sources"] = pages + fixed
    rpath.write_text(json.dumps(retail, ensure_ascii=False, indent=1))
    apath = OUTPUTS / "amazon-offers-service/config.json"
    amazon = json.loads(apath.read_text())
    amazon["keywords"] = [{"id": k["id"], "query": k["query"], "aliases": k["aliases"], "enabled": k["enabled"],
                           "search_index": "Electronics"} for k in settings["amazon_keywords"]] or amazon["keywords"]
    apath.write_text(json.dumps(amazon, ensure_ascii=False, indent=2))
    return {"disabled_operators": [o["id"] for o in settings["operators"] if not o["enabled"]]}
