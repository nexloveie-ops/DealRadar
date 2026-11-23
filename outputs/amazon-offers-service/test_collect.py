import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import collect


class AmazonRules(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((collect.ROOT/'config.json').read_text())
        self.now = datetime(2026,11,22,22,tzinfo=timezone.utc)

    def response(self):
        return {'searchResult':{'items':[{'asin':'B0TEST0001','detailPageURL':'https://www.amazon.ie/dp/B0TEST0001?tag=example-21&linkCode=osi',
            'itemInfo':{'title':{'displayValue':'Synthetic Sony test headphones'}},'offersV2':{'listings':[{
                'condition':{'value':'New'},'availability':{'type':'IN_STOCK'},'isBuyBoxWinner':True,
                'merchantInfo':{'name':'Synthetic merchant'},'price':{'money':{'amount':50,'currency':'EUR'},
                'savingBasis':{'money':{'amount':100,'currency':'EUR'},'savingBasisType':'LIST_PRICE','savingBasisTypeLabel':'List price'}}}]}}]}}

    def parse(self,r):
        return collect.api_items(r,'sony',self.config,self.now)

    def listing(self,r):
        return r['searchResult']['items'][0]['offersV2']['listings'][0]

    def test_exact_40_percent_is_excluded(self):
        r=self.response();self.listing(r)['price']['money']['amount']=60
        rows,_ = self.parse(r)
        self.assertEqual(rows,[])

    def test_rounded_up_below_40_is_not_included(self):
        r=self.response();self.listing(r)['price']['money']['amount']=60.01
        rows,_=self.parse(r);self.assertEqual(rows,[])

    def test_new_condition_cannot_borrow_used_price(self):
        r=self.response();used=copy.deepcopy(self.listing(r));used['condition']['value']='Used';used['price']['money']['amount']=20
        self.listing(r)['price']['money']['amount']=90
        r['searchResult']['items'][0]['offersV2']['listings'].append(used)
        rows,_=self.parse(r);self.assertEqual(rows,[])

    def test_currency_stock_and_featured_listing_checks(self):
        for path,val in [('currency','GBP'),('stock','OUT_OF_STOCK'),('featured',False)]:
            r=self.response();p=self.listing(r)
            if path=='currency':p['price']['money']['currency']=val
            elif path=='stock':p['availability']['type']=val
            else:p['isBuyBoxWinner']=val
            rows,_=self.parse(r);self.assertEqual(rows,[])

    def test_expired_deal_is_excluded(self):
        r=self.response();self.listing(r)['dealDetails']={'endTime':'2026-11-21T00:00:00Z'}
        rows,_=self.parse(r);self.assertEqual(rows,[])

    def test_prime_condition_does_not_become_unrestricted(self):
        r=self.response();self.listing(r)['dealDetails']={'accessType':'PRIME_EXCLUSIVE'}
        rows,_=self.parse(r)
        self.assertTrue(rows[0]['eligibility']['prime_required'])
        self.assertFalse(rows[0]['distribution']['notification_ready'])

    def test_link_replaces_all_tags_and_preserves_existing_amazon_metadata(self):
        u=collect.affiliate_link('B0DJGCX6Q2','mobile123ie-21','https://www.amazon.ie/dp/B0DJGCX6Q2?tag=old&tag=other&linkId=amazon-generated')
        q=parse_qs(urlparse(u).query)
        self.assertEqual(q['tag'],['mobile123ie-21']);self.assertEqual(q['linkId'],['amazon-generated'])

    def test_foreign_domains_and_wrong_variants_are_rejected(self):
        for u in ['https://www.amazon.ie.evil.test/dp/B0DJGCX6Q2','https://www.amazon.co.uk/dp/B0DJGCX6Q2','https://www.amazon.ie/dp/B0FPNV7H69']:
            with self.assertRaises(ValueError):collect.affiliate_link('B0DJGCX6Q2','mobile123ie-21',u)

    def test_no_fake_linkid_is_created(self):
        u=collect.affiliate_link('B0DJGCX6Q2','mobile123ie-21')
        self.assertNotIn('linkId',u)

    def test_enabled_keywords_and_pages_control_request_count(self):
        self.assertEqual(len(collect.request_plan(self.config)),105)
        self.config['keywords'][0]['enabled']=False
        self.assertEqual(len(collect.request_plan(self.config)),100)

    def test_duplicate_asin_preserves_all_keyword_matches(self):
        rows,_=self.parse(self.response());other=copy.deepcopy(rows[0]);other['matched_keyword_ids']=['headphones']
        result=collect.merge_keyword_hits(rows+[other]);self.assertEqual(len(result),1)
        self.assertEqual(result[0]['matched_keyword_ids'],['headphones','sony'])

    def test_purge_removes_expired_api_content(self):
        rows,_=self.parse(self.response())
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder);collect.write_json(out/'offers.json',{'offers':rows})
            self.assertEqual(collect.purge_expired(out,self.now+timedelta(hours=24)),1)
            self.assertEqual(json.loads((out/'offers.json').read_text())['offers'],[])

    def test_historical_research_does_not_gain_fresh_timestamp(self):
        seed=json.loads((collect.ROOT/'research-seeds.json').read_text())
        rows=collect.historical_candidates(seed,self.config)
        self.assertTrue(rows)
        self.assertTrue(all(o['verification']['verified_at'] is None for o in rows))
        self.assertTrue(all(o['verification']['research_date']=='2026-11-21' for o in rows))

    def test_blog_draft_has_tag_but_no_stale_prices_or_images(self):
        seed=json.loads((collect.ROOT/'research-seeds.json').read_text())
        content=collect.blog_draft(seed,self.config)
        self.assertIn('tag=mobile123ie-21',content)
        self.assertNotIn('€',content)
        self.assertNotIn('<img',content)
        self.assertNotIn('% off',content)


if __name__=='__main__':
    unittest.main()
