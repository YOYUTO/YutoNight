"""Resolve portable assets beside the project, independent of the working directory."""
from pathlib import Path
import sys


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        # runtime/app/YutoNight.exe -> project root
        return Path(sys.executable).resolve().parents[2]
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = application_root()
ASR_MODEL = PROJECT_ROOT / "models" / "asr" / "ggml-large-v3-turbo-q5_0.bin"
TRANSLATION_MODEL = PROJECT_ROOT / "models" / "translation" / "qwen3-4b-q4_k_m.gguf"
LLAMA_SERVER = PROJECT_ROOT / "runtime" / "llama" / "llama-server.exe"


def bundled_tool(name: str) -> str | None:
    folders = {"ffmpeg": "ffmpeg", "ffprobe": "ffmpeg", "whisper-cli": "whisper",
               "llama-server": "llama"}
    if name not in folders:
        return None
    directory = PROJECT_ROOT / "runtime" / folders[name]
    filename = name + ".exe"
    for candidate in (directory / filename, directory / "bin" / filename):
        if candidate.is_file():
            return str(candidate)
    if directory.is_dir():
        matches = list(directory.rglob(filename))
        if len(matches) == 1:
            return str(matches[0])
    return None
