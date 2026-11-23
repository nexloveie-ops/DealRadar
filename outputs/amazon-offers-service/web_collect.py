#!/usr/bin/env python3
"""Repeatable, public-HTML Amazon.ie deal discovery. No Amazon or search API required."""
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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from collect import ROOT, ASIN, affiliate_link, money, validate_config, write_json

HOSTS = {"www.goodcraicdeals.ie", "item.ie", "www.amazon.ie", "dealdepot.ie"}


def fetch(url, cache, offline=False):
    p = urlparse(url)
    if p.scheme != "https" or p.hostname not in HOSTS:
        raise ValueError("Unapproved public source")
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    raw_path, meta_path = cache/(key+'.html'), cache/(key+'.json')
    if offline:
        meta = json.loads(meta_path.read_text())
        raw = raw_path.read_bytes() if meta['fetch_status']=='ok' else None
        if raw and hashlib.sha256(raw).hexdigest() != meta['sha256']:
            raise ValueError("Evidence hash mismatch")
        return raw, {**meta,'replayed':True}
    tmp = cache/(key+'.tmp')
    r = subprocess.run(['curl','--silent','--show-error','--location','--max-redirs','3','--proto','=https',
        '--proto-redir','=https','--connect-timeout','10','--max-time','30','--max-filesize','10000000',
        '--output',str(tmp),'--write-out','%{http_code}\n%{url_effective}',url],capture_output=True,text=True,timeout=35)
    parts=r.stdout.splitlines()
    code=int(parts[0]) if parts and parts[0].isdigit() else 0
    meta={'url':url,'fetched_at':datetime.now(timezone.utc).isoformat(),'http_status':code,
        'fetch_status':'ok' if r.returncode==0 and code==200 else 'blocked' if code in (401,403,429) else 'failed'}
    raw=None
    if meta['fetch_status']=='ok':
        if len(parts)<2 or urlparse(parts[1]).hostname not in HOSTS:
            raise ValueError('Unexpected redirect')
        raw=tmp.read_bytes()
        if len(raw)<100 or b'<html' not in raw.lower():
            raise ValueError('Non-HTML response')
        soup=BeautifulSoup(raw,'html.parser')
        title=soup.title.get_text(' ',strip=True) if soup.title else ''
        if re.search(r'robot check|captcha|access denied|just a moment|automated access',title,re.I) or soup.select_one('input#captchacharacters'):
            meta['fetch_status']='blocked'
            raw=None
        else:
            tmp.replace(raw_path)
            meta.update(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    tmp.unlink(missing_ok=True)
    write_json(meta_path,meta)
    return raw,meta


def hydrate(raw):
    soup=BeautifulSoup(raw,'html.parser')
    for script in soup.find_all('script'):
        text=script.string or script.get_text()
        m=re.search(r'window\.__TSR_DEHYDRATED__\s*=\s*',text)
        if m:
            data,_=json.JSONDecoder().raw_decode(text[m.end():])
            return data,soup
    raise ValueError('Good Craic public embedded JSON missing; adapter requires review')


def parse_gcd(raw,url,meta):
    data,_=hydrate(raw)
    rows,page,seen=[],None,set()
    for q in data.get('queryClientState',{}).get('queries',[]):
        key=q.get('queryKey',[])
        if not key or key[0] not in ('deal','deals'):
            continue
        payload=q.get('state',{}).get('data')
        if key[0]=='deals':
            page=key[1].get('page') if len(key)>1 else None
            batches=payload.get('pages',[]) if isinstance(payload,dict) else []
            deals=[d for batch in batches if isinstance(batch,list) for d in batch]
        else:
            deals=[payload] if isinstance(payload,dict) else []
        for d in deals:
            if d.get('id') in seen:
                continue
            seen.add(d.get('id'))
            # Retain deal facts only; no user profiles, descriptions, comments or image bytes.
            rows.append({'source_id':'gcd:'+str(d.get('id')),'source_url':'https://www.goodcraicdeals.ie/deals/'+d.get('slug',''),
                'source_kind':'goodcraic','title':d.get('title',''),'amazon_url':d.get('url'),
                'retailer_domain':d.get('retailerDomain'),'category':d.get('category'),
                'price':d.get('discountedPrice'),'reference':d.get('usualPrice'),
                'reference_type':'third_party_unspecified_reference','source_status':d.get('status'),
                'is_expired':d.get('isExpired',False),'reported_outdated':d.get('reportedOutdated',False),
                'source_updated_at':d.get('updatedAt'),'source_quote_checked_at':None,
                'source_starts_at':d.get('startDate'),'source_ends_at':d.get('endDate'),
                'fetched_at':meta['fetched_at'],'condition_claim':None})
    return rows,page


def parse_item(raw,url,meta):
    soup=BeautifulSoup(raw,'html.parser')
    cards=soup.select('article[data-spotted-card]')
    if not cards:
        raise ValueError('Item.ie spotted-card structure missing; adapter requires review')
    rows=[]
    for card in cards:
        title=card.select_one('h3')
        link=card.select_one('a[href*="amazon.ie/dp/"]')
        if not title or not link:
            continue
        label_node=next((p for p in card.select('p') if 'Amazon’s label:' in p.get_text()),None)
        label=label_node.get_text(' ',strip=True) if label_node else ''
        match=re.search(r'(RRP|Lowest|Was|List price)\s*€\s*([\d,]+(?:\.\d{1,2})?)',label,re.I)
        checked=card.select_one('time[datetime]')
        rows.append({'source_id':'item:'+card.get('id',''),'source_url':url+'#'+card.get('id',''),
            'source_kind':'item_spotted','title':title.get_text(' ',strip=True),'amazon_url':link['href'],
            'retailer_domain':'amazon.ie','category':None,'price':card.get('data-price'),
            'reference':match[2].replace(',','') if match else None,
            'reference_type':'third_party_amazon_label_'+match[1].lower().replace(' ','_') if match else None,
            'source_status':'observed','is_expired':False,'reported_outdated':False,
            'source_updated_at':None,'source_quote_checked_at':checked['datetime'] if checked else None,
            'source_starts_at':None,'source_ends_at':None,'source_quote_expires_at':card.get('data-expires'),
            'fetched_at':meta['fetched_at'],'condition_claim':None})
    return rows


def parsed_time(value):
    if not value:
        return None
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:
        raise ValueError('Timestamp needs timezone')
    return d


def parse_dealdepot(raw,url,meta):
    soup=BeautifulSoup(raw,'html.parser')
    cards=soup.select('article.dd-card-list')
    if not cards:raise ValueError('DealDepot product-list cards missing; adapter needs review')
    rows=[]
    for card in cards:
        title=card.select_one('.dd-card-title a')
        link=card.select_one('.dd-card-cta a[href*="amazon.ie/dp/"]')
        if not title or not link:continue
        pricing=card.select_one('.dd-card-pricing')
        now_node=pricing.select_one('.dd-now') if pricing else None
        was_node=pricing.select_one('del') if pricing else None
        now_match=re.fullmatch(r'€\s*([\d,]+(?:\.\d{1,2})?)',now_node.get_text(' ',strip=True)) if now_node else None
        was_match=re.fullmatch(r'€\s*([\d,]+(?:\.\d{1,2})?)',was_node.get_text(' ',strip=True)) if was_node else None
        expired=bool(re.search(r'\bexpired\b|deal ended',card.get_text(' ',strip=True),re.I))
        rows.append({'source_id':'dealdepot:'+title['href'],'source_url':title['href'],'source_kind':'dealdepot',
            'title':title.get_text(' ',strip=True),'amazon_url':link['href'],'retailer_domain':'amazon.ie','category':None,
            # Only an explicit two-price pair can be considered; hidden prices remain missing.
            'price':now_match[1].replace(',','') if now_match else None,
            'reference':was_match[1].replace(',','') if was_match else None,
            'reference_type':'third_party_unspecified_reference','source_status':'observed','is_expired':expired,'reported_outdated':False,
            'source_updated_at':None,'source_quote_checked_at':None,'source_starts_at':None,'source_ends_at':None,
            'fetched_at':meta['fetched_at'],'condition_claim':None})
    return rows


def source_page_url(source,page):
    p=urlparse(source['url']);q=dict(parse_qsl(p.query))
    if source['paginate']:
        if source['adapter']=='dealdepot':
            p=p._replace(path='/store/amazon-ie/'+('page/'+str(page)+'/' if page>1 else ''))
        else:q['page']=str(page)
    return urlunparse(p._replace(query=urlencode(q)))


def keyword_matches(title,c):
    t=title.casefold()
    found=[]
    for k in c['keywords']:
        if not k['enabled']:
            continue
        aliases=[k['query']]+k.get('aliases',[])
        if k['id']=='fire-tv':aliases+=['fire tv stick','firestick','fire stick']
        if any(re.search(r'(?<!\w)'+re.escape(a.casefold())+r'(?!\w)',t) for a in aliases):
            found.append(k['id'])
    return found


def normalise(row,c,settings,now):
    title=row['title']
    matches=keyword_matches(title,c)
    if not matches:
        raise ValueError('keyword_not_matched')
    if re.search(r'\brenewed\b|\brefurb(?:ished)?\b|\bused\b|\bpre[ -]?owned\b|open[ -]?box|ex[ -]?display',title,re.I):
        raise ValueError('not_new')
    if row['is_expired'] or row['reported_outdated'] or row['source_status'] not in ('APPROVED','observed'):
        raise ValueError('expired_or_reported_outdated')
    url=row.get('amazon_url') or ''
    p=urlparse(url)
    if p.scheme!='https' or p.hostname not in ('amazon.ie','www.amazon.ie'):
        raise ValueError('not_amazon_ie')
    asin_match=re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)',p.path,re.I)
    if not asin_match:
        raise ValueError('not_a_single_asin;bundle_or_search_page')
    if row.get('category') and row['category']!='ELECTRONICS' and not (
        row['category']=='VIDEOGAMES' and re.search(r'\b(?:controller|console|headset|keyboard|mouse|ps5|playstation 5|xbox series)\b',title,re.I)
        and not re.search(r'\bdigital\b|\bgame code\b|\bgame key\b',title,re.I)):
        raise ValueError('not_electronics')
    if row.get('price') is None or row.get('reference') is None:
        raise ValueError('missing_price_pair')
    price,reference=money(row['price']),money(row['reference'])
    if price<=0 or reference<=price:
        raise ValueError('invalid_price_pair')
    exact=Decimal(reference-price)*100/reference
    if exact<=Decimal(str(c['minimum_discount_percent'])):
        raise ValueError('discount_not_strictly_above_threshold')
    source_time=parsed_time(row.get('source_quote_checked_at')) or parsed_time(row.get('source_updated_at'))
    if source_time and (source_time>now+timedelta(minutes=5) or now-source_time>timedelta(days=settings['maximum_source_age_days'])):
        raise ValueError('source_timestamp_too_old_or_future')
    end=parsed_time(row.get('source_ends_at'))
    start=parsed_time(row.get('source_starts_at'))
    quote_expiry=parsed_time(row.get('source_quote_expires_at'))
    if quote_expiry and quote_expiry<=now:
        raise ValueError('source_quote_expired')
    if end and end<=now or start and start>now:
        raise ValueError('promotion_not_current_at_source')
    asin=asin_match[1].upper()
    tag_url=affiliate_link(asin,c['associate_tag'])
    coupon=re.search(r'(?:code|use|with)\s+([A-Z][A-Z0-9]{3,15})\b',title)
    needs_code=bool(coupon or re.search(r'\bcoupon\b|\bcode\b|\bvoucher\b',title,re.I))
    issues=['third_party_quote;current_amazon_price_not_verified','new_condition_not_confirmed','seller_stock_shipping_and_variant_not_verified']
    if row['source_kind']=='goodcraic':issues.append('source_update_is_not_a_verified_price_check')
    if needs_code:issues.append('conditional_price;coupon_eligibility_not_verified')
    return {'id':'ie-amazon-'+asin,'module_id':'amazon_promotions','provider_id':'amazon_ie','provider_name':'Amazon.ie',
        'country':'IE','currency':'EUR','offer_kind':'product_discount',
        'product':{'asin':asin,'name':title,'condition':'unconfirmed','seller':None},
        'pricing':{'device_price_minor':price,'reference_price_minor':reference,'reference_type':row['reference_type'],
            'reference_label':'Third-party reference; not a confirmed Amazon ordinary price',
            'discount_minor':reference-price,'discount_percent':float(exact.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)),
            'discount_exact_fraction':{'saving_minor':reference-price,'reference_minor':reference},
            'includes_delivery':False,'delivery_cost_minor':None},
        'eligibility':{'prime_required':None,'coupon_required':needs_code if needs_code else None,
            'coupon_code':coupon[1] if coupon else None,'conditions':['Check new condition, variant, seller, delivery and any coupon on Amazon.ie.']},
        'stock_status':'unknown','status':'web_candidate','matched_keyword_ids':matches,
        'validity':{'starts_on':row.get('source_starts_at'),'ends_on':row.get('source_ends_at')},
        'verification':{'method':'public_html_third_party','verified_at':None,'expires_at':None,
            'discovered_at':row['fetched_at'],'fresh_until':(parsed_time(row['fetched_at'])+timedelta(hours=settings['freshness_hours'])).isoformat(),
            'source_quote_checked_at':row.get('source_quote_checked_at'),'source_quote_expires_at':row.get('source_quote_expires_at'),'source_updated_at':row.get('source_updated_at'),
            'issues':issues,'amazon_page_check':{'status':'not_checked','checked_at':None,'http_status':None}},
        'source_ids':[row['source_id']],'sources':[{'id':row['source_id'],'url':row['source_url'],'kind':row['source_kind'],
            'fetched_at':row['fetched_at'],'quote_checked_at':row.get('source_quote_checked_at'),'updated_at':row.get('source_updated_at'),
            'price_minor':price,'reference_price_minor':reference,'reference_type':row['reference_type']}],
        'links':{'landing_url':tag_url,'affiliate_url':tag_url,'tag':c['associate_tag']},
        'media':{'image_url':None,'store_image_bytes':False,'publish_image':False},
        'distribution':{'can_list':False,'notification_ready':False}}


