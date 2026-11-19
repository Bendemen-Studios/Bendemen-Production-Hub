import math
import unittest

from app.main import clean_text, public_agent, safe_number


class HubStatusTests(unittest.TestCase):
    def test_safe_number_rejects_nan_infinity_and_boolean(self):
        self.assertIsNone(safe_number(float("nan")))
        self.assertIsNone(safe_number(float("inf")))
        self.assertIsNone(safe_number(True))
        self.assertEqual(safe_number(21.5), 21.5)

    def test_clean_text_limits_untrusted_strings(self):
        self.assertEqual(clean_text("  AD5X  "), "AD5X")
        self.assertEqual(len(clean_text("x" * 200, 32)), 32)

    def test_public_agent_keeps_offline_printer_visible(self):
        agent = public_agent(
            "ad5x-home",
            {"printer_name": "Flashforge AD5X", "printer_status": "printing", "progress": 0.4},
            "2026-11-19T12:00:00+00:00",
            False,
        )
        self.assertFalse(agent["online"])
        self.assertEqual(agent["agent_id"], "ad5x-home")
        self.assertEqual(agent["progress"], 0.4)
        self.assertEqual(agent["printer_name"], "Flashforge AD5X")


if __name__ == "__main__":
    unittest.main()
