import sqlite3
import unittest

from app.main import build_filament_inventory, calculate_monthly_totals, calculate_sale_totals, clean_text, normalize_filament, public_agent, record_print_update, safe_number


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


class PrintHistoryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""CREATE TABLE print_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,
            file_name TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
            result TEXT, last_progress REAL
        )""")

    def tearDown(self):
        self.conn.close()

    def test_records_and_completes_a_print(self):
        record_print_update(self.conn, "ad5x-home", {
            "printer_status": "printing", "file_name": "part.3mf", "progress": 0.2
        }, "2026-11-19T10:00:00+00:00")
        record_print_update(self.conn, "ad5x-home", {
            "printer_status": "printing", "file_name": "part.3mf", "progress": 1.0
        }, "2026-11-19T10:10:00+00:00")
        record_print_update(self.conn, "ad5x-home", {
            "printer_status": "idle", "file_name": "part.3mf", "progress": 1.0
        }, "2026-11-19T10:11:00+00:00")
        row = self.conn.execute("SELECT * FROM print_jobs").fetchone()
        self.assertEqual(row["file_name"], "part.3mf")
        self.assertEqual(row["result"], "completed")
        self.assertEqual(row["started_at"], "2026-11-19T10:00:00+00:00")
        self.assertIsNotNone(row["ended_at"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 1)

    def test_new_file_closes_previous_job_as_interrupted(self):
        record_print_update(self.conn, "ad5x-home", {
            "printer_status": "printing", "file_name": "first.3mf", "progress": 0.4
        }, "2026-11-19T10:00:00+00:00")
        record_print_update(self.conn, "ad5x-home", {
            "printer_status": "printing", "file_name": "second.3mf", "progress": 0.01
        }, "2026-11-19T10:01:00+00:00")
        rows = self.conn.execute("SELECT * FROM print_jobs ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["result"], "interrupted")
        self.assertIsNotNone(rows[0]["ended_at"])
        self.assertIsNone(rows[1]["ended_at"])
        self.assertEqual(rows[1]["file_name"], "second.3mf")



class FilamentInventoryTests(unittest.TestCase):
    def valid_filament(self):
        return {
            "name": "eSUN PLA+ Black", "brand": "eSUN", "material": "PLA+",
            "color": "#222222", "spool_weight_g": 1000,
            "remaining_weight_g": 735, "notes": "Dry storage",
            "shopping_url": "https://example.com/products/esun-pla-plus", "purchase_price_eur": 24.99,
        }

    def test_normalizes_valid_filament(self):
        item = normalize_filament(self.valid_filament())
        self.assertEqual(item["name"], "eSUN PLA+ Black")
        self.assertEqual(item["material"], "PLA+")
        self.assertEqual(item["remaining_weight_g"], 735.0)
        self.assertEqual(item["purchase_price_eur"], 24.99)
        self.assertEqual(item["low_stock_threshold_g"], 200.0)

    def test_rejects_remaining_weight_above_spool_weight(self):
        item = self.valid_filament()
        item["remaining_weight_g"] = 1001
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            normalize_filament(item)

    def test_rejects_unknown_material(self):
        item = self.valid_filament()
        item["material"] = "MAGIC"
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            normalize_filament(item)

    def test_rejects_boolean_weight(self):
        item = self.valid_filament()
        item["remaining_weight_g"] = True
        with self.assertRaises(ValueError):
            normalize_filament(item)


    def test_accepts_https_shopping_link(self):
        item = normalize_filament(self.valid_filament())
        self.assertEqual(item["shopping_url"], "https://example.com/products/esun-pla-plus")

    def test_shopping_link_is_optional(self):
        data = self.valid_filament()
        data.pop("shopping_url")
        self.assertEqual(normalize_filament(data)["shopping_url"], "")

    def test_rejects_non_http_shopping_link(self):
        data = self.valid_filament()
        data["shopping_url"] = "javascript:alert(1)"
        with self.assertRaisesRegex(ValueError, "HTTP or HTTPS"):
            normalize_filament(data)

    def test_rejects_url_with_embedded_credentials(self):
        data = self.valid_filament()
        data["shopping_url"] = "https://user:pass@example.com/item"
        with self.assertRaisesRegex(ValueError, "HTTP or HTTPS"):
            normalize_filament(data)



    def test_custom_low_stock_threshold_is_saved(self):
        data = self.valid_filament()
        data["low_stock_threshold_g"] = 150
        self.assertEqual(normalize_filament(data)["low_stock_threshold_g"], 150.0)

    def test_rejects_negative_low_stock_threshold(self):
        data = self.valid_filament()
        data["low_stock_threshold_g"] = -1
        with self.assertRaisesRegex(ValueError, "Minimum stock threshold"):
            normalize_filament(data)

    def test_rejects_negative_purchase_price(self):
        data = self.valid_filament()
        data["purchase_price_eur"] = -1
        with self.assertRaisesRegex(ValueError, "Purchase price"):
            normalize_filament(data)

    def test_rejects_boolean_purchase_price(self):
        data = self.valid_filament()
        data["purchase_price_eur"] = True
        with self.assertRaisesRegex(ValueError, "Purchase price"):
            normalize_filament(data)



class SalesCalculationTests(unittest.TestCase):
    def test_calculates_total_cost_and_profit(self):
        result = calculate_sale_totals(4.99, 1.06, 0.33)
        self.assertEqual(result["material_cost_eur"], 1.06)
        self.assertEqual(result["total_costs_eur"], 1.39)
        self.assertEqual(result["profit_eur"], 3.60)

    def test_supports_loss_making_print(self):
        result = calculate_sale_totals(1.00, 1.50, 0.25)
        self.assertEqual(result["profit_eur"], -0.75)

    def test_rejects_invalid_cost_values(self):
        with self.assertRaisesRegex(ValueError, "non-negative"):
            calculate_sale_totals(4.99, float("nan"), 0.10)


class InventoryAndMonthlyReportTests(unittest.TestCase):
    def test_groups_multiple_spools_and_calculates_remaining_value(self):
        base = {
            "name": "PLA+ Black", "brand": "eSUN", "material": "PLA+",
            "color": "Black", "spool_weight_g": 1000,
            "purchase_price_eur": 20.0, "low_stock_threshold_g": 200,
        }
        first = {**base, "remaining_weight_g": 100, "id": 1}
        second = {**base, "remaining_weight_g": 500, "id": 2}
        filaments, products, summary = build_filament_inventory([first, second])
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["spools"], 2)
        self.assertEqual(products[0]["remaining_grams"], 600)
        self.assertEqual(products[0]["remaining_value_eur"], 12.0)
        self.assertEqual(products[0]["low_stock_spools"], 1)
        self.assertEqual(summary["remaining_value_eur"], 12.0)
        self.assertEqual(summary["low_stock_spools"], 1)
        self.assertEqual(filaments[0]["remaining_value_eur"], 2.0)

    def test_calculates_monthly_profit(self):
        result = calculate_monthly_totals({
            "revenue_eur": 100.0, "material_cost_eur": 25.5, "extra_costs_eur": 10.0
        })
        self.assertEqual(result["total_costs_eur"], 35.5)
        self.assertEqual(result["profit_eur"], 64.5)

    def test_monthly_report_rejects_non_finite_values(self):
        with self.assertRaisesRegex(ValueError, "non-negative finite"):
            calculate_monthly_totals({
                "revenue_eur": 100.0, "material_cost_eur": float("nan"), "extra_costs_eur": 10.0
            })

if __name__ == "__main__":
    unittest.main()
