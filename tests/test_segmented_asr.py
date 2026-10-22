"""Segmented recognition timing, incremental delivery and synthetic audio boundaries."""
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from unittest.mock import patch

from yutonight import pipeline


def write_audio(path, seconds, quiet=None):
    rate = 16000
    frames = bytearray()
    for frame in range(round(seconds * rate)):
        time = frame / rate
        value = 0 if quiet and quiet[0] <= time < quiet[1] else (4000 if frame % 2 else -4000)
        frames.extend(struct.pack("<h", value))
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        audio.writeframes(frames)


class SegmentedASRTests(unittest.TestCase):
    def test_results_are_incremental_and_have_global_times_and_unique_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "中文 便携目录"
            root.mkdir()
            video, model, cli = (root / name for name in ("video.mp4", "model.bin", "whisper-cli.exe"))
            for file in (video, model, cli):
                file.write_bytes(b"synthetic")
            extractions, recognition = [], []
            def run(command, **kwargs):
                if "-c:a" in command:
                    extractions.append(float(command[command.index("-ss") + 1]))
                    write_audio(command[-1], float(command[command.index("-t") + 1]))
                elif "-osrt" in command:
                    recognition.append(command)
                    self.assertEqual(command[command.index("-m") + 1], "model.bin")
                    self.assertEqual(command[command.index("-f") + 1], "audio.wav")
                    (Path(kwargs["cwd"]) / "recognized.srt").write_text(
                        f"1\n00:00:00,500 --> 00:00:01,500\nsegment {len(recognition)}\n", encoding="utf-8")
                return ""
            with patch("yutonight.pipeline.paths.PROJECT_ROOT", root), patch("yutonight.pipeline.probe_video", return_value={"duration": 70, "has_audio": True}), patch("yutonight.pipeline._tool", return_value="ffmpeg"), patch("yutonight.pipeline._run", side_effect=run), patch("yutonight.pipeline.os.link", wraps=pipeline.os.link) as link:
                iterator = pipeline.transcribe_segments(str(video), str(cli), str(model))
                first = next(iterator)
                self.assertEqual(len(recognition), 1)
                self.assertEqual(extractions, [0])
                rest = list(iterator)
                cues = first + [cue for batch in rest for cue in batch]
                self.assertEqual([cue.start for cue in cues], [.5, 30.5, 60.5])
                self.assertEqual([cue.end for cue in cues], [1.5, 31.5, 61.5])
                self.assertEqual(len({cue.id for cue in cues}), 3)
                self.assertEqual(extractions, [0, 30, 60])
                self.assertEqual(link.call_count, 1)
            self.assertFalse(list((root / "work").iterdir()))

    def test_silence_boundary_and_clipping_cover_adjacent_segments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video, model, cli = (root / name for name in ("video.mp4", "model.bin", "whisper-cli.exe"))
            for file in (video, model, cli):
                file.write_bytes(b"synthetic")
            starts, lengths = [], []
            def run(command, **kwargs):
                if "-c:a" in command:
                    start = float(command[command.index("-ss") + 1])
                    starts.append(start)
                    write_audio(command[-1], float(command[command.index("-t") + 1]), (29, 29.8) if start == 0 else None)
                elif "-osrt" in command:
                    with wave.open(str(Path(kwargs["cwd"]) / "audio.wav"), "rb") as audio:
                        lengths.append(audio.getnframes() / audio.getframerate())
                    (Path(kwargs["cwd"]) / "recognized.srt").write_text("1\n00:00:00,000 --> 00:00:45,000\n説明\n", encoding="utf-8")
                return ""
            with patch("yutonight.pipeline.paths.PROJECT_ROOT", root), patch("yutonight.pipeline.probe_video", return_value={"duration": 40, "has_audio": True}), patch("yutonight.pipeline._tool", return_value="ffmpeg"), patch("yutonight.pipeline._run", side_effect=run):
                batches = list(pipeline.transcribe_segments(str(video), str(cli), str(model)))
            self.assertAlmostEqual(starts[1], 29.4, places=2)
            self.assertAlmostEqual(lengths[0], starts[1], places=3)
            self.assertEqual(batches[0][0].end, batches[1][0].start)
            self.assertEqual(batches[-1][0].end, 40)

    def test_empty_segment_does_not_abort_and_failure_retains_previous_yields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video, model, cli = (root / name for name in ("video.mp4", "model.bin", "whisper-cli.exe"))
            for file in (video, model, cli):
                file.write_bytes(b"synthetic")
            count = 0
            def run(command, **kwargs):
                nonlocal count
                if "-c:a" in command:
                    write_audio(command[-1], float(command[command.index("-t") + 1]))
                elif "-osrt" in command:
                    count += 1
                    if count == 3:
                        raise pipeline.PipelineError("synthetic later failure")
                    text = "" if count == 1 else "1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n"
                    (Path(kwargs["cwd"]) / "recognized.srt").write_text(text, encoding="utf-8")
                return ""
            with patch("yutonight.pipeline.paths.PROJECT_ROOT", root), patch("yutonight.pipeline.probe_video", return_value={"duration": 70, "has_audio": True}), patch("yutonight.pipeline._tool", return_value="ffmpeg"), patch("yutonight.pipeline._run", side_effect=run):
                iterator = pipeline.transcribe_segments(str(video), str(cli), str(model))
                self.assertEqual(next(iterator), [])
                completed = next(iterator)
                with self.assertRaisesRegex(pipeline.PipelineError, "synthetic later failure"):
                    next(iterator)
            self.assertEqual(completed[0].start, 30)
            self.assertEqual(completed[0].source, "こんにちは")
            self.assertFalse(list((root / "work").iterdir()))

    def test_all_empty_segments_report_no_dialogue_and_no_audio_is_actionable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video, model, cli = (root / name for name in ("video.mp4", "model.bin", "whisper-cli.exe"))
            for file in (video, model, cli):
                file.write_bytes(b"synthetic")
            def run(command, **kwargs):
                if "-c:a" in command:
                    write_audio(command[-1], 2)
                elif "-osrt" in command:
                    (Path(kwargs["cwd"]) / "recognized.srt").write_text("", encoding="utf-8")
                return ""
            with patch("yutonight.pipeline.paths.PROJECT_ROOT", root), patch("yutonight.pipeline.probe_video", return_value={"duration": 2, "has_audio": True}), patch("yutonight.pipeline._tool", return_value="ffmpeg"), patch("yutonight.pipeline._run", side_effect=run):
                iterator = pipeline.transcribe_segments(str(video), str(cli), str(model))
                self.assertEqual(next(iterator), [])
                with self.assertRaisesRegex(pipeline.PipelineError, "未识别到对白"):
                    next(iterator)
            with patch("yutonight.pipeline.probe_video", return_value={"duration": 2, "has_audio": False}):
                with self.assertRaisesRegex(pipeline.PipelineError, "音轨"):
                    list(pipeline.transcribe_segments(str(video), str(cli), str(model)))

    def test_empty_audio_tail_does_not_run_engine_on_empty_wav(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video, model, cli = (root / name for name in ("video.mp4", "model.bin", "whisper-cli.exe"))
            for file in (video, model, cli):
                file.write_bytes(b"synthetic")
            recognized = []
            def run(command, **kwargs):
                if "-c:a" in command:
                    start = float(command[command.index("-ss") + 1])
                    write_audio(command[-1], 32 if start == 0 else 0)
                elif "-osrt" in command:
                    with wave.open(str(Path(kwargs["cwd"]) / "audio.wav"), "rb") as audio:
                        self.assertGreater(audio.getnframes(), 0)
                    recognized.append(True)
                    (Path(kwargs["cwd"]) / "recognized.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nテスト\n", encoding="utf-8")
                return ""
            with patch("yutonight.pipeline.paths.PROJECT_ROOT", root), patch("yutonight.pipeline.probe_video", return_value={"duration": 40, "has_audio": True}), patch("yutonight.pipeline._tool", return_value="ffmpeg"), patch("yutonight.pipeline._run", side_effect=run):
                batches = list(pipeline.transcribe_segments(str(video), str(cli), str(model)))
            self.assertEqual(len(recognized), 1)
            self.assertEqual(batches[1], [])

    def test_later_missing_output_cannot_reuse_previous_srt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video, model, cli = (root / name for name in ("video.mp4", "model.bin", "whisper-cli.exe"))
            for file in (video, model, cli):
                file.write_bytes(b"synthetic")
            count = 0
            def run(command, **kwargs):
                nonlocal count
                if "-c:a" in command:
                    write_audio(command[-1], float(command[command.index("-t") + 1]))
                elif "-osrt" in command:
                    count += 1
                    if count == 1:
                        (Path(kwargs["cwd"]) / "recognized.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nテスト\n", encoding="utf-8")
                return ""
            with patch("yutonight.pipeline.paths.PROJECT_ROOT", root), patch("yutonight.pipeline.probe_video", return_value={"duration": 40, "has_audio": True}), patch("yutonight.pipeline._tool", return_value="ffmpeg"), patch("yutonight.pipeline._run", side_effect=run):
                iterator = pipeline.transcribe_segments(str(video), str(cli), str(model))
                self.assertEqual(next(iterator)[0].start, 0)
                with self.assertRaisesRegex(pipeline.PipelineError, "没有生成 SRT"):
                    next(iterator)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_real_ffmpeg_synthetic_video_uses_silence_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio, video, model, cli = (root / name for name in ("source.wav", "video.mp4", "model.bin", "whisper-cli.exe"))
            write_audio(audio, 35, (29, 29.8))
            subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "color=s=16x16:r=1:d=35", "-i", str(audio), "-c:v", "libx264", "-c:a", "pcm_s16le", "-f", "matroska", str(video)], check=True, capture_output=True)
            model.write_bytes(b"synthetic")
            cli.write_bytes(b"synthetic")
            original_run = pipeline._run
            starts = []
            def run(command, **kwargs):
                if "-osrt" not in command:
                    return original_run(command, **kwargs)
                with wave.open(str(Path(kwargs["cwd"]) / "audio.wav"), "rb") as wav:
                    starts.append(wav.getnframes() / wav.getframerate())
                (Path(kwargs["cwd"]) / "recognized.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nテスト\n", encoding="utf-8")
                return ""
            with patch("yutonight.pipeline.paths.PROJECT_ROOT", root), patch("yutonight.pipeline._run", side_effect=run):
                batches = list(pipeline.transcribe_segments(str(video), str(cli), str(model)))
            self.assertEqual(len(batches), 2)
            self.assertAlmostEqual(starts[0], 29.4, places=2)
            self.assertAlmostEqual(batches[1][0].start, 29.4, places=2)
            self.assertAlmostEqual(sum(starts), 35, places=2)
