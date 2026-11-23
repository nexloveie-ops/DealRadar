import copy
import unittest
from datetime import datetime, timezone
import collect


class CollectionRules(unittest.TestCase):
    def card(self, stock="Stock 3 Available", promo="", price="€99.95"):
        return f'''<div id="product-list"><div class="group product"><div class="productname desktop-only"><a href="/product/ABC123">SONY Headphones...</a><b>{stock}</b><div class="promotions">{promo}</div></div><div class="productprice"><div>{price}</div></div></div></div>'''

    def parse(self, raw):
        return collect.parse_powercity(raw, {"id":"test","category":"headphones","url":"https://powercity.ie/groups/view"}, {"fetched_at":"2026-11-22T20:00:00+00:00"})

    def test_display_units_are_excluded(self):
        rows, excluded, count = self.parse(self.card("Stock status: Displays Only"))
        self.assertEqual(len(rows), 0)
        self.assertEqual(excluded[0]["reason"], "not_new_or_display_stock")

    def test_no_reference_no_discount(self):
        rows, _, _ = self.parse(self.card())
        self.assertIsNone(rows[0]["pricing"]["discount_percent"])
        self.assertFalse(rows[0]["distribution"]["can_list"])

    def test_cashback_is_not_double_counted(self):
        rows, _, _ = self.parse(self.card(promo="€79.95 After Instant CashBack"))
        self.assertEqual(rows[0]["pricing"]["device_price_minor"], 9995)
        self.assertIsNone(rows[0]["pricing"]["discount_minor"])
        self.assertIn("promotion_terms_not_verified;not_added_to_savings", rows[0]["verification"]["issues"])

    def test_stock_unknown_does_not_enter_new_stock_list(self):
        rows, excluded, _ = self.parse(self.card("Stock status: Contact store"))
        self.assertEqual(len(rows), 0)
        self.assertEqual(excluded[0]["reason"], "no_confirmed_new_retail_stock")

    def test_variant_identity_remains_retailer_sku(self):
        rows, _, _ = self.parse(self.card())
        self.assertEqual(rows[0]["id"], "ie-retail-powercity-abc123")
        self.assertIsNone(rows[0]["product"]["canonical_product_id"])

    def test_missing_observation_is_unconfirmed_not_deleted(self):
        rows, _, _ = self.parse(self.card())
        merged, _ = collect.reconcile([], rows, datetime(2026,11,22,tzinfo=timezone.utc))
        self.assertEqual(merged[0]["status"], "unconfirmed")

    def test_replay_does_not_refresh_old_observation(self):
        rows, _, _ = self.parse(self.card())
        merged, _ = collect.reconcile(rows, [], datetime(2026,11,24,tzinfo=timezone.utc))
        self.assertEqual(merged[0]["status"], "stale")
        self.assertEqual(merged[0]["verification"]["observed_at"], "2026-11-22T20:00:00+00:00")

    def test_price_change_records_history_without_sending_alert(self):
        before, _, _ = self.parse(self.card(price="€119.95"))
        after, _, _ = self.parse(self.card(price="€99.95"))
        _, events = collect.reconcile(after, before, datetime(2026,11,22,tzinfo=timezone.utc))
        self.assertEqual(events[0]["event"], "observed_price_decrease")
        self.assertFalse(events[0]["send_notification"])

    def test_discount_and_reference_dates(self):
        self.assertEqual(collect.discount(2490,4990),(2500,50.1))
        with self.assertRaises(ValueError):
            collect.discount(10000,9000)

    def test_snapshot_never_gains_live_verified_timestamp(self):
        import json
        data = json.loads((collect.ROOT / "reference-snapshots.json").read_text())
        o = collect.import_snapshots(data)[0]
        self.assertIsNone(o["verification"]["origin_fetched_at"])
        self.assertIsNone(o["verification"]["fresh_until"])
        self.assertFalse(o["distribution"]["notification_ready"])


