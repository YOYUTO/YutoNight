"""Portable runtime lifecycle checks; no real model or external network required."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from yutonight import local_runtime
from yutonight.local_runtime import LocalRuntimeError, managed_translation_server


class FakeProcess:
    def __init__(self, *, exited=False, slow_stop=False):
        self.exited = exited
        self.slow_stop = slow_stop
        self.terminated = False
        self.killed = False
        self.waited = 0

    def poll(self):
        return 1 if self.exited else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout):
        self.waited += 1
        if self.slow_stop and self.waited == 1:
            raise subprocess.TimeoutExpired("test-server", timeout)
        self.exited = True
        return 0


class LocalRuntimeTests(unittest.TestCase):
    def test_missing_assets_fail_before_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            server = Path(temporary) / "llama-server.exe"
            model = Path(temporary) / "model.gguf"
            with patch.object(local_runtime.paths, "LLAMA_SERVER", server), patch.object(local_runtime.paths, "TRANSLATION_MODEL", model):
                with patch.object(local_runtime.subprocess, "Popen") as popen:
                    with self.assertRaisesRegex(LocalRuntimeError, "运行时缺失"):
                        with managed_translation_server():
                            pass
                    server.write_bytes(b"test")
                    with self.assertRaisesRegex(LocalRuntimeError, "模型缺失"):
                        with managed_translation_server():
                            pass
                    popen.assert_not_called()

    def test_loopback_guard_rejects_remote_credentials_and_paths(self):
        for url in ["https://127.0.0.1:1234", "http://remote.example:1234", "http://127.0.0.1.evil:1234",
                    "http://secret@127.0.0.1:1234", "http://127.0.0.1:1234/other", "http://127.0.0.1:invalid"]:
            with self.subTest(url=url), self.assertRaises(LocalRuntimeError):
                local_runtime._healthy(url)
        self.assertEqual(local_runtime._loopback_url("http://127.0.0.1:1234/"), "http://127.0.0.1:1234")

    def test_health_local_http_and_no_redirect(self):
        statuses = [200, 503, 302]
        hits = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                status = statuses.pop(0)
                self.send_response(status)
                if status == 302:
                    self.send_header("Location", "/unexpected")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            self.assertTrue(local_runtime._healthy(url))
            self.assertFalse(local_runtime._healthy(url))
            self.assertFalse(local_runtime._healthy(url))
            self.assertEqual(hits, ["/health"] * 3)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_cleanup_success_body_failure_start_failure_and_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            server, model = Path(temporary) / "llama-server.exe", Path(temporary) / "model.gguf"
            server.write_bytes(b"test")
            model.write_bytes(b"test")
            with patch.object(local_runtime.paths, "LLAMA_SERVER", server), patch.object(local_runtime.paths, "TRANSLATION_MODEL", model):
                for scenario in ("success", "body_error", "timeout", "exited", "slow_stop"):
                    process = FakeProcess(exited=scenario == "exited", slow_stop=scenario == "slow_stop")
                    with self.subTest(scenario=scenario), patch.object(local_runtime.subprocess, "Popen", return_value=process) as popen:
                        with patch.object(local_runtime, "_healthy", return_value=True):
                            with patch.dict(local_runtime.os.environ, {"LLAMA_API_KEY": "FAKE_NOT_FOR_CHILD", "LLAMA_ARG_MODEL_URL": "https://remote.invalid"}):
                                if scenario in {"timeout", "exited"}:
                                    with self.assertRaises(LocalRuntimeError):
                                        with managed_translation_server(timeout=0 if scenario == "timeout" else 1):
                                            self.fail("unready server yielded")
                                elif scenario == "body_error":
                                    with self.assertRaisesRegex(ValueError, "test body"):
                                        with managed_translation_server():
                                            raise ValueError("test body")
                                else:
                                    with managed_translation_server() as url:
                                        self.assertTrue(url.startswith("http://127.0.0.1:"))
                        command = popen.call_args.args[0]
                        self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
                        self.assertEqual(command[command.index("-ngl") + 1], "0")
                        self.assertIn("--offline", command)
                        self.assertNotIn("LLAMA_API_KEY", popen.call_args.kwargs["env"])
                        self.assertNotIn("LLAMA_ARG_MODEL_URL", popen.call_args.kwargs["env"])
                        self.assertGreater(process.waited, 0)
                        if scenario != "exited":
                            self.assertTrue(process.terminated)
                        if scenario == "slow_stop":
                            self.assertTrue(process.killed)


if __name__ == "__main__":
    unittest.main()
