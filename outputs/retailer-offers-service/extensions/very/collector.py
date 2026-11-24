"""Bounded public Constructor reads; no paid scraping service, browser, or LLM."""
import concurrent.futures
import hashlib
import json
import math
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .adapter import CATEGORIES, normalize, timestamp, validate_payload

CLIENT_URL = 'https://cnstrc.com/js/cust/the-very-group_MgRYWn.js'
API_ORIGIN = 'https://wpkmgeuco-zone.cnstrc.com'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


def validate_config(config):
    if config['client_config_url'] != CLIENT_URL or config['api_origin'] != API_ORIGIN:
        raise ValueError('Unapproved Constructor endpoint')
    limits = {'page_size': (1, 200), 'max_pages_per_category': (1, 10), 'max_workers': (1, 3),
              'timeout_seconds': (1, 35), 'max_attempts': (1, 2), 'freshness_hours': (1, 48)}
    for key, (low, high) in limits.items():
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError('Invalid limit: ' + key)
    if config['minimum_discount_percent_exclusive'] != 20:
        raise ValueError('Very collection requires discount strictly greater than 20%')
    categories = config['categories']
    if not isinstance(categories, list) or not 1 <= len(categories) <= 5:
        raise ValueError('Expected 1–5 Very categories')
    seen = set()
    groups = {'phones': 'technology-mobile-phones', 'computers': 'technology-laptops',
              'tablets': 'technology-tablets', 'monitors': 'technology-pc-monitors', 'storage': 'technology-data-storage'}
    for category in categories:
        cat = category['category']
        if cat not in CATEGORIES or cat in seen or category['group_id'] != groups[cat]:
            raise ValueError('Unexpected or duplicate category')
        seen.add(cat)
    return config


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).netloc != urllib.parse.urlparse(req.full_url).netloc:
            raise ValueError('Cross-host redirect rejected')
        if urllib.parse.urlparse(newurl).scheme != 'https':
            raise ValueError('Non-HTTPS redirect rejected')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Client:
    def __init__(self, config, cache, offline=False):
        self.config, self.cache, self.offline = validate_config(config), Path(cache), offline
        self.cache.mkdir(parents=True, exist_ok=True)
        cert = Path('/etc/ssl/cert.pem')
        context = ssl.create_default_context(cafile=str(cert) if cert.exists() else None)
        self.context = context

    def read(self, url, cache_id):
        p = urllib.parse.urlparse(url)
        if not (url == CLIENT_URL or (p.scheme == 'https' and p.netloc == urllib.parse.urlparse(API_ORIGIN).netloc
                                     and re.fullmatch(r'/browse/group_id/technology-[a-z-]+', p.path))):
            raise ValueError('Unapproved request URL')
        body_path, meta_path = self.cache/(cache_id+'.body'), self.cache/(cache_id+'.meta.json')
        if self.offline:
            raw, meta = body_path.read_bytes(), json.loads(meta_path.read_text())
            if meta['url'] != url or hashlib.sha256(raw).hexdigest() != meta['sha256']:
                raise ValueError('Cached evidence identity/hash mismatch')
            timestamp(meta['fetched_at'])
            return raw, {**meta, 'replayed': True}
        started = time.monotonic()
        for attempt in range(1, self.config['max_attempts']+1):
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'DealRadar/1.0 (public product catalogue collector)', 'Accept': 'application/json, application/javascript'})
                opener = urllib.request.build_opener(SafeRedirect(), urllib.request.HTTPSHandler(context=self.context))
                with opener.open(request, timeout=self.config['timeout_seconds']) as response:
                    raw = response.read(6_000_001)
                    if response.status != 200 or len(raw) > 6_000_000 or not raw:
                        raise ValueError('Invalid response size or status')
                meta = {'url': url, 'fetch_status': 'ok', 'http_status': 200,
                        'fetched_at': datetime.now(timezone.utc).isoformat(), 'sha256': hashlib.sha256(raw).hexdigest(),
                        'bytes': len(raw), 'attempts': attempt, 'duration_seconds': round(time.monotonic()-started, 4)}
                body_path.write_bytes(raw)
                write_json(meta_path, meta)
                return raw, meta
            except urllib.error.HTTPError as exc:
                retryable = exc.code in (429, 500, 502, 503, 504)
                retry_after = exc.headers.get('Retry-After', '0')
                wait = float(retry_after) if retry_after.isdigit() else (4 if retry_after else 0)
                if not retryable or attempt == self.config['max_attempts'] or wait > 3:
                    raise
                time.sleep(max(wait, .3*attempt))
            except (urllib.error.URLError, TimeoutError):
                if attempt == self.config['max_attempts']:
                    raise
                time.sleep(.3*attempt)
        raise RuntimeError('Unreachable request state')

    def bootstrap(self):
        # Public frontend key only; never requires an owner credential or admin token.
        raw, meta = self.read(CLIENT_URL, 'very-client')
        key = re.search(r'veryie:\{production:"(key_[A-Za-z0-9]+)"', raw.decode())
        if not key:
            raise ValueError('Very.ie production index missing from public client config')
        return key[1], meta

    def category(self, source, key, now):
        cat, group = source['category'], source['group_id']
        rows, rejected, sources, seen_products = [], [], [], set()
        size, complete = self.config['page_size'], False
        expected = None
        for page in range(1, self.config['max_pages_per_category']+1):
            sid = f'very-{cat}-p{page}'
            query = urllib.parse.urlencode({'key': key, 'section': 'Products', 'page': page,
                                            'num_results_per_page': size, 'filters[offers]': 'all offers'})
            url = API_ORIGIN+'/browse/group_id/'+group+'?'+query
            try:
                raw, meta = self.read(url, sid)
                payload = json.loads(raw)
                response = validate_payload(payload)
                total, results = response['total_num_results'], response['results']
                if expected is None:
                    expected = total
                if len(results) > size:
                    raise ValueError('Endpoint ignored page size')
                ids = [str(item['data'].get('id')) for item in results]
                if len(ids) != len(set(ids)) or any(pid in seen_products for pid in ids):
                    raise ValueError('Repeated product/page: pagination is not advancing')
                seen_products.update(ids)
                parsed, excluded = normalize(payload, cat, sid, meta['fetched_at'], now, self.config)
                rows.extend(parsed)
                rejected.extend(excluded)
                sources.append({'id': sid, 'provider_id': 'very', **meta, 'adapter_status': 'ok',
                                'page': page, 'cards_seen': len(results), 'variants_admitted': len(parsed), 'total_products': total})
                # Cover the earliest reported total and current total before claiming complete.
                if page*size >= max(total, expected):
                    complete = len(seen_products) >= max(total, expected)
                    break
                if len(results) < size:
                    raise ValueError('Unexpected short page before catalogue end')
            except Exception as exc:
                sources.append({'id': sid, 'provider_id': 'very', 'url': url, 'fetch_status': 'failed',
                                'adapter_status': 'failed', 'page': page, 'error': str(exc)[:250]})
                break
        coverage = {'category': cat, 'observed_products': len(seen_products), 'reported_products': expected,
                    'complete': complete, 'page_cap': self.config['max_pages_per_category']}
        return rows, rejected, sources, coverage

    def collect(self, now):
        key, bootstrap = self.bootstrap()
        rows, rejected, sources, coverage = [], [], [], []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.config['max_workers']) as pool:
            futures = [pool.submit(self.category, category, key, now) for category in self.config['categories']]
            for future in concurrent.futures.as_completed(futures):
                r, e, s, c = future.result()
                rows.extend(r); rejected.extend(e); sources.extend(s); coverage.append(c)
        return rows, rejected, sorted(sources, key=lambda s:s['id']), sorted(coverage, key=lambda c:c['category']), bootstrap
