#!/usr/bin/env python3
"""Merge the three Mobile123 modules into one review dataset with buy links.

Reads only the modules' own output files. No network requests. Amazon links are built from the
ASIN and the Associates tag in amazon-offers-service/config.json; nothing else is added to
operator or retailer links.
"""
import csv
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "combined-deals"
# Where each module's output lives. The cloud job points these at its own working folders.
INPUTS = {
    "prepay-offers-service/data/offers.json": ROOT / "prepay-offers-service/data/offers.json",
    "retailer-offers-service/data/offers.json": ROOT / "retailer-offers-service/data/offers.json",
    "amazon-offers-service/config.json": ROOT / "amazon-offers-service/config.json",
    "amazon-offers-service/web-data/offers.json": ROOT / "amazon-offers-service/direct-data/offers.json",
}
AMAZON_SOURCE = "direct"  # "direct" = confirmed on Amazon product pages; "api" = Creators API; "web" = third-party leads only


def load(path):
    p = INPUTS.get(path, ROOT / path)
    return json.loads(Path(p).read_text()) if Path(p).exists() else {"offers": []}


def amazon_link(asin, tag):
    if not re.fullmatch(r"[A-Z0-9]{10}", asin or ""):
        raise ValueError("Bad ASIN: " + str(asin))
    if not re.fullmatch(r"[a-z0-9-]+-21", tag):
        raise ValueError("Bad Associates tag for amazon.ie: " + tag)
    return f"https://www.amazon.ie/dp/{asin}?tag={tag}"


def official(url, hosts):
    if not url:
        return None
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or not any(host == h or host.endswith("." + h) for h in hosts):
        raise ValueError("Unexpected link host: " + url)
    return url


def name_of(o):
    p = o.get("product") or {}
    name = p.get("name") or (o.get("title") or {}).get("en_IE") or o["id"]
    variant = p.get("variant_label")
    return f"{name} ({variant})" if variant and variant not in name else name


def operator_rows():
    data = load("prepay-offers-service/data/offers.json")
    rows = []
    for o in data["offers"]:
        p, e, v = o["pricing"], o["eligibility"], o["validity"]
        who = "转网 (port-in)" if e.get("port_in_required") else ("Clubcard 会员" if e.get("clubcard_required") else "未说明")
        notes = [o["summary"].get("zh_CN") or o["summary"].get("en_IE") or ""]
        if e.get("minimum_top_up_minor"):
            notes.append(f"最低激活充值 €{e['minimum_top_up_minor']/100:.0f}")
        rows.append({
            "module": "operator", "module_name": "运营商优惠", "provider": o["provider_name"], "id": o["id"],
            "title": name_of(o), "category": "phones" if o.get("product") else o["offer_kind"],
            "price_minor": p.get("device_price_minor"), "reference_minor": p.get("reference_price_minor"),
            "reference_label": {"same_provider_non_port_in_price": "同运营商非转网价", "same_provider_current_non_clubcard_price": "非会员价"}.get(p.get("reference_type"), p.get("reference_type")),
            "discount_percent": p.get("discount_percent"),
            "price_basis": "运营商官网 / 官方条款",
            "who": who, "ends_on": v.get("ends_on"), "status": o["status"],
            "can_list": o["distribution"]["can_list"], "fresh_until": o["verification"].get("fresh_until"),
            "buy_url": official(o["links"].get("landing_url"), ["vodafone.ie", "three.ie", "eir.ie", "tescomobile.ie"]),
            "terms_url": o["links"].get("terms_url"), "link_type": "官网链接（无联盟）",
            "notes": "；".join(n for n in notes if n)})
    return rows


