import copy
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from jsonschema import Draft202012Validator
import very
from extensions.very.adapter import extended_schema, normalize, reconcile
from extensions.very.collector import Client, validate_config

NOW = datetime(2026, 11, 24, 19, tzinfo=timezone.utc)
CONFIG = json.loads((very.ROOT/'very.json').read_text())


def payload():
    return {'response': {'total_num_results': 1, 'results': [
        {'value': 'Laptop', 'data': {'id': '1601234567', 'url': '/test-laptop/1601234567.prd', 'brand': 'Test'},
         'variations': [{'value': 'Laptop', 'data': {'variation_id': 'SKU1', 'options': {'COLOUR': 'BLACK'},
            'prices': {'now': {'basePrice': 79.99, 'origPrice': 100, 'currencyCode': 'EUR'},
                       'next': {'basePrice': 1000}}, 'image_url': 'https://media.very.ie/i/test'}}]}]}}


def prices(d):
    return d['response']['results'][0]['variations'][0]['data']['prices']['now']


def normalized(data=None, now=NOW, observed=None):
    return normalize(data or payload(), 'computers', 'very-computers-p1', (observed or NOW).isoformat(), now, CONFIG)


class VeryRules(unittest.TestCase):
    def test_stock_not_a_gate_and_inference_is_explicit(self):
        rows, rejected = normalized()
        self.assertEqual(rejected, [])
        self.assertEqual(rows[0]['stock_status'], 'unknown')
        self.assertEqual(rows[0]['product']['condition'], 'new_inferred')
        self.assertTrue(rows[0]['distribution']['can_list'])
        self.assertFalse(rows[0]['distribution']['notification_ready'])

    def test_strict_threshold_and_cent_precision(self):
        d = payload(); prices(d)['basePrice'] = 80
        self.assertEqual(normalized(d)[0], [])
        prices(d)['basePrice'] = 79.99
        self.assertEqual(normalized(d)[0][0]['pricing']['discount_percent'], 20.01)
        prices(d)['basePrice'] = 79.999
        self.assertEqual(normalized(d)[0], [])

    def test_variant_prices_not_mixed(self):
        d = payload(); product = d['response']['results'][0]
        v2 = copy.deepcopy(product['variations'][0]); v2['data']['variation_id'] = 'SKU2'
        v2['data']['prices']['now']['basePrice'] = 90
        product['variations'].append(v2)
        product['data']['prices'] = {'now': {'basePrice': 1, 'origPrice': 100}}
        rows, _ = normalized(d)
        self.assertEqual([r['product']['sku'] for r in rows], ['SKU1'])
        self.assertEqual(rows[0]['pricing']['device_price_minor'], 7999)

    def test_explicit_non_new_excluded(self):
        for title in ['Refurbished laptop', 'Ex-display laptop', 'Open box laptop', 'Pre-owned laptop']:
            d = payload(); d['response']['results'][0]['value'] = title
            self.assertEqual(normalized(d)[0], [])

    def test_missing_reference_wrong_currency_and_future_price(self):
        d = payload(); del prices(d)['origPrice']
        self.assertEqual(normalized(d)[0], [])
        d = payload(); prices(d)['currencyCode'] = 'GBP'
        self.assertEqual(normalized(d)[0], [])
        self.assertEqual(normalized()[0][0]['pricing']['reference_price_minor'], 10000)
        d = payload(); prices(d)['startDate'] = (NOW+timedelta(days=1)).isoformat()
        self.assertEqual(normalized(d)[0], [])

    def test_replay_keeps_timestamp_and_expires(self):
        observed = NOW-timedelta(days=2)
        rows, _ = normalized(observed=observed)
        self.assertEqual(rows[0]['verification']['origin_fetched_at'], observed.isoformat())
        self.assertEqual(rows[0]['status'], 'stale')
        self.assertFalse(rows[0]['distribution']['can_list'])

    def test_promotion_end_limits_freshness(self):
        d = payload(); prices(d)['endDate'] = (NOW+timedelta(hours=1)).isoformat()
        rows, _ = normalized(d, now=NOW+timedelta(hours=2))
        self.assertEqual(rows[0]['status'], 'stale')
        self.assertFalse(rows[0]['distribution']['can_list'])

    def test_cross_host_product_and_unsafe_image(self):
        d = payload(); d['response']['results'][0]['data']['url'] = 'https://evil.example/test/1601234567.prd'
        self.assertEqual(normalized(d)[0], [])
        d = payload(); d['response']['results'][0]['variations'][0]['data']['image_url'] = 'http://media.very.ie/x'
        self.assertIsNone(normalized(d)[0][0]['media']['image_url'])

    def test_conflicting_duplicate_withheld(self):
        row = normalized()[0][0]; second = copy.deepcopy(row); second['pricing']['device_price_minor'] = 7000
        rows, _ = reconcile([row, second], [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'unconfirmed')
        self.assertFalse(rows[0]['distribution']['can_list'])

    def test_failed_refresh_never_refreshes_previous_timestamp(self):
        previous = normalized()[0]; rows, _ = reconcile([], previous)
        self.assertEqual(rows[0]['verification']['observed_at'], NOW.isoformat())
        self.assertFalse(rows[0]['distribution']['can_list'])
        self.assertEqual(previous[0]['status'], 'active')

    def test_schema_keeps_old_validation_exactly(self):
        original = json.loads((very.ROOT/'offers.schema.json').read_text()); snapshot = copy.deepcopy(original)
        schema = extended_schema(original)
        self.assertEqual(original, snapshot)
        self.assertEqual(schema['$defs']['offer']['oneOf'][0], original['$defs']['offer'])
        row = normalized()[0][0]
        validator = Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/offer'})
        validator.validate(row)
        row['provider_id'] = 'expert'
        self.assertTrue(list(validator.iter_errors(row)))

    def test_request_limits(self):
        for key, value in [('api_origin', 'https://evil.example'), ('max_workers', 20), ('minimum_discount_percent_exclusive', 19)]:
            d = copy.deepcopy(CONFIG); d[key] = value
            with self.assertRaises(ValueError): validate_config(d)

    def test_repeated_page_is_not_complete(self):
        config = copy.deepcopy(CONFIG); config['page_size'] = 1
        with tempfile.TemporaryDirectory() as td:
            client = Client(config, td); d = payload(); d['response']['total_num_results'] = 2
            with patch.object(client, 'read', return_value=(json.dumps(d).encode(), {'fetched_at': NOW.isoformat()})):
                rows, _, sources, coverage = client.category(CONFIG['categories'][1], 'public-key', NOW)
            self.assertFalse(coverage['complete'])
            self.assertEqual(sources[-1]['fetch_status'], 'failed')
            self.assertEqual(len(rows), 1)

    def test_partial_failure_preserves_other_category(self):
        config = copy.deepcopy(CONFIG); config['categories'] = config['categories'][:2]
        with tempfile.TemporaryDirectory() as td:
            client = Client(config, td)
            def read(url, cache_id):
                if 'phones' in cache_id: raise OSError('test source failure')
                return json.dumps(payload()).encode(), {'fetched_at': NOW.isoformat(), 'fetch_status': 'ok'}
            with patch.object(client, 'bootstrap', return_value=('public-key', {})), patch.object(client, 'read', side_effect=read):
                rows, _, sources, coverage, _ = client.collect(NOW)
            self.assertEqual(len(rows), 1)
            self.assertEqual(sum(c['complete'] for c in coverage), 1)
            self.assertEqual(sum(s['fetch_status']=='failed' for s in sources), 1)

    def test_failed_bootstrap_outputs_partial_result_and_disable_avoids_network(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(Client, 'collect', side_effect=ValueError('bootstrap failed')):
                report, code = very.run(CONFIG, Path(td)/'cache', Path(td)/'out', now=NOW)
            self.assertEqual(code, 2)
            self.assertEqual(report['counts']['listable_discounts'], 0)
            with patch.object(Client, 'collect', side_effect=AssertionError('network not allowed')):
                report, code = very.run(CONFIG, Path(td)/'cache', Path(td)/'out', now=NOW, disabled=True)
            self.assertEqual(code, 0)
            self.assertTrue(report['disabled'])

    def test_publication_bridge_only_accepts_very(self):
        spec = importlib.util.spec_from_file_location('combine_very_test', very.ROOT.parent/'combine_deals.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        raw = normalized()[0][0]; public = {'module': 'retailer', 'status': 'active', 'fresh_until': raw['verification']['fresh_until']}
        self.assertTrue(module.auto_publish(copy.deepcopy(public), raw, NOW)[0])
        other = copy.deepcopy(raw); other['provider_id'] = 'expert'
        self.assertFalse(module.auto_publish(copy.deepcopy(public), other, NOW)[0])
        raw['distribution']['can_list'] = False
        self.assertFalse(module.auto_publish(copy.deepcopy(public), raw, NOW)[0])


if __name__ == '__main__':
    unittest.main()
