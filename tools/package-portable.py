"""Create a ZIP64 portable release from an explicit, user-data-free whitelist."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import time
import zipfile

ROOT = Path(__file__).resolve().parent.parent
TOP_FILES = ('README.md', 'PORTABLE-NOTICES.md', 'start.cmd', 'check.cmd',
             'install.cmd', 'setup.ps1', 'requirements.txt', 'YutoNight.spec',
             'asset-manifest.json', 'AGENTS.md', 'CONTRIBUTING.md', '.gitignore', '.gitattributes')
FOLDERS = ('runtime', 'models', 'licenses', 'yutonight', 'tests', 'tools', '.github')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    version = re.search(r'__version__ = "([^"]+)"', (ROOT/'yutonight/__init__.py').read_text()).group(1)
    args.destination.mkdir(parents=True, exist_ok=True)
    archive_path = args.destination / f'YutoNight-{version}-portable.zip'
    partial = archive_path.with_suffix('.zip.part')
    if archive_path.exists() or partial.exists():
        raise SystemExit('Release file already exists; kept untouched.')
    files = [ROOT/name for name in TOP_FILES]
    for folder in FOLDERS:
        files.extend(p for p in (ROOT/folder).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts
                     and (folder not in {'yutonight', 'tests', 'tools'} or p.suffix in {'.py', '.ps1'}))
    required = ['runtime/app/YutoNight.exe', 'runtime/ffmpeg/ffmpeg.exe',
                'runtime/ffmpeg/ffprobe.exe', 'runtime/llama/llama-server.exe',
                'runtime/whisper/Release/whisper-cli.exe',
                'models/asr/ggml-large-v3-turbo-q5_0.bin',
                'models/translation/qwen3-4b-q4_k_m.gguf']
    if any(not (ROOT/name).is_file() for name in required):
        raise SystemExit('A required portable asset is missing.')
    manifest = {'version': version, 'platform': 'Windows 11 x64', 'files': []}
    started = time.monotonic()
    for p in sorted(files):
        manifest['files'].append({'path': p.relative_to(ROOT).as_posix(),
                                  'bytes': p.stat().st_size, 'sha256': sha256(p)})
    for expected in json.loads((ROOT/'asset-manifest.json').read_text(encoding='utf8')):
        if expected['name'].endswith('-model'):
            entry = next(item for item in manifest['files'] if item['path'] == expected['path'])
            if entry['sha256'] != expected['sha256']:
                raise SystemExit('Model checksum mismatch; release not created.')
    (ROOT/'portable-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    files.append(ROOT/'portable-manifest.json')
    print(f'Verified {len(files)} release files; creating ZIP64 archive.',flush=True)
    prefix = f'YutoNight-{version}'
    packed=0
    next_report=512*1024*1024
    with zipfile.ZipFile(partial,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as archive:
        for p in sorted(files):
            archive.write(p, prefix+'/'+p.relative_to(ROOT).as_posix())
            packed+=p.stat().st_size
            if packed>=next_report:
                print(f'Packed {packed/(1024**3):.2f} GiB',flush=True)
                next_report=packed+512*1024*1024
    with zipfile.ZipFile(partial) as archive:
        if archive.testzip() is not None:
            raise SystemExit("Archive integrity check failed; partial archive kept.")
    partial.rename(archive_path)
    checksum=sha256(archive_path)
    archive_path.with_suffix('.zip.sha256').write_text(checksum+'  '+archive_path.name+'\n',encoding='ascii')
    print(json.dumps({'archive':str(archive_path),'bytes':archive_path.stat().st_size,'sha256':checksum,
                      'seconds':round(time.monotonic()-started,1)},indent=2),flush=True)


if __name__=='__main__':
    main()
