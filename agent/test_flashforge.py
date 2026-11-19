import unittest

from flashforge import FlashForgeError, normalize_detail


class NormalizeDetailTests(unittest.TestCase):
    def test_normalizes_ad5x_status_without_leaking_camera_url(self):
        data = normalize_detail({
            "pid": 38,
            "name": "AD5X",
            "status": "printing",
            "printProgress": 0.5,
            "rightTemp": 205,
            "rightTargetTemp": 210,
            "platTemp": 58,
            "platTargetTemp": 60,
            "printLayer": 20,
            "targetPrintLayer": 40,
            "estimatedTime": 600,
            "cameraStreamUrl": "http://192.168.1.50:8080/?action=stream",
        }, "192.168.1.50")
        self.assertEqual(data["progress"], 0.5)
        self.assertEqual(data["printer_status"], "printing")
        self.assertTrue(data["camera_available"])
        self.assertNotIn("cameraStreamUrl", data)
        self.assertNotIn("192.168.1.50:8080", str(data))

    def test_rejects_invalid_progress(self):
        data = normalize_detail({"printProgress": 4}, "127.0.0.1")
        self.assertIsNone(data["progress"])


if __name__ == "__main__":
    unittest.main()
