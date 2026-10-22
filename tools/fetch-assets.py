"""Download pinned portable assets; no account, token or environment configuration."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
from urllib import request, error
import zipfile

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "work" / "asset-downloads"
ASSETS = [
    {"name": "whisper-model", "path": "models/asr/ggml-large-v3-turbo-q5_0.bin",
     "repo": "ggerganov/whisper.cpp", "file": "ggml-large-v3-turbo-q5_0.bin",
     "size": 574041195, "sha256": "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"},
    {"name": "translation-model", "path": "models/translation/qwen3-4b-q4_k_m.gguf",
     "repo": "Qwen/Qwen3-4B-GGUF", "file": "Qwen3-4B-Q4_K_M.gguf",
     "size": 2497280256, "sha256": "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"},
    {"name": "llama-runtime", "path": "work/assets/llama-b11804-win-cpu-x64.zip",
     "url": "https://github.com/ggml-org/llama.cpp/releases/download/b11804/llama-b11804-bin-win-cpu-x64.zip",
     "sha256": "3a3e417a0816329829081bdd443e3045ff3b2dbe866fcef89a1d919e941ce23f",
     "extract": "runtime/llama"},
    {"name": "whisper-runtime", "path": "work/assets/whisper-b5454-win-x64.zip",
     "url": "https://github.com/ggml-org/whisper.cpp/releases/download/b5454/whisper-bin-x64.zip",
     "sha256": "6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb",
     "extract": "runtime/whisper"},
]


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(data)
    return result.hexdigest()


def opener():
    return request.build_opener(request.ProxyHandler({}))


def extract(asset, filename):
    folder = ROOT / asset["extract"]
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(filename) as archive:
        for entry in archive.infolist():
            name = entry.filename.replace("\\", "/")
            target = (folder / name).resolve()
            if not target.is_relative_to(folder.resolve()):
                raise RuntimeError("Archive contains an invalid path")
        archive.extractall(folder)
    print(asset["name"], "runtime ready", flush=True)


def fetch(asset, mirror=False, modelscope=False):
    output = ROOT / asset["path"]
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if digest(output) == asset["sha256"]:
            print(asset["name"], "already verified", flush=True)
            if asset.get("extract"):
                extract(asset, output)
            return
        raise RuntimeError(asset["name"] + ": existing file has wrong SHA256; kept untouched")
    if "repo" in asset:
        host = "https://hf-mirror.com" if mirror else "https://huggingface.co"
        url = host + "/" + asset["repo"] + "/resolve/main/" + asset["file"]
        if modelscope and asset["repo"].startswith("Qwen/"):
            url = "https://modelscope.cn/models/" + asset["repo"] + "/resolve/master/" + asset["file"]
    else:
        url = asset["url"]
    partial = output.with_name(output.name + ".part")
    size = asset.get("size")
    start_time = time.monotonic()
    if size:
        # Separate completed ranges can be reused after an interrupted transfer.
        pieces = CACHE / asset["sha256"]
        pieces.mkdir(parents=True, exist_ok=True)
        chunk_size = 32 * 1024 * 1024
        count = math.ceil(size / chunk_size)
        done = 0
        lock = threading.Lock()
        def chunk(index):
            nonlocal done
            start = index * chunk_size
            end = min(size, start + chunk_size) - 1
            target = pieces / f"{index:04d}.chunk"
            length = end - start + 1
            if target.exists() and target.stat().st_size == length:
                with lock:
                    done += length
                return
            for attempt in range(3):
                try:
                    existing = target.stat().st_size if target.exists() else 0
                    if existing > length:
                        existing = 0
                    range_start = start + existing
                    req = request.Request(url, headers={"Range": f"bytes={range_start}-{end}", "User-Agent": "YutoNight/0.2"})
                    with opener().open(req, timeout=45) as response:
                        content_range = response.headers.get("Content-Range", "")
                        if response.status != 206 or content_range != f"bytes {range_start}-{end}/{size}":
                            raise RuntimeError("Download server does not honor exact byte ranges")
                        with target.open("ab" if existing else "wb") as stream:
                            remaining = length - existing
                            while remaining:
                                data = response.read(min(1024 * 1024, remaining))
                                if not data:
                                    raise RuntimeError("Incomplete model range")
                                stream.write(data)
                                remaining -= len(data)
                    with lock:
                        done += length
                        print(asset["name"], f"{done / size:.1%}", f"{done // (1024 * 1024)} MiB", flush=True)
                    return
                except Exception:
                    if attempt == 2:
                        raise RuntimeError(asset["name"] + ": download range failed; completed ranges retained") from None
                    time.sleep(1 + attempt)
        with ThreadPoolExecutor(max_workers=12) as pool:
            for future in as_completed([pool.submit(chunk, i) for i in range(count)]):
                future.result()
        with partial.open("wb") as stream:
            for index in range(count):
                with (pieces / f"{index:04d}.chunk").open("rb") as piece:
                    for data in iter(lambda: piece.read(8 * 1024 * 1024), b""):
                        stream.write(data)
    else:
        with opener().open(request.Request(url, headers={"User-Agent": "YutoNight/0.2"}), timeout=60) as response, partial.open("wb") as stream:
            for data in iter(lambda: response.read(1024 * 1024), b""):
                stream.write(data)
    if digest(partial) != asset["sha256"]:
        raise RuntimeError(asset["name"] + ": SHA256 mismatch; not installed")
    if size and partial.stat().st_size != size:
        raise RuntimeError(asset["name"] + ": size mismatch; not installed")
    # Do not clobber a file that appeared during download.
    os.link(partial, output)
    partial.unlink()
    print(asset["name"], "SHA256 verified", f"{time.monotonic() - start_time:.1f}s", flush=True)
    if asset.get("extract"):
        extract(asset, output)


if __name__ == "__main__":
    mirror = "--mirror" in sys.argv
    selected = [a for a in ASSETS if not any(arg.startswith("--only=") for arg in sys.argv)
                or "--only=" + a["name"] in sys.argv]
    for asset in selected:
        fetch(asset, mirror=mirror, modelscope="--modelscope" in sys.argv)
    (ROOT / "asset-manifest.json").write_text(json.dumps(ASSETS, indent=2) + "\n", encoding="utf8")