def retailer_rows():
    data = load("retailer-offers-service/data/offers.json")
    rows = []
    hosts = ["powercity.ie", "expert.ie", "currys.ie", "harveynorman.ie"]
    for o in data["offers"]:
        p, v = o["pricing"], o["verification"]
        live = v["method"] == "direct_official_html"
        hist = p.get("observed_history") or {}
        notes = []
        if not live:
            notes.append("检索工具快照，读取时间不明，不能自动刷新")
        if o["eligibility"].get("conditions"):
            notes.append("促销文字：" + "; ".join(o["eligibility"]["conditions"])[:120] + "（未计入价格）")
        if any("observed_lower_or_equal_price_in_prior_30d" in i for i in v["issues"]):
            notes.append("前 30 天曾观测到更低价，Was 价需复核")
        if hist:
            notes.append(f"已观测 {hist['days_observed']} 天")
        rows.append({
            "module": "retailer", "module_name": "连锁零售店", "provider": o["provider_name"], "id": o["id"],
            "title": o["product"]["name"], "category": o["product"]["category"],
            "price_minor": p["device_price_minor"], "reference_minor": p["reference_price_minor"],
            "reference_label": {"retailer_was_price": "零售商 Was 价", "manufacturer_rrp": "制造商 RRP"}.get(p["reference_type"], p["reference_type"]),
            "discount_percent": p["discount_percent"],
            "price_basis": "零售商官网（本次直接读取）" if live else "官网快照（非实时）",
            "who": "所有顾客", "ends_on": None, "status": o["status"], "can_list": o["distribution"]["can_list"],
            "fresh_until": v.get("fresh_until"), "buy_url": official(o["links"]["landing_url"], hosts),
            "terms_url": None, "link_type": "官网链接（无联盟）", "notes": "；".join(notes)})
    return rows


def amazon_rows():
    cfg = load("amazon-offers-service/config.json")
    tag = cfg["associate_tag"]
    data = load("amazon-offers-service/web-data/offers.json")
    rows = []
    for o in data["offers"]:
        p, v = o["pricing"], o["verification"]
        method = v.get("method")
        wanted = {"direct": "amazon_product_page", "api": "creators_api"}.get(AMAZON_SOURCE)
        if (wanted and method != wanted) or (not wanted and method in ("amazon_product_page", "creators_api")):
            continue
        api = method in ("amazon_product_page", "creators_api")
        asin = o["product"]["asin"]
        check = (v.get("amazon_page_check") or {}).get("status")
        if api:
            src = "Amazon 商品页" if method == "amazon_product_page" else "Amazon 官方 API"
            notes = [f"{src}价格，核对于 {v.get('verified_at', '')[:16].replace('T', ' ')} UTC；以 Amazon 页面为准"]
            if o["product"].get("seller") and o["product"]["seller"] != "Amazon.ie":
                notes.append("卖家：" + o["product"]["seller"])
        else:
            notes = ["第三方折扣网站报价，Amazon 当前价未核实；参考价为第三方标注，不一定是 Amazon 原价"]
        if check == "price_hidden_requires_cart":
            notes.append("Amazon 商品页需加入购物车才显示价格")
        if o["status"] == "conflicting_quotes":
            notes.append("不同来源报价冲突")
        rows.append({
            "module": "amazon", "module_name": "Amazon.ie", "provider": "Amazon.ie", "id": o["id"],
            "title": re.sub(r"^(amazon\s+)?b+lack friday( deal)?:\s*|^deal:\s*", "", o["product"]["name"], flags=re.I),
            "category": ",".join(o.get("matched_keyword_ids") or []),
            "price_minor": p["device_price_minor"], "reference_minor": p["reference_price_minor"],
            "reference_label": (p.get("reference_label") or "Amazon 参考价") if api else "第三方参考价", "discount_percent": p["discount_percent"],
            "price_basis": ("Amazon 商品页（核对时间见备注）" if method == "amazon_product_page" else "Amazon 官方 API") if api else "第三方报价（内部线索，未核实）", "who": "所有顾客", "ends_on": None, "status": o["status"],
            "can_list": False, "fresh_until": v.get("fresh_until") or v.get("expires_at"), "price_public": api,
            "buy_url": amazon_link(asin, tag), "terms_url": None,
            "link_type": f"Amazon Associates（tag={tag}）", "asin": asin, "notes": "；".join(notes)})
    return rows


PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mobile123 deals review</title>
<style>
:root{--bg:#fbfaf7;--card:#fff;--ink:#1d1d1b;--muted:#6b6a64;--faint:#9a988f;--line:#e6e3da;--accent:#1f5fa8;--accent-bg:#e7f0fb;--ok:#2f6b12;--ok-bg:#eaf3de;--warn:#7a4a06;--warn-bg:#faeeda;--gray-bg:#f1efe8}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecebe6;--muted:#a8a69e;--faint:#7d7b74;--line:#34332f;--accent:#8db8ec;--accent-bg:#1b2f47;--ok:#a9d27a;--ok-bg:#24351a;--warn:#f0c27a;--warn-bg:#3d2c10;--gray-bg:#2a2a27}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 60px}h1{font-size:22px;font-weight:600;margin:0 0 4px}
.sub{color:var(--muted);font-size:13px;margin:0 0 20px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:18px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.stat b{display:block;font-size:22px;font-weight:600}.stat span{font-size:12px;color:var(--muted)}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
button,select,input[type=search]{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:6px 11px}
button{cursor:pointer}button.on{background:var(--accent-bg);color:var(--accent);border-color:var(--accent)}
input[type=search]{flex:1;min-width:180px}label{font-size:13px;color:var(--muted);display:flex;gap:6px;align-items:center}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px;min-width:860px}th{text-align:left;font-weight:600;color:var(--muted);padding:9px 10px;border-bottom:1px solid var(--line);cursor:pointer;white-space:nowrap;position:sticky;top:0;background:var(--card)}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}tr:last-child td{border-bottom:0}
.r{text-align:right;white-space:nowrap}.t{font-weight:500}.note{color:var(--faint);font-size:12px;margin-top:2px}
.pill{display:inline-block;font-size:11px;padding:2px 7px;border-radius:6px;white-space:nowrap}
.ok{background:var(--ok-bg);color:var(--ok)}.warn{background:var(--warn-bg);color:var(--warn)}.gray{background:var(--gray-bg);color:var(--muted)}
a.buy{color:var(--accent);text-decoration:none;white-space:nowrap}a.buy:hover{text-decoration:underline}
.big{font-weight:600}.foot{color:var(--muted);font-size:12px;margin-top:14px;line-height:1.7}
</style></head><body><main>
<h1>__TITLE__</h1>
<p class="sub">生成于 __GENERATED__ · 运营商、连锁零售店、Amazon.ie 三个模块 · 默认只显示有折扣的记录</p>
<div class="stats" id="stats"></div>
<div class="bar" id="mods"></div>
<div class="bar"><input type="search" id="q" placeholder="搜索商品或商家，如 Galaxy A27、Sony"><label><input type="checkbox" id="disc" checked>只看有折扣</label><label><input type="checkbox" id="list">只看自动上线</label></div>
<div class="wrap"><table><thead><tr><th data-k="module_name">来源</th><th data-k="title">商品 / 优惠</th><th data-k="price_minor" class="r">价格</th><th data-k="reference_minor" class="r">对比价</th><th data-k="discount_percent" class="r">降幅</th><th data-k="status">状态</th><th>购买链接</th></tr></thead><tbody id="tb"></tbody></table></div>
<p class="foot" id="count"></p>
<p class="foot">对比价不是统一的“原价”：运营商是同运营商非转网价或非会员价；零售店是零售商自己的 Was 价或制造商 RRP；Amazon 是第三方折扣网站标注的参考价。<br>
Amazon 价格全部来自第三方折扣网站，未经 Amazon 官方核实。按 Amazon Associates 规则，对外发布的价格应来自 Amazon 官方接口并注明时间，这一列只供内部挑选，不能直接用于公开页面。Amazon 链接已带 Associates tag；运营商和零售店链接是官网原链接，没有联盟参数。<br>
“可展示”只对运营商模块成立；零售店和 Amazon 记录目前都需要人工审核。</p>
</main>
<script>
const D=__DATA__;
const MOD={operator:"运营商优惠",retailer:"连锁零售店",amazon:"Amazon.ie"};
const ST={active:["有效","ok"],active_end_unknown:["有效·无截止日","ok"],needs_review:["待审核","warn"],unconfirmed:["本次未确认","gray"],stale:["已过期","gray"],price_only:["仅现价","gray"],web_candidate:["第三方候选","warn"],conflicting_quotes:["报价冲突","warn"]};
let mod="all",sk="discount_percent",sd=-1;
const eur=m=>m==null?"—":"€"+(m/100).toFixed(2).replace(/\\.00$/,"");
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function stats(){const g=m=>D.filter(x=>x.module===m);const el=document.getElementById("stats");
const pub=m=>g(m).filter(x=>x.publish!==false).length;el.innerHTML=[["operator","自动上线 "+pub("operator")+" 条"],["retailer","自动上线 "+pub("retailer")+" 条 · 有折扣 "+g("retailer").filter(x=>x.discount_percent!=null&&x.publish!==false).length],["amazon","自动上线 "+pub("amazon")+" 条（带联盟链接）"]].map(([m,s])=>`<div class="stat"><span>${MOD[m]}</span><b>${g(m).length}</b><span>${s}</span></div>`).join("");}
function mods(){document.getElementById("mods").innerHTML=["all","operator","retailer","amazon"].map(m=>`<button data-m="${m}" class="${m===mod?"on":""}">${m==="all"?"全部":MOD[m]}</button>`).join("");
document.querySelectorAll("#mods button").forEach(b=>b.onclick=()=>{mod=b.dataset.m;mods();render()});}
function render(){const q=document.getElementById("q").value.trim().toLowerCase(),disc=document.getElementById("disc").checked,lst=document.getElementById("list").checked;
let r=D.filter(x=>(mod==="all"||x.module===mod)&&(!disc||x.discount_percent!=null)&&(!lst||x.publish!==false)&&(!q||(x.title+" "+x.provider).toLowerCase().includes(q)));
r.sort((a,b)=>{let A=a[sk],B=b[sk];if(A==null&&B==null)return 0;if(A==null)return 1;if(B==null)return -1;return(A>B?1:A<B?-1:0)*sd});
document.getElementById("tb").innerHTML=r.map(x=>{const s=ST[x.status]||[x.status,"gray"];const sav=x.price_minor!=null&&x.reference_minor!=null?`<div class="note">省 ${eur(x.reference_minor-x.price_minor)}</div>`:"";
const end=x.ends_on?`<div class="note">截止 ${x.ends_on}</div>`:"";
return `<tr><td>${MOD[x.module]}<div class="note">${esc(x.provider)}</div></td><td><div class="t">${esc(x.title)}</div><div class="note">${esc(x.who)} · ${esc(x.price_basis)}${x.notes?" · "+esc(x.notes):""}${x.publish===false?" · 未上线："+esc(x.publish_reason):""}</div></td><td class="r big">${eur(x.price_minor)}</td><td class="r">${eur(x.reference_minor)}<div class="note">${esc(x.reference_label||"")}</div></td><td class="r big">${x.discount_percent==null?"—":Math.round(x.discount_percent)+"%"}${sav}</td><td><span class="pill ${s[1]}">${s[0]}</span>${end}</td><td>${x.buy_url?`<a class="buy" href="${esc(x.buy_url)}" target="_blank" rel="noopener sponsored">${x.price_minor==null&&x.module==="amazon"?"查看 Amazon 价格":"去购买"} ↗</a><div class="note">${esc(x.link_type)}</div>`:"—"}</td></tr>`}).join("");
document.getElementById("count").textContent="显示 "+r.length+" / "+D.length+" 条。点表头排序。";}
document.querySelectorAll("th[data-k]").forEach(th=>th.onclick=()=>{const k=th.dataset.k;if(sk===k)sd=-sd;else{sk=k;sd=["title","module_name","status"].includes(k)?1:-1}render()});
["q","disc","list"].forEach(id=>document.getElementById(id).addEventListener("input",render));
stats();mods();render();
</script></body></html>
"""


def auto_publish(r, raw, now):
    """Rule-based publication, no human step. Returns (publish, reason) and may hide price fields."""
    fresh = r.get("fresh_until")
    if fresh and datetime.fromisoformat(fresh.replace("Z", "+00:00")) < now:
        return False, "数据超过有效期"
    if r["status"] in ("unconfirmed", "stale", "expired", "scheduled"):
        return False, "本次未确认或不在有效期"
    if r["module"] == "operator":
        return (True, "运营商官网及条款核对通过") if r["can_list"] else (False, "条款或资格不完整")
    if r["module"] == "retailer":
        v = (raw or {}).get("verification", {})
        if v.get("method") != "direct_official_html":
            return False, "非本次官网直接读取（快照）"
        if any("was_price_claim_needs_review" in i for i in v.get("issues", [])):
            r.update(reference_minor=None, discount_percent=None, reference_label="已隐藏：前 30 天观测到更低价")
        return True, "零售商官网本次直接读取"
    if r["module"] == "amazon":
        if not r.get("price_public"):
            r.update(price_minor=None, reference_minor=None, discount_percent=None,
                     reference_label=None, price_basis="查看 Amazon 当前价格")
            return True, "商品与联盟链接；价格待 Amazon 官方 API"
        return True, "Amazon 商品页 / 官方 API 价格"
    return False, "未知模块"


PUBLIC_FIELDS = ("id", "module", "module_name", "provider", "title", "category", "price_minor", "reference_minor",
                 "reference_label", "discount_percent", "price_basis", "who", "ends_on", "status", "fresh_until",
                 "buy_url", "terms_url", "link_type", "asin")


def build_rows():
    """Internal rows (everything) with an automatic `publish` decision and the `public_view` to show publicly."""
    rows = operator_rows() + retailer_rows() + amazon_rows()
    raw = {}
    for key in ("prepay-offers-service/data/offers.json", "retailer-offers-service/data/offers.json", "amazon-offers-service/web-data/offers.json"):
        for o in load(key).get("offers", []):
            raw[o["id"]] = o
    now = datetime.now(timezone.utc)
    for r in rows:
        r.setdefault("price_public", True)
        shadow = dict(r)
        r["publish"], r["publish_reason"] = auto_publish(shadow, raw.get(r["id"]), now)
        r["public_view"] = {k: shadow.get(k) for k in PUBLIC_FIELDS} if r["publish"] else None
    assert len({r["id"] for r in rows}) == len(rows)
    return rows


def render_page(rows, generated, public=False):
    if public:
        rows = [r["public_view"] for r in rows if r.get("publish")]
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    title = "Mobile123 爱尔兰优惠" if public else "Mobile123 优惠汇总（内部视图）"
    return (PAGE.replace("__DATA__", data).replace("__TITLE__", title)
            .replace("__GENERATED__", html.escape(generated.replace("T", " ").replace("+00:00", " UTC"))))


def main(out=OUT):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rows = build_rows()
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "deals.json").write_text(json.dumps({"generated_at": generated, "count": len(rows), "deals": rows}, ensure_ascii=False, indent=1) + "\n")
    with (out / "deals.csv").open("w", encoding="utf-8-sig", newline="") as f:
        cols = ["module_name", "provider", "title", "category", "price_minor", "reference_minor", "reference_label", "discount_percent",
                "status", "publish", "publish_reason", "can_list", "who", "ends_on", "price_basis", "buy_url", "link_type", "notes"]
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([(f"{r[c]/100:.2f}" if c.endswith("_minor") and r[c] is not None else r.get(c, "")) for c in cols])
    (out / "index.html").write_text(render_page(rows, generated))
    counts = {m: sum(r["module"] == m for r in rows) for m in ("operator", "retailer", "amazon")}
    summary = {"total": len(rows), **counts, "with_discount": sum(r["discount_percent"] is not None for r in rows)}
    print(json.dumps(summary))
    return rows, generated


if __name__ == "__main__":
    main()