class ExpertRules(unittest.TestCase):
    SRC = {"id":"expert-headphones","category":"headphones","url":"https://www.expert.ie/tv-audio/headphones/"}
    META = {"fetched_at":"2026-11-22T20:00:00+00:00"}

    def card(self, title="Sony WH-CH520 Headphones | WHCH520B", now="€34.95", was="", rrp="", button="Add to Cart", slug="sony-wh-ch520-whch520b"):
        return f'''<li class="product"><article class="card"><figure><a class="card-figcaption-button" href="#">{button}</a></figure>
        <div class="card-body"><h4 class="card-title"><a href="https://www.expert.ie/{slug}/">{title}</a></h4><p class="card-text--brand">Sony</p>
        <span data-product-rrp-with-tax>{rrp}</span><span data-product-price-with-tax>{now}</span><span data-product-non-sale-price-with-tax>{was}</span></div></article></li>'''

    def parse(self, cards, nxt=""):
        page = '<div class="page-content"><ul class="productGrid">' + cards + '</ul></div>' + nxt
        return collect.parse_expert(page, self.SRC, self.META)

    def test_was_price_gives_discount_but_never_listable(self):
        rows, _, seen, _ = self.parse(self.card(was="€59.95"))
        p = rows[0]["pricing"]
        self.assertEqual((p["device_price_minor"], p["reference_price_minor"], p["reference_type"]), (3495, 5995, "retailer_was_price"))
        self.assertEqual(p["discount_percent"], 41.7)
        self.assertEqual(rows[0]["status"], "needs_review")
        self.assertFalse(rows[0]["distribution"]["can_list"])
        self.assertEqual(rows[0]["product"]["model_code"], "WHCH520B")

    def test_rrp_used_only_without_was_and_labelled(self):
        rows, _, _, _ = self.parse(self.card(rrp="€49.95"))
        self.assertEqual(rows[0]["pricing"]["reference_type"], "manufacturer_rrp")

    def test_reference_not_above_price_is_not_a_discount(self):
        rows, _, _, _ = self.parse(self.card(was="€34.95"))
        self.assertIsNone(rows[0]["pricing"]["discount_percent"])
        self.assertIn("reference_not_above_current_price;discount_not_computed", rows[0]["verification"]["issues"])

    def test_unavailable_and_refurbished_cards_are_excluded(self):
        rows, excluded, _, _ = self.parse(self.card(button="Out of stock") + self.card(title="Apple iPhone 15 Refurbished", slug="iphone-15-refurb"))
        self.assertEqual(rows, [])
        self.assertEqual(sorted(e["reason"] for e in excluded), ["not_available_to_order:out of stock", "not_new_or_display_stock"])

    def test_next_page_is_followed_only_from_page_link(self):
        _, _, _, nxt = self.parse(self.card(), '<link rel="next" href="https://www.expert.ie/tv-audio/headphones/?page=2">')
        self.assertEqual(nxt, "https://www.expert.ie/tv-audio/headphones/?page=2")

    def test_robots_disallowed_paths_are_never_requested(self):
        with self.assertRaises(ValueError):
            collect.checked_url("https://www.expert.ie/blackfriday/headphones/")
        with self.assertRaises(ValueError):
            collect.checked_url("https://www.expert.ie/search.php?q=sony")
        self.assertTrue(collect.checked_url("https://www.expert.ie/black-friday/headphones/"))


class PriceHistory(unittest.TestCase):
    def row(self, day, price, reference=None):
        o = collect.observation("expert", "item-1", "Item", "headphones", price, reference, "retailer_was_price" if reference else None,
            "https://www.expert.ie/item-1/", day + "T20:00:00+00:00", "s", "direct_official_html")
        o["verification"]["fresh_until"] = day + "T23:00:00+00:00"
        return o

    def test_one_point_per_day_and_old_points_expire(self):
        now = datetime(2026,11,22,21,tzinfo=timezone.utc)
        h = {"ie-retail-expert-item-1":[{"date":"2026-07-01","price_minor":100}]}
        collect.update_history(h, [self.row("2026-11-22", 5000)], now)
        collect.update_history(h, [self.row("2026-11-22", 4500)], now)
        self.assertEqual(h["ie-retail-expert-item-1"], [{"date":"2026-11-22","price_minor":4500}])

    def test_prior_lower_price_flags_was_claim(self):
        now = datetime(2026,11,22,21,tzinfo=timezone.utc)
        h = {"ie-retail-expert-item-1":[{"date":"2026-11-10","price_minor":3000}, {"date":"2026-11-22","price_minor":3495}]}
        o = self.row("2026-11-22", 3495, 5995)
        collect.apply_history([o], h, now)
        self.assertEqual(o["pricing"]["observed_history"]["prior_30d_low_minor"], 3000)
        self.assertIn("observed_lower_price_in_prior_30d;was_price_claim_needs_review", o["verification"]["issues"])

    def test_ongoing_sale_at_same_price_is_not_flagged(self):
        now = datetime(2026,11,23,1,tzinfo=timezone.utc)
        h = {"ie-retail-expert-item-1":[{"date":"2026-11-22","price_minor":3495}, {"date":"2026-11-23","price_minor":3495}]}
        o = self.row("2026-11-23", 3495, 5995)
        collect.apply_history([o], h, now)
        self.assertFalse(any("was_price_claim_needs_review" in i for i in o["verification"]["issues"]))

    def test_snapshots_never_enter_history(self):
        import json
        data = json.loads((collect.ROOT / "reference-snapshots.json").read_text())
        h = collect.update_history({}, collect.import_snapshots(data), datetime(2026,11,22,tzinfo=timezone.utc))
        self.assertEqual(h, {})


if __name__ == "__main__":
    unittest.main()
