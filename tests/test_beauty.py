import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

import cv2
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from yutonight.beauty import PRESETS, process_frame, render_video, _video_info


class BeautyTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.full((100, 100, 3), (100, 145, 180), np.uint8)
        self.settings = dict(PRESETS["白净"])

    def apply(self, frame=None, regions=None, time_seconds=0):
        with patch("yutonight.beauty._find_faces", return_value=[(10, 5, 80, 90)]):
            return process_frame(self.frame if frame is None else frame, self.settings, regions or [], time_seconds)

    def test_zero_strength_is_exact_and_returns_copy(self):
        settings = dict(PRESETS["原画"], enabled=True)
        result = process_frame(self.frame, settings, [])
        np.testing.assert_array_equal(result, self.frame)
        self.assertIsNot(result, self.frame)

    def test_face_detector_loads_from_chinese_and_spaced_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "中文 模型目录"
            folder.mkdir()
            filename = "haarcascade_frontalface_default.xml"
            shutil.copyfile(Path(cv2.data.haarcascades) / filename, folder / filename)
            with patch("cv2.data.haarcascades", str(folder) + "/"), patch("yutonight.beauty._DETECTORS", threading.local()):
                result = process_frame(self.frame, self.settings, [])
            np.testing.assert_array_equal(result, self.frame)

    def test_no_face_keeps_original(self):
        with patch("yutonight.beauty._find_faces", return_value=[]):
            np.testing.assert_array_equal(process_frame(self.frame, self.settings, []), self.frame)

    def test_whitening_changes_face_but_not_background(self):
        result = self.apply()
        self.assertGreater(int(result[57, 35].sum()), int(self.frame[57, 35].sum()))
        np.testing.assert_array_equal(result[0:5], self.frame[0:5])

    def test_protected_pixels_are_exact(self):
        regions = [{"x": .2, "y": .5, "w": .2, "h": .2}]
        result = self.apply(regions=regions)
        np.testing.assert_array_equal(result[50:70, 20:40], self.frame[50:70, 20:40])

    def test_protected_colors_cannot_contaminate_neighbors(self):
        regions = [{"x": .25, "y": .5, "w": .15, "h": .15}]
        a, b = self.frame.copy(), self.frame.copy()
        a[50:65, 25:40] = (0, 0, 255)
        b[50:65, 25:40] = (255, 0, 0)
        first, second = self.apply(a, regions), self.apply(b, regions)
        outside = np.ones((100, 100), bool)
        outside[50:65, 25:40] = False
        np.testing.assert_array_equal(first[outside], second[outside])

    def test_region_time_window(self):
        region = {"x": 0, "y": 0, "w": 1, "h": 1, "start": 2, "end": 4}
        np.testing.assert_array_equal(self.apply(regions=[region], time_seconds=3), self.frame)
        self.assertTrue(np.any(self.apply(regions=[region], time_seconds=1) != self.frame))
        self.assertTrue(np.any(self.apply(regions=[region], time_seconds=4) != self.frame))

    def test_invalid_settings_rejected(self):
        for value in (-1, 101, float("nan"), "50", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                process_frame(self.frame, {"enabled": True, "smoothing": value}, [])

    def test_invalid_region_rejected_even_if_disabled(self):
        with self.assertRaises(ValueError):
            process_frame(self.frame, PRESETS["原画"], [{"x": .9, "y": 0, "w": .2, "h": .1}])

    def test_input_is_not_modified(self):
        original = self.frame.copy()
        self.apply()
        np.testing.assert_array_equal(self.frame, original)

    def test_source_cannot_be_overwritten(self):
        with self.assertRaises(ValueError):
            render_video("sample.mp4", "sample.mp4", self.settings, [])


    def test_existing_output_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "existing.mp4"
            output.write_bytes(b"existing-result")
            with self.assertRaises(FileExistsError):
                render_video(Path(temp) / "input.mp4", output, self.settings, [])
            self.assertEqual(output.read_bytes(), b"existing-result")

    def test_hdr_rejected_before_decoding(self):
        for transfer in ("smpte2084", "arib-std-b67"):
            response = SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [{"color_transfer": transfer}]}))
            with self.subTest(transfer=transfer), patch("yutonight.beauty.subprocess.run", return_value=response), self.assertRaisesRegex(RuntimeError, "HDR"):
                _video_info("hdr.mp4", "ffprobe")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
class BeautyVideoTests(unittest.TestCase):
    def test_vfr_render_retains_duration_audio_and_frame_size(self):
        def probe(path):
            result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], capture_output=True, check=True)
            return json.loads(result.stdout)

        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "vfr.mp4", Path(temp) / "rendered.mp4"
            args = ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=12:duration=2", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-vf", "select='gte(t,1)+not(mod(n,2))'", "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)]
            subprocess.run(args, capture_output=True, check=True)
            render_video(source, output, PRESETS["自然"], [])
            before, after = probe(source), probe(output)
            input_video = next(s for s in before["streams"] if s["codec_type"] == "video")
            video = next(s for s in after["streams"] if s["codec_type"] == "video")
            audio = next(s for s in after["streams"] if s["codec_type"] == "audio")
            self.assertEqual((video["width"], video["height"]), (96, 64))
            self.assertEqual(video["avg_frame_rate"], video["r_frame_rate"])
            self.assertGreater(int(video["nb_frames"]), 0)
            numerator, denominator = map(int, input_video["avg_frame_rate"].split("/"))
            tolerance = denominator / numerator + .08
            self.assertLessEqual(abs(float(video["duration"]) - float(input_video["duration"])), tolerance)
            self.assertLessEqual(abs(float(video["duration"]) - float(audio["duration"])), tolerance)
            self.assertEqual(audio["codec_name"], "aac")

    def test_missing_or_short_audio_does_not_truncate_video(self):
        with tempfile.TemporaryDirectory() as temp:
            for with_audio in (False, True):
                with self.subTest(with_audio=with_audio):
                    source = Path(temp) / f"source-{with_audio}.mp4"
                    output = Path(temp) / f"output-{with_audio}.mp4"
                    args = ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=5:duration=1.2"]
                    if with_audio:
                        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=0.25", "-c:a", "aac"]
                    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)]
                    subprocess.run(args, capture_output=True, check=True)
                    render_video(source, output, PRESETS["自然"], [])
                    info = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)], capture_output=True, check=True)
                    streams = json.loads(info.stdout)["streams"]
                    video = next(s for s in streams if s["codec_type"] == "video")
                    self.assertGreaterEqual(float(video["duration"]), 1.19)
                    self.assertEqual(any(s["codec_type"] == "audio" for s in streams), with_audio)

if __name__ == "__main__":
    unittest.main()
