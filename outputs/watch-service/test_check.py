import json
import unittest
from unittest import mock

import check

EXPERT = ('<h1>JBL Tune 780NC Headphones</h1><meta property="og:image" content="https://cdn11.bigcommerce.com/p.png">'
          '"price":{"with_tax":{"formatted":"€59.95","value":59.95,"currency":"EUR"},"sale_price_with_tax":{"value":59.95},'
          '"non_sale_price_with_tax":{"formatted":"€129.99","value":129.99,"currency":"EUR"}} "instock":true "purchasable":true '
          '"out_of_stock_message":"Out of stock"')
POWERCITY = ('<title>POWERCITY - 002069 JVC IN EAR GUMY HEADPHONES Headphone</title><div id="price"><span id="pricetag">&euro;7.95</span></div>'
             '<p>Stock 135 pcs available</p>')


class Checkers(unittest.TestCase):
    def test_expert_price_was_price_and_stock(self):
        with mock.patch.object(check, "get", lambda u, **k: (200, EXPERT)):
            r = check.check_expert({"url": "https://www.expert.ie/x/", "key": "x"})
        self.assertEqual((r["status"], r["price_minor"], r["reference_minor"]), ("checked", 5995, 12999))
        with mock.patch.object(check, "get", lambda u, **k: (200, EXPERT.replace('"instock":true', '"instock":false'))):
            self.assertEqual(check.check_expert({"url": "x", "key": "x"})["status"], "unavailable")

    def test_powercity_price_stock_no_was_price(self):
        with mock.patch.object(check, "get", lambda u, **k: (200, POWERCITY)):
            r = check.check_powercity({"url": "https://powercity.ie/product/002069", "key": "002069"})
        self.assertEqual((r["status"], r["price_minor"], r["reference_minor"]), ("checked", 795, None))
        with mock.patch.object(check, "get", lambda u, **k: (200, POWERCITY.replace("135 pcs", "0 pcs"))):
            self.assertEqual(check.check_powercity({"url": "x", "key": "002069"})["status"], "unavailable")

    def test_mobile123_variant_from_link(self):
        body = json.dumps({"title": "Galaxy A37", "featured_image": "//cdn.shopify.com/a.jpg", "variants": [
            {"id": 1, "price": 30000, "compare_at_price": 40000, "available": True},
            {"id": 2, "price": 25000, "compare_at_price": 40000, "available": False}]})
        with mock.patch.object(check, "get", lambda u, **k: (200, body)):
            r = check.check_mobile123({"url": "https://www.mobile123.ie/products/a37?variant=2", "key": "a37"})
        self.assertEqual((r["status"], r["price_minor"], r["image_url"]), ("unavailable", 25000, "https://cdn.shopify.com/a.jpg"))

    def test_not_found_and_discount(self):
        with mock.patch.object(check, "get", lambda u, **k: (404, "")):
            self.assertEqual(check.check_expert({"url": "x", "key": "x"})["status"], "not_found")
        self.assertEqual(check.pct(5995, 12999), 53.9)


class AmazonInstant(unittest.TestCase):
    HIDDEN = '<span id="productTitle">Echo Show 15</span><img id="landingImage" src="https://m.media-amazon.com/images/I/e.jpg"><div>add this item to your basket</div>' + " " * 6000
    SEARCH = ('<div data-component-type="s-search-result" data-asin="B0C7XNR559"><h2>Echo Show 15</h2>'
              '<span class="a-price"><span class="a-offscreen">€199.99</span></span>'
              '<span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">€299.99</span></span></div>' + " " * 6000)

    def fake(self, url, **k):
        return (200, self.SEARCH) if "/s?" in url else (200, self.HIDDEN)

    def test_hidden_price_then_asin_search(self):
        with mock.patch.object(check, "get", self.fake), mock.patch("time.sleep"):
            r = check.check_amazon_page({"key": "B0C7XNR559", "url": "x"}, allow_search=True)
        self.assertEqual((r["status"], r["price_minor"], r["reference_minor"], r["title"]), ("checked", 19999, 29999, "Echo Show 15"))
        self.assertEqual(r["image_url"], "https://m.media-amazon.com/images/I/e.jpg")

    def test_hidden_price_without_search_keeps_name_and_picture(self):
        with mock.patch.object(check, "get", self.fake), mock.patch("time.sleep"):
            r = check.check_amazon_page({"key": "B0C7XNR559", "url": "x"}, allow_search=False)
        self.assertEqual((r["status"], r["title"]), ("not_checked", "Echo Show 15"))
        self.assertIn("next collection", r["error"])


if __name__ == "__main__":
    unittest.main()
