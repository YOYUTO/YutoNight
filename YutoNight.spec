# PyInstaller one-directory bundle. Large models and external tools remain outside.
from importlib.metadata import distribution
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_data_files

project_root = Path(SPECPATH)
datas = collect_data_files("cv2", includes=["data/haarcascade_frontalface_default.xml"])
for package in ("PySide6-Essentials", "PySide6-Addons", "shiboken6", "opencv-python", "numpy", "pyinstaller"):
    installed = distribution(package)
    for relative in installed.files or []:
        name = relative.name.lower()
        if name == "metadata" or (relative.suffix.lower() != ".xml" and ("licenses" in relative.parts or name.startswith(("license", "copying", "notice", "copyright")))):
            source = Path(installed.locate_file(relative))
            if source.is_file():
                datas.append((str(source), str(Path("licenses") / package / relative.parent)))
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    datas.append((str(python_license), "licenses/Python"))
if (project_root / "licenses").is_dir():
    datas.append((str(project_root / "licenses"), "licenses"))

a = Analysis(
    [str(project_root / "yutonight" / "portable_entry.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=["PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2"],
    noarchive=False,
)
# Qt 6.12 uses Windows 11 system ICU. Ambient Poppler ICU has incompatible
# version-suffixed exports and must not shadow the system library in the bundle.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() != "icuuc.dll"
              and not Path(entry[0]).name.lower().startswith("icudt")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="YutoNight", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="app")
