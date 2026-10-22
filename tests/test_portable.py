import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from yutonight.portable_entry import self_check


class PortableEntryTests(unittest.TestCase):
    def test_self_check_is_offline_and_records_runtime_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "nested" / "report.json"
            with patch("urllib.request.urlopen", side_effect=AssertionError("self-check must not use the network")):
                self.assertEqual(self_check(destination), 0)
            result = json.loads(destination.read_text(encoding="utf-8"))
            self.assertTrue(result["haar_cascade"])
            self.assertTrue(result["qt_multimedia"])
            self.assertTrue(result["beauty_import"])
            self.assertFalse(result["frozen"])

    def test_self_check_does_not_export_api_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "report.json"
            with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "TEST_ONLY_NOT_A_REAL_SECRET"}):
                self_check(destination)
            text = destination.read_text(encoding="utf-8")
            self.assertNotIn("TEST_ONLY_NOT_A_REAL_SECRET", text)
            self.assertNotIn("DEEPSEEK_API_KEY", text)


if __name__ == "__main__":
    unittest.main()