def consolidate(rows,now):
    by_asin={}
    for row in rows:
        old=by_asin.get(row['id'])
        if old is None:
            by_asin[row['id']]=row
            continue
        old['matched_keyword_ids']=sorted(set(old['matched_keyword_ids']+row['matched_keyword_ids']))
        for source in row['sources']:
            if source['id'] not in old['source_ids']:
                old['source_ids'].append(source['id']);old['sources'].append(source)
        if old['pricing']['device_price_minor']!=row['pricing']['device_price_minor'] or old['pricing']['reference_price_minor']!=row['pricing']['reference_price_minor']:
            old['status']='conflicting_quotes'
            old['verification']['issues']=sorted(set(old['verification']['issues']+['conflicting_source_quotes;do_not_choose_largest_discount']))
    for row in by_asin.values():
        if parsed_time(row['verification']['fresh_until'])<=now:
            row['status']='stale'
    return sorted(by_asin.values(),key=lambda o:(-Decimal(o['pricing']['discount_minor'])/Decimal(o['pricing']['reference_price_minor']),o['id']))


def check_amazon_page(row,cache,offline,settings):
    # Bounded ordinary reads only. No login, proxy, cookies, CAPTCHA workarounds or cart actions.
    url='https://www.amazon.ie/dp/'+row['product']['asin']
    raw,meta=fetch(url,cache,offline)
    status='blocked' if meta['fetch_status']=='blocked' else 'unavailable'
    if raw:
        soup=BeautifulSoup(raw,'html.parser')
        box=soup.select_one('#buybox')
        status='price_hidden_requires_cart' if box and re.search(r'add this item to your basket',box.get_text(' ',strip=True),re.I) else 'accessible_needs_manual_offer_review'
    row['verification']['amazon_page_check']={'status':status,'checked_at':meta['fetched_at'],'http_status':meta['http_status']}
    # Accessible HTML is not proof of same variant, new offer, seller or current price.
    return status


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',required=True)
    parser.add_argument('--out',default=str(ROOT/'web-data'))
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--as-of',help='Timezone-aware timestamp for reproducible replay')
    args=parser.parse_args()
    now=parsed_time(args.as_of) if args.as_of else datetime.now(timezone.utc)
    started=time.monotonic()
    c=json.loads((ROOT/'config.json').read_text());validate_config(c)
    settings=json.loads((ROOT/'web-sources.json').read_text())
    cache,out=Path(args.cache),Path(args.out);cache.mkdir(parents=True,exist_ok=True);out.mkdir(parents=True,exist_ok=True)
    previous=json.loads((out/'offers.json').read_text())['offers'] if (out/'offers.json').exists() else []
    observations,rejected,reports=[],[],[]
    for source in settings['sources']:
        previous_signature=None
        for page in range(1,(source.get('pages',settings['pages_per_directory']) if source['paginate'] else 1)+1):
            url=source_page_url(source,page)
            meta={'url':url,'source_id':source['id'],'page':page}
            try:
                raw,fetched=fetch(url,cache,args.offline);meta.update(fetched)
                if not raw:
                    raise ValueError('source_fetch_'+meta['fetch_status'])
                if source['adapter']=='goodcraic':
                    rows,actual=parse_gcd(raw,url,meta)
                    if source['paginate'] and actual!=page:raise ValueError('pagination_not_honoured')
                elif source['adapter']=='item_spotted':rows=parse_item(raw,url,meta)
                elif source['adapter']=='dealdepot':rows=parse_dealdepot(raw,url,meta)
                else:raise ValueError('Unknown adapter')
                signature=tuple(row['source_id'] for row in rows)
                if signature and signature==previous_signature:raise ValueError('duplicate_page_content')
                previous_signature=signature
                observations.extend(rows);meta.update(adapter_status='ok',observed_rows=len(rows))
                if source['adapter']=='goodcraic' and len(rows)<25:
                    reports.append(meta);break
            except (ValueError,KeyError,TypeError,subprocess.TimeoutExpired,OSError) as exc:
                meta.update(adapter_status='failed',error=str(exc)[:180]);reports.append(meta);break
            reports.append(meta)
            print(source['id']+' page '+str(page)+': '+meta.get('adapter_status','unknown'),flush=True)
            if not args.offline:time.sleep(settings['delay_seconds'])
    unique={}
    for row in observations:unique.setdefault(row['source_id'],row)
    accepted=[]
    for row in unique.values():
        try:accepted.append(normalise(row,c,settings,now))
        except (ValueError,KeyError,TypeError) as exc:
            rejected.append({'source_id':row['source_id'],'source_url':row['source_url'],'title':row['title'],'reason':str(exc)[:160]})
    offers=consolidate(accepted,now)
    checks=[]
    for row in offers[:settings['max_amazon_checks']]:
        try:
            status=check_amazon_page(row,cache,args.offline,settings)
            checks.append({'asin':row['product']['asin'],'status':status})
            if status=='blocked':break
        except (ValueError,KeyError,OSError,subprocess.TimeoutExpired) as exc:
            checks.append({'asin':row['product']['asin'],'status':'failed','error':str(exc)[:100]})
        if not args.offline:time.sleep(settings['delay_seconds'])
    newids={o['id'] for o in offers}
    missing=[{'id':o['id'],'status':'not_observed','send_notification':False} for o in previous if o['id'] not in newids]
    # Missing old rows do not enter the current candidate file; no fabricated expiry is asserted.
    result={'schema_version':'1.0.0','module_id':'amazon_promotions','module_name':c['module_name'],
        'generated_at':now.isoformat(),'mode':'public_html_no_api','scope':{'marketplace':'www.amazon.ie','minimum_discount_percent':c['minimum_discount_percent'],
            'discount_comparison':'>','condition':'new_only;unconfirmed candidates held for review','full_catalogue':False},'offers':offers}
    from jsonschema import Draft202012Validator, FormatChecker
    Draft202012Validator(json.loads((ROOT/'web-offers.schema.json').read_text()),format_checker=FormatChecker()).validate(result)
    keyword_counts={k['id']:sum(k['id'] in o['matched_keyword_ids'] for o in offers) for k in c['keywords'] if k['enabled']}
    counts={'source_pages':len(reports),'unique_source_posts':len(unique),'matched_candidates':len(offers),
        'verified_new_offers':0,'excluded_source_posts':len(rejected),'by_keyword':keyword_counts}
    report={'generated_at':now.isoformat(),'duration_seconds':round(time.monotonic()-started,2),'mode':'public_html_no_api','offline':args.offline,
        'amazon_api_calls':0,'llm_calls':0,'llm_tokens':0,'counts':counts,'sources':reports,'amazon_page_checks':checks,
        'limitations':['Third-party quotes only; no current Amazon offer is independently verified.',
            'Source update times are not price-check times. Exact ASINs are not merged across variants.',
            'No publication, notifications, copyrighted images or Amazon price-history service created.']}
    for filename,payload in [('offers.json',result),('run-report.json',report),('excluded.json',{'records':rejected}),('changes.json',{'events':missing})]:write_json(out/filename,payload)
    with (out/'candidates.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['asin','product','source_price_eur','source_reference_eur','reference_type','discount_percent','new_condition','status','source_updated_at','source_quote_checked_at','affiliate_url'])
        for o in offers:
            pr=o['pricing'];v=o['verification'];w.writerow([o['product']['asin'],o['product']['name'],f'{pr["device_price_minor"]/100:.2f}',f'{pr["reference_price_minor"]/100:.2f}',pr['reference_type'],pr['discount_percent'],o['product']['condition'],o['status'],v['source_updated_at'],v['source_quote_checked_at'],o['links']['affiliate_url']])
    print(json.dumps({'duration_seconds':report['duration_seconds'],**counts},ensure_ascii=False))
    return 2 if any(r.get('adapter_status')!='ok' for r in reports) else 0


if __name__=='__main__':
    raise SystemExit(main())
