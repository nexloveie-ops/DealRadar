#!/usr/bin/env python3
"""Very.ie bulk discount collector; independent state and failures, shared retailer data format."""
import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from extensions.very.adapter import extended_schema, reconcile
from extensions.very.collector import Client, validate_config, write_json

ROOT = Path(__file__).resolve().parent


def run(config, cache, out, offline=False, now=None, disabled=False):
    started = time.monotonic()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Timezone-aware date required')
    cache, out = Path(cache).resolve(), Path(out).resolve()
    protected = [ROOT/'data', ROOT/'hn-data', ROOT/'data-with-harvey-norman']
    if out == ROOT or any(out == p or p in out.parents for p in protected) or out == cache or out in cache.parents or cache in out.parents:
        raise ValueError('Use independent Very output and evidence directories')
    validate_config(config)
    previous = json.loads((out/'offers.json').read_text())['offers'] if (out/'offers.json').exists() else []
    rows, excluded, sources, coverage, errors, bootstrap = [], [], [], [], [], None
    disabled = disabled or not config.get('enabled', True)
    if not disabled:
        try:
            rows, excluded, sources, coverage, bootstrap = Client(config, cache, offline).collect(now)
        except Exception as exc:
            errors.append(str(exc)[:250])
            sources.append({'id': 'very-bootstrap', 'provider_id': 'very', 'url': config['client_config_url'],
                            'fetch_status': 'failed', 'adapter_status': 'failed', 'error': errors[-1]})
    offers, events = reconcile(rows, [] if disabled else previous)
    result = {'schema_version': '1.0.0', 'module_id': 'retailer_promotions', 'module_name': '连锁零售店优惠',
              'generated_at': now.isoformat(), 'scope': {'country': 'IE', 'provider_id': 'very',
              'device_condition': 'new_inferred_from_merchant_policy;explicit_exceptions_excluded',
              'stock_required': False, 'minimum_discount_percent_exclusive': 20, 'full_catalogue': False},
              'offers': offers, 'sources': sources}
    schema = extended_schema(json.loads((ROOT/'offers.schema.json').read_text()))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(result)
    assert len({row['id'] for row in offers}) == len(offers)
    report = {'generated_at': now.isoformat(), 'duration_seconds': round(time.monotonic()-started, 4),
              'offline': offline, 'disabled': disabled, 'counts': {'total_records': len(offers),
              'listable_discounts': sum(o['distribution']['can_list'] for o in offers),
              'product_groups': len({o['product']['merchant_product_id'] for o in offers if o['distribution']['can_list']}),
              'excluded_variants': len(excluded)}, 'providers': {'very': {'coverage': coverage}},
              'sources': sources, 'errors': errors, 'bootstrap': bootstrap,
              'all_configured_categories_complete': bool(coverage) and all(c['complete'] for c in coverage),
              'llm_calls': 0, 'llm_tokens': 0, 'paid_scraping_api_calls': 0,
              'limitations': ['Public storefront endpoint, not an affiliate feed or third-party API contract.',
                              'Stock is unknown and is not a collection gate.', 'No automatic notifications sent.',
                              'Merchant reference price is not verified manufacturer RRP or prior-30-day low.']}
    out.mkdir(parents=True, exist_ok=True)
    for filename, value in [('offers.json', result), ('run-report.json', report), ('excluded.json', {'records': excluded}),
                            ('changes.json', {'events': events}), ('offers.schema.json', schema)]:
        write_json(out/filename, value)
    with (out/'offers.csv').open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['id', 'provider', 'product', 'variant', 'category', 'price_eur', 'reference_eur', 'discount_percent', 'stock', 'condition', 'observed_at', 'can_list', 'url'])
        for o in offers:
            p, v = o['pricing'], o['verification']
            w.writerow([o['id'], o['provider_name'], o['product']['name'], o['product']['variant_label'], o['product']['category'],
                        f"{p['device_price_minor']/100:.2f}", f"{p['reference_price_minor']/100:.2f}", p['discount_percent'],
                        o['stock_status'], o['product']['condition'], v['observed_at'], o['distribution']['can_list'], o['links']['landing_url']])
    code = 2 if errors or any(s['fetch_status'] != 'ok' for s in sources) or (coverage and not report['all_configured_categories_complete']) else 0
    return report, code


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=ROOT/'very.json')
    p.add_argument('--out', type=Path, default=ROOT/'very-data')
    p.add_argument('--cache', type=Path, default=ROOT/'very-cache')
    p.add_argument('--offline', action='store_true')
    p.add_argument('--as-of')
    p.add_argument('--disabled', action='store_true')
    a = p.parse_args()
    now = datetime.fromisoformat(a.as_of) if a.as_of else datetime.now(timezone.utc)
    report, code = run(json.loads(a.config.read_text()), a.cache, a.out, a.offline, now, a.disabled)
    print(json.dumps({k: report[k] for k in ('generated_at', 'duration_seconds', 'counts', 'errors', 'all_configured_categories_complete')}))
    return code


if __name__ == '__main__':
    sys.exit(main())
