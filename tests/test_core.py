import json
from pathlib import Path
import tempfile
import unittest

from yutonight.core import Cue, Project, format_srt, load_project, parse_srt, save_project


class ProjectTests(unittest.TestCase):
    def test_roundtrip_preserves_manual_correction_and_regions(self):
        project = Project("D:/素材/影片.mp4", [Cue("a", 1.125, 2.875, "こんにちは", "你好", True)],
                          {"enabled": True, "smoothing": 30, "whitening": 10, "sharpening": 0},
                          [{"x": .1, "y": .2, "w": .2, "h": .3, "start": 1, "end": 4}])
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "project.json"
            save_project(project, target)
            self.assertEqual(load_project(target), project)
            self.assertNotIn("api_key", target.read_text(encoding="utf8"))

    def test_bad_edit_does_not_destroy_existing_project(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "project.json"
            project = Project(cues=[Cue("a", 0, 1, "hello")])
            save_project(project, target)
            saved = target.read_bytes()
            project.cues[0].end = -1
            with self.assertRaises(ValueError):
                save_project(project, target)
            self.assertEqual(target.read_bytes(), saved)

    def test_original_video_cannot_be_project_save_target(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "video.mp4"
            target.write_bytes(b"original")
            with self.assertRaises(ValueError):
                save_project(Project(str(target)), target)
            self.assertEqual(target.read_bytes(), b"original")

    def test_rejects_invalid_schema_and_nonfinite_times(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "project.json"
            for payload in (
                {"schema_version": 99},
                {"schema_version": 1, "api_key": "FAKE_TEST_VALUE"},
                {"schema_version": 1, "cues": [{"id": "a", "start": float("nan"), "end": 1}]},
                {"schema_version": 1, "regions": [{"x": .9, "y": 0, "w": .2, "h": .2}]},
            ):
                target.write_text(json.dumps(payload), encoding="utf8")
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    load_project(target)

    def test_rejects_duplicate_ids_and_unknown_beauty_fields(self):
        for project in (
            Project(cues=[Cue("a", 0, 1), Cue("a", 1, 2)]),
            Project(beauty={"enabled": True, "api_key": "FAKE_TEST_VALUE"}),
        ):
            with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
                save_project(project, Path(folder) / "p.json")


class SrtTests(unittest.TestCase):
    def test_bom_crlf_and_multiline_subtitle(self):
        parsed = parse_srt("\ufeff1\r\n00:00:01,500 --> 00:00:03,025\r\nこんにちは\r\n今日は晴れです\r\n")
        self.assertEqual(parsed[0].start, 1.5)
        self.assertEqual(parsed[0].end, 3.025)
        self.assertEqual(parsed[0].source, "こんにちは\n今日は晴れです")

    def test_bilingual_export_and_timestamp_carry(self):
        text = format_srt([Cue("a", 59.9996, 61, "こんにちは", "你好", True)])
        self.assertIn("00:01:00,000 --> 00:01:01,000", text)
        self.assertIn("こんにちは\n你好", text)
        self.assertEqual(parse_srt(text)[0].source, "こんにちは\n你好")

    def test_translation_only_does_not_substitute_source(self):
        self.assertEqual(format_srt([Cue("a", 0, 1, "hello")], mode="translation"), "")

    def test_invalid_srt_is_rejected(self):
        for text in ("garbage", "1\n00:00:02,000 --> 00:00:01,000\nx",
                     "1\n00:99:00,000 --> 00:99:01,000\nx"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_srt(text)


if __name__ == "__main__":
    unittest.main()
