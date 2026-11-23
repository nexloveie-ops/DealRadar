import unittest

import amazon_direct as ad


class AmazonDirectParsing(unittest.TestCase):
    def test_search_card_price_and_strike(self):
        raw = '''<div data-component-type="s-search-result" data-asin="B0C665WWTD"><h2>JBL</h2>
        <div data-cy="title-recipe">JBL Tune 520BT Wireless On-Ear Headphones</div>
        <span class="a-price"><span class="a-offscreen">€43.30</span></span>
        <span class="a-price a-text-price"><span class="a-offscreen">€99.99</span></span></div>
        <a class="s-pagination-next" href="/s?page=2">Next</a>'''
        rows, more = ad.parse_search(raw)
        self.assertTrue(more)
        self.assertEqual(rows[0]["asin"], "B0C665WWTD")
        self.assertEqual(rows[0]["title"], "JBL Tune 520BT Wireless On-Ear Headphones")
        self.assertEqual((rows[0]["price_minor"], rows[0]["strike_minor"]), (4330, 9999))

    def test_product_page_price_reference_label_and_seller(self):
        raw = '''<span id="productTitle"> Echo Dot (2022 model) </span>
        <div id="corePriceDisplay_desktop_feature_div"><span class="priceToPay"><span class="a-offscreen">€37.94</span></span>
        <span class="basisPrice">RRP: <span class="a-offscreen">€95.18</span></span></div>
        <div id="availability"> In Stock </div>
        <div id="merchantInfoFeature_feature_div"><span class="offer-display-feature-text-message">Amazon.ie</span></div>'''
        p = ad.parse_product(raw)
        self.assertEqual((p["price_minor"], p["reference_minor"], p["reference_label"]), (3794, 9518, "RRP"))
        self.assertEqual(p["seller"], "Amazon.ie")
        self.assertEqual(ad.reference_type(p["reference_label"]), "amazon_rrp")

    def test_hidden_basket_price_is_not_guessed(self):
        p = ad.parse_product("<div>To see the product details, add this item to your basket.</div>")
        self.assertIsNone(p["price_minor"])
        self.assertTrue(p["price_hidden"])

    def test_threshold_is_strictly_above_forty(self):
        self.assertEqual(ad.exact_discount(6000, 10000), (4000, 40.0))
        self.assertIsNone(ad.exact_discount(10000, 10000)[1])
        self.assertTrue(ad.NOT_NEW.search("Apple iPhone 13 (Renewed)"))


if __name__ == "__main__":
    unittest.main()
