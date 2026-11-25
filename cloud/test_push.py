import unittest
from datetime import datetime, timedelta, timezone

import members
import push

TIERS = members.DEFAULT_TIERS if isinstance(members.DEFAULT_TIERS, list) else [members.DEFAULT_TIERS]
EVENING = datetime(2026, 11, 25, 19, 15, tzinfo=timezone.utc)     # 19:15 in Dublin (winter: UTC)
MORNING = datetime(2026, 11, 26, 7, 15, tzinfo=timezone.utc)
NIGHT = datetime(2026, 11, 25, 23, 0, tzinfo=timezone.utc)
SETTINGS = {"mode": "all"}


def row(i, title, pct, price, public=True):
    view = {"id": i, "title": title, "discount_percent": pct, "price_minor": price, "store": "Shop"}
    return {"publish": public, "public_view": view if public else None, "member_view": None if public else view}


ROWS = [row("a", "JBL Tune 780NC", 54, 5999), row("b", "Sony WH-1000XM6", 30, 29900), row("c", "PS5 controller", 62, 999),
        row("d", "JBL Go 4", 25, 3999, public=False)]
OLD = {"ids": ["b"], "day": "2026-11-24"}


def member(words=(), watch=()):
    exp = (EVENING + timedelta(days=2)).isoformat()
    return {"tier": "free",
            "keywords": [{"id": w, "query": w, "expires_at": exp, "min_discount": 20} for w in words],
            "watch": [dict(w, expires_at=exp) for w in watch]}


def dev(i, uid=None, **prefs):
    return {"id": i, "token": "tok-" + i, "uid": uid, "prefs": prefs}


class Plan(unittest.TestCase):
    def run_plan(self, now, devices, docs, status=None, baseline=OLD, settings=SETTINGS):
        return push.plan(now, devices, docs, TIERS, status or {}, ROWS, baseline, settings)

    def test_anonymous_phone_gets_new_public_deals_summary_in_the_evening(self):
        msgs, _, base = self.run_plan(EVENING, [dev("p1")], {})
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["title"], "2 new deals today")               # a and c are new; b was there; d is members-only
        self.assertEqual(msgs[0]["body"], "up to 62% off · from €9.99")        # the biggest discount and that product's price
        self.assertEqual(base["day"], "2026-11-25")
        self.assertEqual(base["ids"], ["a", "b", "c", "d"])

    def test_nothing_in_the_morning_or_at_night_or_twice_an_evening(self):
        self.assertEqual(self.run_plan(MORNING, [dev("p1")], {})[0], [])
        self.assertEqual(self.run_plan(NIGHT, [dev("p1")], {})[0], [])
        self.assertEqual(self.run_plan(EVENING, [dev("p1")], {}, baseline={"ids": [], "day": "2026-11-25"})[0], [])

    def test_first_run_sets_the_baseline_and_sends_no_summary(self):
        msgs, _, base = self.run_plan(EVENING, [dev("p1")], {}, baseline=None)
        self.assertEqual(msgs, [])
        self.assertEqual(len(base["ids"]), 4)

    def test_morning_first_run_sets_baseline_without_using_up_the_evening(self):
        _, _, base = self.run_plan(MORNING, [dev("p1")], {}, baseline=None)
        self.assertEqual(base["day"], "")
        msgs, _, _ = self.run_plan(EVENING + timedelta(days=1), [dev("p1")], {}, baseline=base)
        self.assertEqual(msgs, [])           # nothing new since the morning in this test data, but the evening still ran
        _, _, base2 = self.run_plan(EVENING + timedelta(days=1), [dev("p1")], {}, baseline=base)
        self.assertEqual(base2["day"], "2026-11-26")

    def test_member_words_new_matches_including_members_only_deals(self):
        msgs, _, _ = self.run_plan(EVENING, [dev("p1", "u1")], {"u1": member(words=["jbl"])})
        self.assertEqual(msgs[0]["title"], "2 new deals for “jbl”")
        self.assertEqual(msgs[0]["body"], "up to 54% off · from €59.99")
        self.assertEqual(msgs[0]["data"], {"type": "words"})

    def test_member_with_words_but_nothing_new_gets_nothing_not_the_summary(self):
        msgs, _, _ = self.run_plan(EVENING, [dev("p1", "u1")], {"u1": member(words=["sony"])})
        self.assertEqual(msgs, [])

    def test_watch_alert_once_then_only_when_cheaper(self):
        w = {"id": "w1", "site": "Expert", "key": "jbl-780", "url": "https://www.expert.ie/x", "label": None,
             "condition": {"type": "price", "value": 70}}
        status = {members.watch_doc_id("Expert", "jbl-780"): {"status": "checked", "price_minor": 5999, "discount_percent": 54,
                                                               "title": "JBL Tune 780NC", "buy_url": "https://www.expert.ie/x"}}
        docs = {"u1": member(watch=[w])}
        msgs, updates, _ = self.run_plan(MORNING, [dev("p1", "u1")], docs, status)
        self.assertEqual(msgs[0]["title"], "Price alert")
        self.assertEqual(msgs[0]["body"], "JBL Tune 780NC is now €59.99, below your €70 target")
        self.assertEqual(msgs[0]["data"]["type"], "watch")
        self.assertEqual(updates, {"u1": {"w1": 5999}})
        docs["u1"]["push_watch_sent"] = {"w1": 5999}
        self.assertEqual(self.run_plan(MORNING, [dev("p1", "u1")], docs, status)[0], [])      # same price: no repeat
        status[members.watch_doc_id("Expert", "jbl-780")]["price_minor"] = 4999
        self.assertEqual(len(self.run_plan(MORNING, [dev("p1", "u1")], docs, status)[0]), 1)  # cheaper: alert again
        status[members.watch_doc_id("Expert", "jbl-780")]["price_minor"] = 7999
        _, updates, _ = self.run_plan(MORNING, [dev("p1", "u1")], docs, status)
        self.assertEqual(updates, {"u1": {}})                                                 # no longer at target: forgotten

    def test_watch_alert_beats_words_one_message_per_phone(self):
        w = {"id": "w1", "site": "Expert", "key": "k", "url": "u", "condition": {"type": "any"}}
        status = {members.watch_doc_id("Expert", "k"): {"status": "checked", "price_minor": 100, "discount_percent": 10}}
        msgs, _, _ = self.run_plan(EVENING, [dev("p1", "u1")], {"u1": member(words=["jbl"], watch=[w])}, status)
        self.assertEqual([m["data"]["type"] for m in msgs], ["watch"])

    def test_choices_and_test_mode(self):
        self.assertEqual(self.run_plan(EVENING, [dev("p1", daily=False)], {})[0], [])
        test = {"mode": "test", "test_uids": ["u9"]}
        self.assertEqual(self.run_plan(EVENING, [dev("p1"), dev("p2", "u1")], {"u1": member()}, settings=test)[0], [])
        msgs = self.run_plan(EVENING, [dev("p3", "u9")], {"u9": member()}, settings=test)[0]
        self.assertEqual([m["device"] for m in msgs], ["p3"])

    def test_euro_format(self):
        self.assertEqual([push.eur(4999), push.eur(5900), push.eur(129900)], ["€49.99", "€59", "€1,299"])


if __name__ == "__main__":
    unittest.main()
