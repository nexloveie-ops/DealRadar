import unittest

import collect


def product(variants, ptype="Unlocked Mobile Phones", title="Samsung Galaxy A37 5G 128GB – Brand New"):
    return {"id": 1, "handle": "galaxy-a37", "title": title, "product_type": ptype, "tags": [],
            "images": [{"src": "https://cdn.shopify.com/a.jpg"}], "variants": variants}


def variant(vid, price, compare, available=True, title="Default Title"):
    return {"id": vid, "title": title, "price": price, "compare_at_price": compare, "available": available, "sku": f"S{vid}"}


class Mobile123Feed(unittest.TestCase):
    T = "2026-11-23T07:00:00+00:00"

    def test_discounted_in_stock_variant_is_listed_with_link_and_image(self):
        rows, _ = collect.offers_from([product([variant(11, "329.99", "489.00")])], self.T)
        r = rows[0]
        self.assertEqual((r["pricing"]["device_price_minor"], r["pricing"]["reference_price_minor"]), (32999, 48900))
        self.assertEqual(r["pricing"]["discount_percent"], 32.5)
        self.assertEqual(r["links"]["landing_url"], "https://www.mobile123.ie/products/galaxy-a37?variant=11")
        self.assertEqual(r["media"]["image_url"], "https://cdn.shopify.com/a.jpg")
        self.assertEqual(r["product"]["category"], "phones")

    def test_no_compare_price_out_of_stock_or_price_rise_is_skipped(self):
        rows, skipped = collect.offers_from([product([variant(1, "10", None), variant(2, "10", "20", available=False), variant(3, "30", "20")])], self.T)
        self.assertEqual(rows, [])
        self.assertEqual(skipped[0]["reason"], "no_in_stock_discounted_variant")

    def test_best_variant_wins_and_variant_name_is_shown(self):
        rows, _ = collect.offers_from([product([variant(1, "90", "100", title="Black"), variant(2, "60", "100", title="Blue")])], self.T)
        self.assertEqual(rows[0]["pricing"]["discount_percent"], 40.0)
        self.assertTrue(rows[0]["product"]["name"].endswith("(Blue)"))

    def test_categories(self):
        self.assertEqual(collect.category({"product_type": "Laptop Chargers", "title": "65W charger"}), "accessories")
        self.assertEqual(collect.category({"product_type": "LEGO Sets", "title": "LEGO City"}), "toys")
        self.assertEqual(collect.category({"product_type": "Speakers", "title": "JBL Go"}), "speakers")


if __name__ == "__main__":
    unittest.main()
