"""Meaningful regression checks for consumer-facing prices, eligibility and lifecycle."""
import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

import collect


class PipelineChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dataset = json.loads((Path(__file__).parent / "data/offers.json").read_text())
        cls.base = next(o for o in cls.dataset["offers"] if o["provider_id"] == "three")
        cls.at = datetime.fromisoformat(cls.base["verification"]["verified_at"])

    def row(self):
        return copy.deepcopy(self.base)

    def test_exact_minor_unit_arithmetic(self):
        row=self.row()
        collect.discount(row,collect.money("379.99"),collect.money("529.99"),"same_provider_non_port_in_price")
        self.assertEqual(row["pricing"]["discount_minor"],15000)
        self.assertEqual(row["pricing"]["discount_percent"],28.3)

    def test_catalogue_binary_float_artifacts_round_to_cents(self):
        self.assertEqual(collect.money(99.98999),9999)
        self.assertEqual(collect.money(89.990005),8999)

    def test_vodafone_a27_is_a_separate_128gb_switch_offer(self):
        row=next(o for o in self.dataset["offers"] if o["id"]=='ie-prepay-vodafone-handset-galaxy-a27-5g')
        self.assertEqual(row['product']['storage_gb'],128)
        self.assertEqual(row['pricing']['reference_price_minor'],29999)
        self.assertEqual(row['pricing']['device_price_minor'],9999)
        self.assertEqual(row['pricing']['discount_minor'],20000)
        self.assertEqual(row['eligibility']['customer_groups'],['port_in'])

    def test_bad_reference_is_rejected(self):
        with self.assertRaises(ValueError):collect.discount(self.row(),20000,10000,"same_provider_non_port_in_price")

    def test_rerun_does_not_make_a_new_offer_or_change_event(self):
        first,_=collect.finalize([self.row()],{},self.at,24)
        second,changes=collect.finalize([self.row()],{"offers":first},self.at,24)
        self.assertEqual(first[0]["id"],second[0]["id"])
        self.assertEqual(changes,[])

    def test_price_change_is_one_change_not_a_new_offer(self):
        old,_=collect.finalize([self.row()],{},self.at,24)
        row=self.row();row["pricing"]["device_price_minor"]+=100
        _,changes=collect.finalize([row],{"offers":old},self.at,24)
        self.assertEqual([e["event"] for e in changes],["changed"])

    def test_missing_fetch_does_not_delete_or_claim_expiry(self):
        old,_=collect.finalize([self.row()],{},self.at,24)
        rows,changes=collect.finalize([],{"offers":old},self.at,24)
        self.assertEqual(rows[0]["status"],"unconfirmed")
        self.assertFalse(rows[0]["distribution"]["can_list"])
        self.assertEqual(changes[0]["event"],"not_observed")

    def test_old_evidence_cannot_become_current_on_replay(self):
        row=self.row();row["validity"]["ends_on"]=None
        rows,_=collect.finalize([row],{},datetime(2026,12,15,tzinfo=timezone.utc),24)
        self.assertEqual(rows[0]["status"],"stale")
        self.assertFalse(rows[0]["distribution"]["can_list"])

    def test_entry_end_is_not_claim_deadline(self):
        row=self.row();row["validity"].update(ends_on="2026-10-15",claim_by="2026-12-31")
        rows,_=collect.finalize([row],{},self.at,24)
        self.assertEqual(rows[0]["status"],"expired")

    def test_changed_terms_require_review(self):
        row=self.row();sid=row["source_ids"][0]
        records={x:{"role":"landing"} for x in row["source_ids"]}
        records[sid]={"role":"terms","sha256":"changed-hash"}
        collect.pin_review(row,{sid:"previous-hash"},{},records)
        self.assertEqual(row["status"],"needs_review")

    def test_duplicates_fail_loudly(self):
        with self.assertRaises(ValueError):collect.finalize([self.row(),self.row()],{},self.at,24)

    def test_dataset_never_contains_used_devices_or_billpay(self):
        for o in self.dataset["offers"]:
            self.assertEqual(o["service_type"],"prepay")
            self.assertNotRegex((o["product"] or {}).get("name","").lower(),r"refurbished|nearly new|mint\+")
            self.assertIn(o["device_condition"],["new","not_applicable"])

    def test_unknown_eligibility_is_not_marked_all_customers(self):
        tesco=[o for o in self.dataset["offers"] if o["provider_id"]=="tesco_mobile"]
        self.assertTrue(tesco)
        self.assertTrue(all(o["eligibility"]["customer_groups"]==["not_specified"] for o in tesco))

    def test_pending_credit_and_gifts_are_not_publishable_benefits(self):
        for o in self.dataset["offers"]:
            if o["status"]=="needs_review":self.assertFalse(o["distribution"]["can_list"])
            for b in o["benefits"]:
                if b["type"]=="gift":self.assertFalse(b["can_publish"])


if __name__=="__main__":unittest.main()
