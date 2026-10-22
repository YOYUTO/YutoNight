"""Release packaging works without real models or user media."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile


class PackagingTests(unittest.TestCase):
    def test_portable_package_reads_model_manifest_and_excludes_work_directory(self):
        spec = importlib.util.spec_from_file_location("portable_package", Path(__file__).resolve().parents[1] / "tools/package-portable.py")
        package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "source"
            root.mkdir()
            for name in package.TOP_FILES:
                (root / name).write_text("fixture", encoding="utf8")
            (root / "yutonight").mkdir()
            (root / "yutonight/__init__.py").write_text('__version__ = "0.0.1"', encoding="utf8")
            required = ["runtime/app/YutoNight.exe", "runtime/ffmpeg/ffmpeg.exe", "runtime/ffmpeg/ffprobe.exe",
                        "runtime/llama/llama-server.exe", "runtime/whisper/Release/whisper-cli.exe",
                        "models/asr/ggml-large-v3-turbo-q5_0.bin", "models/translation/qwen3-4b-q4_k_m.gguf"]
            assets = []
            for name in required:
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"synthetic fixture")
                if name.startswith("models/"):
                    assets.append({"name": target.stem + "-model", "path": name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
            (root / "asset-manifest.json").write_text(json.dumps(assets), encoding="utf8")
            (root / "work").mkdir()
            (root / "work/private.txt").write_text("must stay local", encoding="utf8")
            output = Path(temporary) / "output"
            with patch.object(package, "ROOT", root), patch("sys.argv", ["package-portable.py", "--destination", str(output)]), redirect_stdout(io.StringIO()):
                package.main()
            archive_path = output / "YutoNight-0.0.1-portable.zip"
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIsNone(archive.testzip())
                names = archive.namelist()
                self.assertIn("YutoNight-0.0.1/portable-manifest.json", names)
                self.assertTrue(all("/work/" not in name for name in names))
                self.assertTrue(all("YutoNight-0.0.1/" + name in names for name in required))
            self.assertTrue(archive_path.with_suffix(".zip.sha256").is_file())
