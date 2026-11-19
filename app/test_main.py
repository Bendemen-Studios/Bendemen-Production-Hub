import sqlite3
import unittest

from app.main import clean_text, public_agent, record_print_update, safe_number


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


if __name__ == "__main__":
    unittest.main()
