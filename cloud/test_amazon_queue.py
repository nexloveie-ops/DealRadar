"""Offline tests for the Amazon search queue: N-minute spacing, retry on block, advance on success, new rounds."""
import unittest
from datetime import datetime, timedelta, timezone

import amazon_queue as q

AD = q.amazon_direct()
T0 = datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
PAGE = "<html>" + "x" * 6000 + "</html>"


class Snap:
    def __init__(self, ref):
        self.reference, self.exists = ref, ref.key in ref.store

    def to_dict(self):
        return dict(self.reference.store[self.reference.key])


class Doc:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def get(self):
        return Snap(self)

    def set(self, d):
        self.store[self.key] = dict(d)

    def delete(self):
        self.store.pop(self.key, None)


class Coll:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def document(self, i):
        return Doc(self.db.store, (self.name, i))

    def add(self, d):
        self.db.log.append(d)

    def stream(self):
        return [Snap(Doc(self.db.store, k)) for k in list(self.db.store) if k[0] == self.name]


class DB:
    def __init__(self, interval=2, enabled=True):
        self.store, self.log = {("settings", "amazon_queue"): {"enabled": enabled, "interval_minutes": interval}}, []

    def collection(self, name):
        return Coll(self, name)

    def state(self):
        return self.store.get(("meta", "amazon_queue"), {})


SETTINGS = {"amazon_keywords": [{"id": "jbl", "query": "jbl"}, {"id": "sony", "query": "sony"},
                                {"id": "off", "query": "off", "enabled": False}]}
MEMBERS = [{"id": "k1", "query": "dyson", "on_whitelist": False}, {"id": "k2", "query": "JBL", "on_whitelist": True}]


def run(db, now, fetch):
    return q.tick(db, now, fetch, lambda: SETTINGS, lambda: MEMBERS)


def ok(url):
    return PAGE


def blocked(url):
    raise AD.Blocked("HTTP 503")


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.parse = AD.parse_search
        AD.parse_search = lambda raw: ([{"asin": "B0", "title": "t", "price_minor": 5000, "strike_minor": 10000,
                                         "image_url": None, "sponsored": False}], False)

    def tearDown(self):
        AD.parse_search = self.parse

    def test_list_is_admin_words_then_members_not_on_whitelist(self):
        self.assertEqual([i["query"] for i in q.build_list(SETTINGS, MEMBERS)], ["jbl", "sony", "dyson"])
        self.assertEqual(q.build_list(SETTINGS, MEMBERS)[2]["id"], "m:k1")

    def test_disabled_does_nothing(self):
        db = DB(enabled=False)
        self.assertEqual(run(db, T0, ok), {"skipped": "disabled"})
        self.assertEqual(db.log, [])

    def test_success_advances_and_saves(self):
        db = DB()
        r = run(db, T0, ok)
        self.assertTrue(r["ok"])
        self.assertEqual((r["query"], r["results"], r["qualifying"]), ("jbl", 1, 1))
        self.assertEqual(db.state()["index"], 1)
        self.assertEqual(db.store[("amazon_search_pool", "jbl")]["searched_at"], T0.isoformat(timespec="seconds"))

    def test_waits_n_minutes(self):
        db = DB(interval=5)
        run(db, T0, ok)
        self.assertEqual(run(db, T0 + timedelta(minutes=2), ok)["skipped"], "not_due")
        self.assertEqual(run(db, T0 + timedelta(minutes=5), ok)["query"], "sony")

    def test_block_retries_same_word(self):
        db = DB()
        run(db, T0, ok)
        r = run(db, T0 + timedelta(minutes=2), blocked)
        self.assertEqual((r["ok"], r["error"], r["query"], r["attempt"]), (False, "blocked", "sony", 1))
        r = run(db, T0 + timedelta(minutes=4), blocked)
        self.assertEqual((r["query"], r["attempt"]), ("sony", 2))
        r = run(db, T0 + timedelta(minutes=6), ok)
        self.assertEqual((r["query"], r["attempt"], r["ok"]), ("sony", 3, True))
        self.assertEqual(db.state()["attempts"], 0)
        self.assertEqual(len(db.log), 4)

    def test_new_round_after_last_word(self):
        db = DB(interval=1)
        queries = [run(db, T0 + timedelta(minutes=i), ok)["query"] for i in range(4)]
        self.assertEqual(queries, ["jbl", "sony", "dyson", "jbl"])
        self.assertEqual(db.state()["round"], 2)

    def test_pool_keeps_only_fresh_results(self):
        db = DB()
        run(db, T0, ok)
        self.assertEqual(len(q.pool_for_collector(db, T0 + timedelta(hours=10), 36)), 1)
        self.assertEqual(q.pool_for_collector(db, T0 + timedelta(hours=40), 36), [])


if __name__ == "__main__":
    unittest.main()
