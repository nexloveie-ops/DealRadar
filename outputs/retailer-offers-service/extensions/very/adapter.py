"""Normalize public Constructor product variants into the shared retailer format."""
import copy
import html
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urljoin, urlparse

METHOD = 'direct_storefront_constructor_json'
NOT_NEW = re.compile(r'refurb|renewed|pre[ -]?owned|ex[ -]?display|display\s+(?:unit|model)|open[ -]?box|used\s+condition|grade\s+[abc]\b', re.I)
CATEGORIES = {'phones', 'computers', 'tablets', 'monitors', 'storage', 'gaming'}


def timestamp(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        raise ValueError('Timezone required')
    return value.astimezone(timezone.utc)


def money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('Invalid money value')
    number = Decimal(str(value))
    if not number.is_finite() or number < 0 or number > Decimal('1000000'):
        raise ValueError('Invalid money range')
    cents = number * 100
    if cents != cents.to_integral_value():
        raise ValueError('Money has sub-cent precision')
    return int(cents)


def product_url(value, product_id):
    if not isinstance(value, str):
        raise ValueError('Missing product URL')
    url = urljoin('https://www.very.ie', value)
    p = urlparse(url)
    if (p.scheme != 'https' or p.netloc != 'www.very.ie' or p.query or p.fragment
            or not re.fullmatch(r'/[^/]+/' + re.escape(product_id) + r'\.prd', p.path)):
        raise ValueError('Unexpected Very product URL')
    return url


def product_name(brand, value):
    """Very sends names with HTML entities (&reg;) and often already starting with the brand."""
    brand, value = (html.unescape(str(x or '')).strip() for x in (brand, value))
    if brand and value.lower().startswith(brand.lower()):
        return value
    return ' '.join(x for x in (brand, value) if x)


def validate_payload(payload):
    response = payload.get('response', {})
    total, rows = response.get('total_num_results'), response.get('results')
    if type(total) is not int or total < 0 or not isinstance(rows, list):
        raise ValueError('Missing Constructor results or total')
    if len(rows) > total or (total > 0 and not rows):
        raise ValueError('Inconsistent result count')
    if any(not isinstance(row, dict) or not isinstance(row.get('data'), dict) for row in rows):
        raise ValueError('Invalid product structure')
    return response


def normalize(payload, category, source_id, fetched_at, now, config):
    if category not in CATEGORIES:
        raise ValueError('Unsupported Very category')
    observed = timestamp(fetched_at)
    if observed > now + timedelta(minutes=5):
        raise ValueError('Observation is in the future')
    expires = observed + timedelta(hours=config['freshness_hours'])
    offers, excluded = [], []
    for item in validate_payload(payload)['results']:
        data = item['data']
        try:
            pid = str(data['id'])
            if not re.fullmatch(r'\d+', pid):
                raise ValueError('Missing stable product ID')
            url = product_url(data.get('url'), pid)
            title = product_name(data.get('brand'), item.get('value'))
            if not title:
                raise ValueError('Missing product name')
            variants = item.get('variations')
            if not isinstance(variants, list) or not variants:
                raise ValueError('Missing variant-level prices')
        except (KeyError, ValueError, TypeError) as exc:
            excluded.append({'product_id': data.get('id'), 'reason': str(exc)})
            continue
        for variant in variants:
            sku = None
            try:
                v = variant['data']
                sku = v.get('variation_id') or v.get('catalogueNumber')
                if not isinstance(sku, str) or not re.fullmatch(r'[A-Z0-9_-]{1,60}', sku):
                    raise ValueError('Missing stable SKU')
                if NOT_NEW.search(title + ' ' + str(variant.get('value', ''))):
                    raise ValueError('Explicitly non-new or display product')
                prices = v.get('prices', {}).get('now', {})  # Never use prices.next.
                if prices.get('currencyCode') != 'EUR':
                    raise ValueError('Missing EUR prices')
                sale, reference = money(prices['basePrice']), money(prices['origPrice'])
                threshold = Decimal(str(config['minimum_discount_percent_exclusive']))
                if not (reference > sale > 0 and Decimal(reference-sale)*100 > threshold*reference):
                    raise ValueError('Discount does not exceed threshold')
                for key, before in [('startDate', True), ('endDate', False)]:
                    if prices.get(key):
                        t = timestamp(prices[key])
                        if (before and t > observed) or (not before and t <= observed):
                            raise ValueError('Current price period is not active')
                options = v.get('options') or {}
                if not isinstance(options, dict):
                    raise ValueError('Invalid variant options')
                label = ', '.join(str(value) for key, value in sorted(options.items()) if value)
                image = v.get('image_url')
                if image and (urlparse(image).scheme != 'https' or urlparse(image).netloc != 'media.very.ie'):
                    image = None
                fresh_until = min(expires, timestamp(prices['endDate'])) if prices.get('endDate') else expires
                active = now < fresh_until
                issues = ['condition_inferred_from_very_new_retail_policy', 'stock_not_supplied_not_a_listing_gate',
                          'retailer_was_price_not_verified_rrp_or_prior_30d_low', 'delivery_not_included']
                offers.append({
                    'id': f'ie-retail-very-{pid}-{sku.lower()}', 'module_id': 'retailer_promotions',
                    'provider_id': 'very', 'provider_name': 'Very.ie', 'country': 'IE', 'currency': 'EUR',
                    'offer_kind': 'product_discount',
                    'product': {'sku': sku, 'name': title, 'category': category, 'condition': 'new_inferred',
                                'condition_basis': 'Very new-retail policy accepted by project owner; explicit exceptions excluded',
                                'canonical_product_id': None, 'merchant_product_id': pid,
                                'merchant_variant_id': sku, 'variant_label': label, 'variant_options': options},
                    'pricing': {'device_price_minor': sale, 'reference_price_minor': reference,
                                'reference_type': 'retailer_was_price', 'reference_period': None,
                                'discount_minor': reference-sale,
                                'discount_percent': float((Decimal(reference-sale)*100/reference).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)),
                                'includes_delivery': False, 'delivery_cost_minor': None, 'cashback_minor': None},
                    'eligibility': {'conditions': [], 'coupon_code': None},
                    'validity': {'starts_on': None, 'ends_on': None, 'timezone': 'Europe/Dublin',
                                 'price_starts_at': prices.get('startDate'), 'price_ends_at': prices.get('endDate')},
                    'stock_status': 'unknown', 'status': 'active' if active else 'stale',
                    'verification': {'method': METHOD, 'observed_at': fetched_at, 'origin_fetched_at': fetched_at,
                                     'fresh_until': fresh_until.isoformat(),
                                     'issues': issues},
                    'source_ids': [source_id], 'links': {'landing_url': url, 'affiliate_url': None},
                    'media': {'image_url': image}, 'distribution': {'can_list': active, 'notification_ready': False}})
            except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
                excluded.append({'product_id': data.get('id'), 'sku': sku, 'reason': str(exc)})
    return offers, excluded


