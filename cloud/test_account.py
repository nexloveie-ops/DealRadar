import unittest

import account


class Doc:
    def __init__(self, db, path):
        self.db, self.path = db, path
        self.reference = self

    def get(self):
        return Snap(self.path in self.db.data)

    def delete(self):
        self.db.data.pop(self.path, None)

    def collection(self, name):
        return Col(self.db, self.path + (name,))


class Snap:
    def __init__(self, exists):
        self.exists = exists


class Col:
    def __init__(self, db, path, flt=None):
        self.db, self.path, self.flt = db, path, flt

    def document(self, i):
        return Doc(self.db, self.path + (i,))

    def where(self, filter):
        return Col(self.db, self.path, filter)

    def stream(self):
        for p, v in list(self.db.data.items()):
            if p[:-1] != self.path:
                continue
            if self.flt and v.get(self.flt.field_path) != self.flt.value:
                continue
            yield Doc(self.db, p)


class DB:
    def __init__(self, data):
        self.data = data

    def collection(self, name):
        return Col(self, (name,))


class DeleteAccount(unittest.TestCase):
    def test_removes_member_clicks_and_phones_only(self):
        db = DB({
            ("members", "u1"): {"email": "a@b.ie"},
            ("members", "u1", "clicks", "d1"): {"deal_id": "d1"},
            ("members", "u1", "clicks", "d2"): {"deal_id": "d2"},
            ("members", "u2"): {"email": "c@d.ie"},
            ("members", "u2", "clicks", "d1"): {"deal_id": "d1"},
            ("push_devices", "p1"): {"uid": "u1"},
            ("push_devices", "p2"): {"uid": None},
            ("push_devices", "p3"): {"uid": "u2"},
            ("watch_status", "Expert:x"): {"price_minor": 100},
        })
        self.assertEqual(account.delete_member_data(db, "u1"), 4)
        self.assertEqual(sorted(db.data), [("members", "u2"), ("members", "u2", "clicks", "d1"), ("push_devices", "p2"),
                                           ("push_devices", "p3"), ("watch_status", "Expert:x")])


if __name__ == "__main__":
    unittest.main()
