import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
import web_collect as wc


class PublicWebRules(unittest.TestCase):
    def setUp(self):
        self.c=json.loads((wc.ROOT/'config.json').read_text())
        self.settings=json.loads((wc.ROOT/'web-sources.json').read_text())
        self.now=datetime(2026,11,22,23,tzinfo=timezone.utc)
        self.row={'source_id':'gcd:test','source_url':'https://www.goodcraicdeals.ie/deals/test','source_kind':'goodcraic',
            'title':'Sony test headphones','amazon_url':'https://www.amazon.ie/dp/B0TEST0001','retailer_domain':'amazon.ie','category':'ELECTRONICS',
            'price':50,'reference':100,'reference_type':'third_party_unspecified_reference','source_status':'APPROVED','is_expired':False,
            'reported_outdated':False,'source_updated_at':'2026-11-22T20:00:00Z','source_quote_checked_at':None,
            'source_starts_at':None,'source_ends_at':None,'fetched_at':'2026-11-22T22:00:00+00:00','condition_claim':None}

    def normalise(self,row=None):
        return wc.normalise(row or self.row,self.c,self.settings,self.now)

    def test_strict_40_excluded_and_above_40_included(self):
        self.row['price']=60
        with self.assertRaisesRegex(ValueError,'threshold'):self.normalise()
        self.row['price']=59.99
        self.assertEqual(self.normalise()['pricing']['discount_minor'],4001)

    def test_rounded_40_is_not_automatically_accepted(self):
        self.row['price']=60.01
        with self.assertRaisesRegex(ValueError,'threshold'):self.normalise()

    def test_energy_saving_40_percent_has_no_price_pair(self):
        self.row.update(title='Sony SSD 40% less power consumption',reference=None)
        with self.assertRaisesRegex(ValueError,'missing_price_pair'):self.normalise()

    def test_wrong_country_expired_and_used_rejected(self):
        for patch in [{'amazon_url':'https://www.amazon.co.uk/dp/B0TEST0001'},{'is_expired':True},{'title':'Sony refurbished headphones'}]:
            row={**self.row,**patch}
            with self.assertRaises(ValueError):self.normalise(row)

    def test_search_page_multi_product_collection_not_single_asin(self):
        self.row.update(amazon_url='https://www.amazon.ie/s?k=beats',title='Beats sale headphones and earbuds')
        with self.assertRaisesRegex(ValueError,'single_asin'):self.normalise()

    def test_no_source_update_becomes_verified_new_price(self):
        row=self.normalise()
        self.assertIsNone(row['verification']['verified_at'])
        self.assertEqual(row['product']['condition'],'unconfirmed')
        self.assertFalse(row['distribution']['can_list'])

    def test_coupon_not_silently_made_unconditional(self):
        self.row['title']='Sony headphones (Use code BLACK26 for price)'
        o=self.normalise()
        self.assertTrue(o['eligibility']['coupon_required'])
        self.assertEqual(o['eligibility']['coupon_code'],'BLACK26')

    def test_older_source_and_expired_promotion_rejected(self):
        for patch in [{'source_updated_at':'2026-09-01T00:00:00Z'},{'source_ends_at':'2026-11-21T00:00:00Z'}]:
            with self.assertRaises(ValueError):self.normalise({**self.row,**patch})

    def test_same_asin_conflict_is_not_best_price_cherry_picked(self):
        a=self.normalise();b=self.normalise({**self.row,'source_id':'gcd:other','price':40})
        merged=wc.consolidate([a,b],self.now)
        self.assertEqual(len(merged),1)
        self.assertEqual(merged[0]['status'],'conflicting_quotes')
        self.assertEqual(merged[0]['pricing']['device_price_minor'],5000)
        self.assertEqual(len(merged[0]['sources']),2)

    def test_variants_remain_distinct_and_other_tags_removed(self):
        a=self.normalise();b=self.normalise({**self.row,'source_id':'gcd:other','amazon_url':'https://www.amazon.ie/dp/B0TEST0002?tag=other-21'})
        self.assertEqual(len(wc.consolidate([a,b],self.now)),2)
        self.assertEqual(b['links']['affiliate_url'],'https://www.amazon.ie/dp/B0TEST0002?tag=mobile123ie-21')

    def test_item_card_structured_price_and_reference(self):
        raw='''<article data-spotted-card data-price="59.99" data-expires="2026-11-23T00:00:00Z" id="example"><h3>Sony headphones</h3><p>Amazon’s label: RRP <span>€100</span></p><time datetime="2026-11-22T20:00:00Z"></time><a href="https://www.amazon.ie/dp/B0TEST0001?tag=other-21">View</a></article>'''
        row=wc.parse_item(raw,'https://item.ie/deals/spotted',{'fetched_at':'2026-11-22T22:00:00Z'})[0]
        o=self.normalise(row)
        self.assertEqual(o['pricing']['reference_type'],'third_party_amazon_label_rrp')

    def test_embedded_json_is_decoded_without_executing_script(self):
        data={'queryClientState':{'queries':[{'queryKey':['deals',{'page':2}],'state':{'data':{'pages':[[{'id':'one','title':'Sony headphones','slug':'one','discountedPrice':50,'usualPrice':100,'url':'https://www.amazon.ie/dp/B0TEST0001','status':'APPROVED'}]]}}}]}}
        raw='<script>window.__TSR_DEHYDRATED__ = '+json.dumps(data)+';throw Error("must not execute")</script>'
        rows,page=wc.parse_gcd(raw,'https://www.goodcraicdeals.ie/category/electronics/audio?page=2',{'fetched_at':'2026-11-22T22:00:00Z'})
        self.assertEqual(page,2);self.assertEqual(len(rows),1)

    def test_quote_cache_expiry_is_not_a_promotion_end(self):
        raw='''<article data-spotted-card data-price="50" data-expires="2026-11-23T00:00:00Z" id="example"><h3>Sony headphones</h3><p>Amazon’s label: RRP €100</p><a href="https://www.amazon.ie/dp/B0TEST0001">View</a></article>'''
        row=wc.parse_item(raw,'https://item.ie/deals/spotted',{'fetched_at':'2026-11-22T22:00:00Z'})[0]
        o=self.normalise(row)
        self.assertIsNone(o['validity']['ends_on'])
        self.assertEqual(o['verification']['source_quote_expires_at'],'2026-11-23T00:00:00Z')

    def test_expanded_aliases_match_new_3c_products(self):
        for title in ['UGREEN 100W charging station','Kindle Colorsoft reader','SteelSeries Arctis headset','Crucial 2TB SSD','Ring video doorbell']:
            self.assertTrue(wc.keyword_matches(title,self.c),title)

    def test_expanded_keywords_do_not_match_unrelated_products(self):
        for title in ['LEGO toy car','Nike trainers','Philips steam iron','Ninja air fryer']:
            self.assertFalse(wc.keyword_matches(title,self.c),title)

    def test_dealdepot_uses_real_path_pagination(self):
        source={'adapter':'dealdepot','paginate':True,'url':'https://dealdepot.ie/store/amazon-ie/?sort=discount'}
        self.assertEqual(wc.source_page_url(source,2),'https://dealdepot.ie/store/amazon-ie/page/2/?sort=discount')

    def test_dealdepot_hidden_price_not_inferred_from_discount_text(self):
        raw='''<article class="dd-card-list"><h3 class="dd-card-title"><a href="https://dealdepot.ie/deal/sony/">Sony headphones</a></h3><div class="dd-card-pricing"><span class="dd-now">See today’s price</span></div><div class="dd-card-cta"><a href="https://www.amazon.ie/dp/B0TEST0001">See price</a></div></article>'''
        rows=wc.parse_dealdepot(raw,'https://dealdepot.ie/store/amazon-ie/',{'fetched_at':'2026-11-22T22:00:00Z'})
        self.assertIsNone(rows[0]['price']);self.assertIsNone(rows[0]['reference'])


if __name__=='__main__':unittest.main()
