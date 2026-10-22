"""Read-only portable environment check; no credentials or network requests."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

from . import paths


def report():
    root = paths.PROJECT_ROOT
    ffmpeg = paths.bundled_tool("ffmpeg") or shutil.which("ffmpeg")
    ffprobe = paths.bundled_tool("ffprobe") or shutil.which("ffprobe")
    filters = ""
    if ffmpeg:
        try:
            result = subprocess.run([ffmpeg, "-hide_banner", "-filters"],
                                    capture_output=True, timeout=15,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            filters = result.stdout.decode("utf8", "replace")
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {
        "workspace": str(root),
        "python": sys.version.split()[0],
        "dependencies": {name: importlib.util.find_spec(name) is not None
                         for name in ("PySide6", "numpy", "cv2")},
        "ffmpeg": bool(ffmpeg),
        "ffprobe": bool(ffprobe),
        "ffmpeg_whisper_filter": any(line.split()[1:2] == ["whisper"]
                                      for line in filters.splitlines()),
        "local_asr_models": [p.name for p in (root / "models").rglob("ggml-*.bin")],
        "bundled_asr_runtime": bool(paths.bundled_tool("whisper-cli")),
        "bundled_asr_model": paths.ASR_MODEL.is_file(),
        "bundled_translation_runtime": paths.LLAMA_SERVER.is_file(),
        "bundled_translation_model": paths.TRANSLATION_MODEL.is_file(),
        "beauty_backend": "OpenCV CPU experimental",
        "gpu_acceleration_verified": False,
        "translation": "Project-local model, Ollama, or DeepSeek. No translation request made by self-check.",
    }


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report(), ensure_ascii=False, indent=2))
