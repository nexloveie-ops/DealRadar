import unittest

import amazon_direct as ad


class AmazonDirectParsing(unittest.TestCase):
    def test_search_card_price_and_strike(self):
        raw = '''<div data-component-type="s-search-result" data-asin="B0C665WWTD"><h2>JBL</h2>
        <div data-cy="title-recipe">JBL Tune 520BT Wireless On-Ear Headphones</div>
        <span class="a-price"><span class="a-offscreen">€43.30</span></span>
        <span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">€99.99</span></span></div>
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


class SearchListingFallback(unittest.TestCase):
    """From cloud IPs Amazon hides product-page prices but its search results show them."""

    def test_hidden_page_uses_search_listing_and_blocked_hits_still_listed(self):
        import json, sys, tempfile
        from unittest import mock
        card = lambda a, now, was: (f'<div data-component-type="s-search-result" data-asin="{a}"><h2>Item {a}</h2>'
                                    f'<img class="s-image" src="https://m.media-amazon.com/images/I/{a}.jpg">'
                                    f'<span class="a-price"><span class="a-offscreen">€{now}</span></span>'
                                    f'<span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">€{was}</span></span></div>')
        search = card("B000000001", "30.00", "100.00") + card("B000000002", "20.00", "50.00") + card("B000000003", "90.00", "100.00")
        calls = []

        def get(self, url, key):
            calls.append(url)
            if "/s?" in url:
                return search, "s"
            if url.endswith("B000000001"):
                return "<div>add this item to your basket</div>", "h"
            raise ad.Blocked(f"HTTP 503 at {url}")

        with tempfile.TemporaryDirectory() as d, mock.patch.object(ad.Fetcher, "get", get), mock.patch("time.sleep"):
            cfg = json.loads((ad.ROOT / "config.json").read_text())
            cfg["direct"]["max_searches_per_run"] = 1
            with mock.patch.object(ad.Path, "read_text", lambda self, *a, **k: json.dumps(cfg) if self.name == "config.json" else open(self).read()):
                with mock.patch.object(sys, "argv", ["x", "--cache", d + "/c", "--out", d + "/o"]):
                    ad.main()
            out = json.loads(open(d + "/o/offers.json").read())["offers"]
        got = {o["product"]["asin"]: o for o in out}
        self.assertEqual(set(got), {"B000000001", "B000000002"})  # 10% off is below threshold
        o = got["B000000001"]
        self.assertEqual(o["verification"]["method"], "amazon_search_listing")
        self.assertEqual((o["pricing"]["device_price_minor"], o["pricing"]["reference_price_minor"]), (3000, 10000))
        self.assertEqual(o["media"]["image_url"], "https://m.media-amazon.com/images/I/B000000001.jpg")
        self.assertEqual(got["B000000002"]["verification"]["method"], "amazon_search_listing")


class SearchQueuePool(unittest.TestCase):
    """With --search-pool the collector does no searches: it lists the queue's saved results, priced as of that search."""

    def test_pool_rows_listed_without_searching(self):
        import json, sys, tempfile
        from unittest import mock
        pool = [{"word_id": "jbl", "query": "jbl", "searched_at": "2026-11-26T09:00:00+00:00",
                 "rows": [{"asin": "B000000001", "title": "JBL Tune 670NC headphones", "price_minor": 4000, "strike_minor": 10000,
                           "image_url": None, "sponsored": False},
                          {"asin": "B000000003", "title": "JBL Go speaker", "price_minor": 9000, "strike_minor": 10000,
                           "image_url": None, "sponsored": False}]}]
        calls = []

        def get(self, url, key):
            calls.append(url)
            raise ad.Blocked(f"HTTP 503 at {url}")

        with tempfile.TemporaryDirectory() as d, mock.patch.object(ad.Fetcher, "get", get), mock.patch("time.sleep"):
            open(d + "/pool.json", "w").write(json.dumps(pool))
            with mock.patch.object(sys, "argv", ["x", "--cache", d + "/c", "--out", d + "/o", "--search-pool", d + "/pool.json"]):
                ad.main()
            out = json.loads(open(d + "/o/offers.json").read())["offers"]
            report = json.loads(open(d + "/o/run-report.json").read())
        self.assertFalse([u for u in calls if "/s?" in u])  # no searches of its own
        self.assertEqual([o["product"]["asin"] for o in out], ["B000000001"])
        v = out[0]["verification"]
        self.assertEqual((v["method"], v["verified_at"]), ("amazon_search_listing", "2026-11-26T09:00:00+00:00"))
        self.assertEqual(report["searches"][0]["from_queue"], True)


class SearchPlan(unittest.TestCase):
    def test_productive_first_then_stalest(self):
        kws = [{"id": i} for i in "abcdef"]
        stats = {"a": {"qualifying": 0, "searched_at": "2026-11-23T19"}, "b": {"qualifying": 11, "searched_at": "2026-11-23T19"},
                 "c": {"qualifying": 2, "searched_at": "2026-11-23T07"}, "d": {"qualifying": 9, "searched_at": "2026-11-23T19"},
                 "e": {"qualifying": 0, "searched_at": "2026-11-22T07"}}
        self.assertEqual([k["id"] for k in ad.plan_searches(kws, stats, 6)], ["b", "d", "c", "f", "e", "a"])
        self.assertEqual([k["id"] for k in ad.plan_searches(kws, stats, 4)], ["b", "d", "f", "e"])
        self.assertEqual([k["id"] for k in ad.plan_searches(kws, {}, 2)], ["a", "b"])


class ListingSanity(unittest.TestCase):
    def test_unit_price_is_not_a_was_price(self):
        raw = """<div data-component-type="s-search-result" data-asin="B0D4H554KW"><h2>Echo Ease Tinnitus Inhaler</h2>
        <span class="a-price"><span class="a-offscreen">€5.91</span></span>
        <span class="a-price a-text-price" data-a-size="b"><span class="a-offscreen">€591.00</span></span></div>
        <div data-component-type="s-search-result" data-asin="B0C665WWTD"><h2>JBL Tune 520BT</h2>
        <span class="a-price"><span class="a-offscreen">€43.30</span></span>
        <span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">€99.99</span></span></div>"""
        rows, _ = ad.parse_search(raw)
        self.assertEqual([r["strike_minor"] for r in rows], [None, 9999])

    def test_non_tech_titles(self):
        for t in ["JBL Pronovo Lotl Grano, Main Food for Axolotl", "Echo Ease Tinnitus Inhaler", "Echoes of Bluebell History: a book"]:
            self.assertTrue(ad.NOT_TECH.search(t), t)
        for t in ["Apple MacBook Air 13", "Echo Dot (2022 model)", "JBL Tune 520BT", "Kindle Paperwhite"]:
            self.assertIsNone(ad.NOT_TECH.search(t), t)


class MemberKeywords(unittest.TestCase):
    def test_private_keyword_results_stay_out_of_public_offers(self):
        import json, sys, tempfile
        from unittest import mock
        card = lambda a: (f'<div data-component-type="s-search-result" data-asin="{a}"><h2>Item {a}</h2>'
                          f'<span class="a-price"><span class="a-offscreen">€30.00</span></span>'
                          f'<span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">€100.00</span></span></div>')
        searched = []
        small = lambda a: (f'<div data-component-type="s-search-result" data-asin="{a}"><h2>Item {a}</h2>'
                           f'<span class="a-price"><span class="a-offscreen">€80.00</span></span>'
                           f'<span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">€100.00</span></span></div>')

        def get(self, url, key):
            if "/s?" in url:
                searched.append(url)
                return ((card("B0000MEMBR") + small("B0000SMALL")) if "k=garmin" in url else (card("B0000PUBLC") + small("B0000PUBSM"))), "s"
            return "<div>add this item to your basket</div>", "h"

        with tempfile.TemporaryDirectory() as d, mock.patch.object(ad.Fetcher, "get", get), mock.patch("time.sleep"):
            cfg = json.loads((ad.ROOT / "config.json").read_text())
            cfg["direct"]["max_searches_per_run"] = 2
            mk = d + "/mk.json"
            open(mk, "w").write(json.dumps([{"id": "garmin-fenix", "query": "garmin fenix", "members": 2}]))
            import os
            os.makedirs(d + "/o", exist_ok=True)  # admin words were searched before; the member's word never was
            open(d + "/o/search-cursor.json", "w").write(json.dumps({"stats": {k["id"]: {"searched_at": "2026-11-20T07:00:00+00:00", "qualifying": 0}
                                                                     for k in cfg["keywords"]}}))
            with mock.patch.object(ad.Path, "read_text", lambda self, *a, **k: json.dumps(cfg) if self.name == "config.json" else open(self).read()):
                with mock.patch.object(sys, "argv", ["x", "--cache", d + "/c", "--out", d + "/o", "--member-keywords", mk]):
                    ad.main()
            public = [o["product"]["asin"] for o in json.loads(open(d + "/o/offers.json").read())["offers"]]
            member = json.loads(open(d + "/o/member-offers.json").read())
        self.assertEqual(len(searched), 2)
        self.assertTrue(any("k=garmin+fenix" in u for u in searched))  # members' words share the search pool
        self.assertTrue(all("&i=electronics&rh=p_8%3A20-" in u for u in searched))  # Amazon's own 20%-and-up filter
        self.assertEqual(public, ["B0000PUBLC"])
        # Every word collects above 20%: the 70%-off item is kept, the exactly-20% one is not.
        self.assertEqual(sorted(o["product"]["asin"] for o in member["offers"]), ["B0000MEMBR"])
        self.assertEqual(member["searched_keyword_ids"], ["garmin-fenix"])


class WatchedProducts(unittest.TestCase):
    def test_page_price_then_asin_search_then_out_of_budget(self):
        import json, sys, tempfile
        from unittest import mock
        def get(self, url, key):
            if url.endswith("/dp/B000000AAA"):
                return ('<span id="productTitle">Echo Dot</span><div id="corePriceDisplay_desktop_feature_div"><span class="priceToPay">'
                        '<span class="a-offscreen">€30.00</span></span><span class="basisPrice">RRP: <span class="a-offscreen">€60.00</span></span></div>'
                        '<div id="availability">In stock</div>' + " " * 6000), "p"
            if "/dp/" in url:
                return "<div>add this item to your basket</div>" + " " * 6000, "h"
            if "k=B000000BBB" in url:
                return ('<div data-component-type="s-search-result" data-asin="B000000BBB"><h2>Garmin Venu 3</h2>'
                        '<span class="a-price"><span class="a-offscreen">€288.92</span></span>'
                        '<span class="a-price a-text-price" data-a-strike="true"><span class="a-offscreen">€499.99</span></span></div>'), "s"
            return "", "s"
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ad.Fetcher, "get", get), mock.patch("time.sleep"):
            cfg = json.loads((ad.ROOT / "config.json").read_text())
            cfg["direct"].update(max_searches_per_run=0, max_watch_searches_per_run=1)
            open(d + "/w.json", "w").write(json.dumps([{"key": k} for k in ("B000000AAA", "B000000BBB", "B000000CCC")]))
            with mock.patch.object(ad.Path, "read_text", lambda self, *a, **k: json.dumps(cfg) if self.name == "config.json" else open(self).read()):
                with mock.patch.object(sys, "argv", ["x", "--cache", d + "/c", "--out", d + "/o", "--watch", d + "/w.json"]):
                    ad.main()
            res = {r["key"]: r for r in json.loads(open(d + "/o/watch-status.json").read())["items"]}
        self.assertEqual((res["B000000AAA"]["status"], res["B000000AAA"]["price_minor"], res["B000000AAA"]["discount_percent"]), ("checked", 3000, 50.0))
        self.assertEqual((res["B000000BBB"]["status"], res["B000000BBB"]["price_minor"], res["B000000BBB"]["reference_minor"]), ("checked", 28892, 49999))
        self.assertEqual(res["B000000CCC"]["status"], "not_checked")   # page hid the price and the one watch search was used


if __name__ == "__main__":
    unittest.main()
