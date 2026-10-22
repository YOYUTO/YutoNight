"""Backend integration checks with local HTTP stubs and synthetic video only."""
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from yutonight.core import Cue, Project
from yutonight.pipeline import PipelineError, _chat, _endpoint, _translations, export_video, probe_video, transcribe, translate_cues


class PipelineTests(unittest.TestCase):
    def test_urls_keep_local_text_local_and_tokens_on_official_domain(self):
        self.assertEqual(_endpoint("ollama", "http://127.0.0.1:1234"), "http://127.0.0.1:1234/api/chat")
        self.assertEqual(_endpoint("deepseek", "https://api.deepseek.com/v1"), "https://api.deepseek.com/chat/completions")
        for provider, url in [("ollama", "https://remote.example"), ("ollama", "http://localhost.evil.example"),
                              ("ollama", "http://secret@localhost"), ("deepseek", "http://api.deepseek.com"),
                              ("deepseek", "https://api.deepseek.com.evil.example"),
                              ("deepseek", "https://api.deepseek.com:444")]:
            with self.subTest(provider=provider, url=url), self.assertRaises(PipelineError):
                _endpoint(provider, url)

    def test_duplicate_missing_and_unexpected_ids_rejected(self):
        for rows in [[{"id": "a", "text": "甲"}, {"id": "a", "text": "乙"}],
                     [{"id": "a", "text": "甲"}],
                     [{"id": "a", "text": "甲"}, {"id": "c", "text": "丙"}],
                     [{"id": "a", "text": "甲"}, {"id": "b", "text": " "}]]:
            with self.subTest(rows=rows), self.assertRaises(PipelineError):
                _translations(json.dumps({"translations": rows}), ["a", "b"])

    def test_reviewed_cues_preserved_and_context_sent_to_local_stub(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body = json.loads(payload["messages"][1]["content"])
                requests.append((self.path, self.headers.get("Authorization"), body))
                rows = [{"id": row["id"], "text": "测试译文"} for row in body["targets"]]
                response = json.dumps({"message": {"content": json.dumps({"translations": rows})}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(response)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cues = [Cue("a", 0, 1, "こんにちは", "人工修订", True), Cue("b", 1, 2, "ありがとう", "旧机器译文")]
            result = translate_cues(cues, "ollama", "test-model", api_key="FAKE_KEY_NOT_SENT",
                                    base_url=f"http://127.0.0.1:{server.server_port}", glossary="名前=姓名")
            self.assertEqual(cues[1].translation, "旧机器译文")
            self.assertEqual(result[0].translation, "人工修订")
            self.assertEqual(result[1].translation, "测试译文")
            self.assertEqual(requests[0][0], "/api/chat")
            self.assertIsNone(requests[0][1])
            self.assertEqual(requests[0][2]["context"][0]["translation"], "人工修订")
            self.assertEqual(requests[0][2]["glossary"], "名前=姓名")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


    def test_http_errors_do_not_expose_body_and_redirects_not_followed(self):
        hits = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(self.path)
                self.rfile.read(int(self.headers["Content-Length"]))
                if self.path == "/redirect":
                    self.send_response(307)
                    self.send_header("Location", "/unexpected")
                else:
                    self.send_response(401)
                self.end_headers()
                self.wfile.write(b"FAKE_PRIVATE_RESPONSE_DO_NOT_LOG")
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for endpoint in ["/denied", "/redirect"]:
                with self.assertRaises(PipelineError) as caught:
                    _chat(f"http://127.0.0.1:{server.server_port}" + endpoint, {}, "FAKE_SESSION_KEY")
                self.assertNotIn("FAKE_PRIVATE", str(caught.exception))
                self.assertNotIn("FAKE_SESSION_KEY", str(caught.exception))
            self.assertEqual(hits, ["/denied", "/redirect"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


    def test_ffmpeg_asr_uses_cpu_fixed_filter_paths_and_preserves_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            video = Path(temporary) / "video.mp4"
            model = Path(temporary) / "model, [name]=safe.bin"
            video.write_bytes(b"synthetic-input")
            model.write_bytes(b"synthetic-model")
            calls = []
            def run(command, **kwargs):
                calls.append((command, kwargs))
                if "filter=whisper" in command:
                    return "whisper AVOptions"
                if "-af" in command:
                    cwd = Path(kwargs["cwd"])
                    self.assertEqual((cwd / "model.bin").read_bytes(), b"synthetic-model")
                    (cwd / "recognized.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n", encoding="utf-8")
                return ""
            with patch("yutonight.pipeline._tool", return_value="ffmpeg"), patch("yutonight.pipeline._run", side_effect=run):
                cues = transcribe(str(video), "", str(model))
            self.assertEqual(cues[0].source, "こんにちは")
            filtered = [command for command, _ in calls if "-af" in command][0]
            expression = filtered[filtered.index("-af") + 1]
            self.assertIn("use_gpu=false", expression)
            self.assertIn("model=model.bin", expression)
            self.assertNotIn(str(model), expression)
            self.assertEqual(model.read_bytes(), b"synthetic-model")

    def test_cli_asr_uses_relative_paths_for_chinese_portable_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "中文 便携目录"
            root.mkdir()
            video, model, cli = root / "视频.mp4", root / "模型.bin", root / "whisper-cli.exe"
            video.write_bytes(b"synthetic-input")
            model.write_bytes(b"synthetic-model")
            cli.write_bytes(b"synthetic-cli")
            def run(command, **kwargs):
                if "-osrt" in command:
                    self.assertEqual(command[command.index("-m") + 1], "model.bin")
                    self.assertEqual(command[command.index("-f") + 1], "audio.wav")
                    self.assertEqual(command[command.index("-of") + 1], "recognized")
                    cwd = Path(kwargs["cwd"])
                    self.assertEqual((cwd / "model.bin").read_bytes(), b"synthetic-model")
                    (cwd / "recognized.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n", encoding="utf-8")
                return ""
            with patch("yutonight.pipeline.paths.PROJECT_ROOT", root), patch("yutonight.pipeline._tool", return_value="ffmpeg"), patch("yutonight.pipeline._run", side_effect=run):
                cues = transcribe(str(video), str(cli), str(model))
            self.assertEqual(cues[0].source, "こんにちは")
            self.assertEqual(model.read_bytes(), b"synthetic-model")

    def test_ffmpeg_asr_missing_filter_is_actionable(self):
        with tempfile.TemporaryDirectory() as temporary:
            video, model = Path(temporary) / "video.mp4", Path(temporary) / "model.bin"
            video.write_bytes(b"test")
            model.write_bytes(b"test")
            with patch("yutonight.pipeline._tool", return_value="ffmpeg"), patch("yutonight.pipeline._run", return_value=""):
                with self.assertRaisesRegex(PipelineError, "whisper"):
                    transcribe(str(video), "", str(model))


    def test_bundled_endpoint_and_tool_preference(self):
        self.assertEqual(_endpoint("bundled", "http://127.0.0.1:1234"), "http://127.0.0.1:1234/v1/chat/completions")
        with self.assertRaises(PipelineError):
            _endpoint("bundled", "https://remote.example")
        with patch("yutonight.pipeline.paths.bundled_tool", return_value="portable-ffmpeg"), patch("yutonight.pipeline.shutil.which", return_value="system-ffmpeg"):
            from yutonight.pipeline import _tool
            self.assertEqual(_tool("ffmpeg"), "portable-ffmpeg")

    def test_bundled_skips_start_without_eligible_cues(self):
        with patch("yutonight.pipeline.managed_translation_server") as managed:
            result = translate_cues([Cue("a", 0, 1, "こんにちは", "你好", True)], "bundled", "")
            self.assertEqual(result[0].translation, "你好")
            managed.assert_not_called()

    def test_bundled_one_server_for_batches_and_cleanup_on_failure(self):
        for fail_last in (False, True):
            events = []
            @contextmanager
            def managed(progress):
                events.append("start")
                try:
                    yield "http://127.0.0.1:12345"
                finally:
                    events.append("stop")
            def chat(endpoint, payload, api_key):
                self.assertEqual(endpoint, "http://127.0.0.1:12345/v1/chat/completions")
                self.assertEqual(api_key, "")
                self.assertEqual(payload["model"], "bundled-translation")
                self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
                targets = json.loads(payload["messages"][1]["content"])["targets"]
                self.assertEqual(payload["response_format"]["schema"]["properties"]["translations"]["minItems"], len(targets))
                rows = [{"id": row["id"], "text": "测试译文"} for row in targets]
                if fail_last and targets[0]["id"] == "12":
                    rows = []
                return {"choices": [{"message": {"content": json.dumps({"translations": rows})}}]}
            cues = [Cue(str(i), i, i + 1, "テスト") for i in range(13)]
            with self.subTest(fail_last=fail_last), patch("yutonight.pipeline.managed_translation_server", side_effect=managed), patch("yutonight.pipeline._chat", side_effect=chat) as mocked:
                if fail_last:
                    with self.assertRaises(PipelineError):
                        translate_cues(cues, "bundled", "", api_key="FAKE_NOT_SENT")
                else:
                    result = translate_cues(cues, "bundled", "", api_key="FAKE_NOT_SENT")
                    self.assertTrue(all(c.translation == "测试译文" for c in result))
                self.assertEqual(mocked.call_count, 2)
            self.assertEqual(events, ["start", "stop"])
            self.assertTrue(all(c.translation == "" for c in cues))

    def test_failed_later_batch_never_mutates_input(self):
        cues = [Cue(str(i), i, i + 1, "テスト") for i in range(13)]
        def respond(endpoint, payload, key):
            targets = json.loads(payload["messages"][1]["content"])["targets"]
            rows = [{"id": item["id"], "text": "测试"} for item in targets]
            if targets[0]["id"] == "12":
                rows = []
            return {"message": {"content": json.dumps({"translations": rows})}}
        with patch("yutonight.pipeline._chat", side_effect=respond), self.assertRaises(PipelineError):
            translate_cues(cues, "ollama", "test")
        self.assertTrue(all(c.translation == "" for c in cues))

    def test_export_refuses_existing_output_and_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.mp4"
            source.write_bytes(b"original")
            project = Project(video_path=str(source))
            for output in [source, Path(temporary) / "existing.mp4"]:
                if output != source:
                    output.write_bytes(b"existing")
                with self.assertRaises(PipelineError):
                    export_video(project, str(output))
            self.assertEqual(source.read_bytes(), b"original")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_real_synthetic_video_bilingual_burn_and_audio(self):
        with tempfile.TemporaryDirectory(prefix="字幕 test ") as temporary:
            source, destination = Path(temporary) / "原始 video.mp4", Path(temporary) / "成片.mp4"
            subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                            "color=c=blue:s=320x240:r=10:d=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source)],
                           check=True, capture_output=True)
            original = source.read_bytes()
            export_video(Project(video_path=str(source), cues=[Cue("a", 0, 0.8, "こんにちは", "你好")]), str(destination))
            info = probe_video(str(destination))
            self.assertTrue(info["has_audio"])
            self.assertEqual(info["width"], 320)
            self.assertAlmostEqual(info["duration"], 1, delta=0.2)
            self.assertEqual(source.read_bytes(), original)
            self.assertGreater(destination.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
