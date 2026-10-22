"""Own the lifetime of the bundled offline translation server."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib import error, parse, request

from . import paths


class LocalRuntimeError(RuntimeError):
    pass


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _loopback_url(base_url: str) -> str:
    try:
        parsed = parse.urlsplit(base_url)
        valid = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
                 and not parsed.username and not parsed.password and parsed.path in {"", "/"}
                 and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise LocalRuntimeError("随包翻译服务仅允许本机回环地址。")
    return base_url.rstrip("/")


def _healthy(base_url: str) -> bool:
    endpoint = _loopback_url(base_url) + "/health"
    opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(endpoint, timeout=2) as response:
            data = json.loads(response.read(65536))
        return isinstance(data, dict) and data.get("status") == "ok"
    except error.HTTPError as exc:
        exc.close()
        return False
    except (error.URLError, OSError, ValueError):
        return False


def _stop(process) -> None:
    if process is None:
        return
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            if process.poll() is None:
                raise LocalRuntimeError("无法停止本地翻译服务，请关闭程序后检查运行进程。") from None
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


@contextmanager
def managed_translation_server(progress=lambda message: None, *, timeout: float = 180):
    """Start one CPU-only server per translation operation and always reap it."""
    server, model = Path(paths.LLAMA_SERVER), Path(paths.TRANSLATION_MODEL)
    if not server.is_file():
        raise LocalRuntimeError("随包翻译运行时缺失，请确认 runtime/llama/llama-server.exe 已完整复制。")
    if not model.is_file():
        raise LocalRuntimeError("随包翻译模型缺失，请确认 models/translation/qwen3-4b-q4_k_m.gguf 已完整复制。")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    command = [str(server.resolve()), "--host", "127.0.0.1", "--port", str(port),
               "--model", str(model.resolve()), "-ngl", "0", "-c", "4096", "--parallel", "1",
               "--alias", "bundled-translation", "--jinja", "--offline", "--no-webui", "--log-disable"]
    # Ignore external llama configuration, logging and credentials; no online models are allowed.
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("LLAMA_")
                   and key.upper() not in {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}}
    process = None
    try:
        progress("正在加载随包本地翻译模型（CPU 基线），首次加载可能较慢…")
        try:
            process = subprocess.Popen(command, cwd=str(server.parent.resolve()), env=environment,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            raise LocalRuntimeError("无法启动随包翻译服务，请检查运行时文件和系统运行库。") from None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise LocalRuntimeError("随包翻译服务提前退出，请检查模型完整性、可用内存和运行库。")
            if _healthy(base_url):
                if process.poll() is not None:
                    raise LocalRuntimeError("随包翻译服务已停止，请重试。")
                progress("随包本地翻译模型已就绪（CPU 基线）。")
                yield base_url
                return
            time.sleep(0.25)
        raise LocalRuntimeError("随包翻译模型加载超时，请检查可用内存或稍后重试。")
    finally:
        _stop(process)
