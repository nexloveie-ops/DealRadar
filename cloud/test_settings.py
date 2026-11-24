import json
import unittest

import settings


class Settings(unittest.TestCase):
    def test_public_minimum_defaults_to_30_not_the_collection_minimum(self):
        hn = json.loads((settings.OUTPUTS / "retailer-offers-service/harvey-norman.json").read_text())
        self.assertEqual(hn["minimum_discount_percent"], 20)          # collection minimum
        self.assertEqual(settings.defaults()["retail_min_discount"], 30.0)
        self.assertEqual(settings.validate({})["retail_min_discount"], 30.0)

    def test_very_is_a_shop_and_on_unless_switched_off(self):
        old = settings.validate({"shops": [{"id": "powercity", "enabled": True}]})
        self.assertIn({"id": "very", "name": "Very.ie", "enabled": True}, old["shops"])
        off = settings.validate({"shops": [{"id": "very", "enabled": False}]})
        self.assertIn({"id": "very", "name": "Very.ie", "enabled": False}, off["shops"])


if __name__ == "__main__":
    unittest.main()
