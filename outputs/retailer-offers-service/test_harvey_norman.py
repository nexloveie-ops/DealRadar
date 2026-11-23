import unittest

import harvey_norman as hn

T = "2026-11-23T20:00:00+00:00"
SEARCH = {"id": "hn-headphones", "query": "headphones", "category": "headphones"}


def product(title, variants, handle="item"):
    return {"id": "gid://shopify/Product/1", "title": title, "handle": handle, "url": f"https://www.harveynorman.ie/products/{handle}",
            "media": [{"type": "image", "url": "https://cdn.shopify.com/p.jpg"}], "variants": variants}


def variant(vid, price, was, available=True, title="Default Title"):
    return {"id": f"gid://shopify/ProductVariant/{vid}", "sku": f"S{vid}", "title": title, "price": {"amount": price, "currency": "EUR"},
            "list_price": {"amount": was, "currency": "EUR"}, "availability": {"available": available}}


class HarveyNorman(unittest.TestCase):
    def test_discount_at_or_above_threshold_is_kept_with_link_and_image(self):
        rows, _ = hn.offers_from([product("JBL Tune 680NC Wireless On-Ear Headphones", [variant(1, 4995, 9900)], "jbl-680")], SEARCH, T, 30)
        r = rows[0]
        self.assertEqual((r["pricing"]["device_price_minor"], r["pricing"]["reference_price_minor"], r["pricing"]["discount_percent"]), (4995, 9900, 49.5))
        self.assertEqual(r["links"]["landing_url"], "https://www.harveynorman.ie/products/jbl-680")
        self.assertEqual(r["media"]["image_url"], "https://cdn.shopify.com/p.jpg")
        self.assertEqual(r["verification"]["method"], "official_ucp_catalog")

    def test_below_threshold_out_of_stock_and_no_list_price_are_skipped(self):
        rows, skipped = hn.offers_from([product("Sony WH-1000XM4 Headphones", [variant(1, 19900, 24900)]),
                                        product("JBL Headphones", [variant(2, 1000, 9900, available=False)]),
                                        product("Beats Headphones", [variant(3, 9900, 9900)])], SEARCH, T, 30)
        self.assertEqual(rows, [])
        self.assertEqual([s["reason"] for s in skipped], ["discount_below_30_percent", "no_in_stock_discounted_variant", "no_in_stock_discounted_variant"])

    def test_best_variant_wins_and_variant_link(self):
        rows, _ = hn.offers_from([product("JLab Earbuds", [variant(1, 9000, 10000, title="Black"), variant(2, 5000, 10000, title="Blue")])], SEARCH, T, 30)
        self.assertEqual(rows[0]["pricing"]["discount_percent"], 50.0)
        self.assertTrue(rows[0]["links"]["landing_url"].endswith("?variant=2"))
        self.assertTrue(rows[0]["product"]["name"].endswith("(Blue)"))

    def test_appliances_and_refurbished_are_dropped_and_title_sets_category(self):
        rows, skipped = hn.offers_from([product("Hisense Washing Machine", [variant(1, 100, 1000)]),
                                        product("Apple iPad (Refurbished)", [variant(2, 100, 1000)]),
                                        product("Anker Nano 10K Power Bank", [variant(3, 100, 1000)])],
                                       {"id": "hn-tablet", "query": "tablet", "category": "tablets"}, T, 30)
        self.assertEqual([r["product"]["category"] for r in rows], ["accessories"])
        self.assertEqual({s["reason"] for s in skipped}, {"not_a_tech_product", "not_new"})
        self.assertEqual(hn.classify("Pure Woodland Portable Bluetooth Speaker", "x"), "speakers")
        self.assertIsNone(hn.NOT_TECH.search("Pure Woodland Portable Bluetooth Speaker"))


if __name__ == "__main__":
    unittest.main()