def reconcile(current, previous):
    """Do not refresh old observations, silently merge conflicting prices, or reuse stock claims."""
    rows, conflicts, events = {}, set(), []
    for row in current:
        oid = row['id']
        if oid in rows:
            old = rows[oid]
            if old['pricing'] != row['pricing']:
                conflicts.add(oid)
            old['source_ids'] = sorted(set(old['source_ids'] + row['source_ids']))
        else:
            rows[oid] = copy.deepcopy(row)
    for oid in conflicts:
        rows[oid]['status'] = 'unconfirmed'
        rows[oid]['distribution']['can_list'] = False
        rows[oid]['verification']['issues'].append('conflicting_variant_prices')
    for row in previous:
        if row.get('provider_id') != 'very':
            raise ValueError('Previous Very state contains a different retailer')
        oid = row['id']
        if oid not in rows:
            old = copy.deepcopy(row)
            old['status'] = 'unconfirmed'
            old['distribution'].update(can_list=False, notification_ready=False)
            old['verification']['issues'] = sorted(set(old['verification']['issues'] + ['not_observed_or_no_longer_qualifies']))
            rows[oid] = old
        elif rows[oid]['status'] == 'active' and row['pricing']['device_price_minor'] != rows[oid]['pricing']['device_price_minor']:
            events.append({'offer_id': oid, 'old_price_minor': row['pricing']['device_price_minor'],
                           'new_price_minor': rows[oid]['pricing']['device_price_minor'], 'send_notification': False})
    return sorted(rows.values(), key=lambda r: (-Decimal(r['pricing']['discount_minor'])/r['pricing']['reference_price_minor'], r['id'])), events


def extended_schema(original):
    """Keep the existing retailers' exact validation branch; extend only provider=very."""
    schema = copy.deepcopy(original)
    old = copy.deepcopy(schema['$defs']['offer'])
    very = copy.deepcopy(old)
    p = very['properties']
    p['provider_id'] = {'const': 'very'}
    p['status'] = {'enum': ['active', 'stale', 'unconfirmed']}
    p['product']['properties']['condition'] = {'const': 'new_inferred'}
    p['verification']['properties']['method'] = {'const': METHOD}
    p['distribution']['properties']['can_list'] = {'type': 'boolean'}
    p['distribution']['properties']['notification_ready'] = {'const': False}
    schema['$defs']['offer'] = {'oneOf': [old, very]}
    return schema
