"""Install pinned development binaries locally, without system-wide changes."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        while block := source.read(4 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-baseline', action='store_true')
    parser.add_argument('--with-analysis', action='store_true')
    parser.add_argument('--with-source', action='store_true', help='Fetch the pinned clean GPU source used by the enhanced renderer')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    local = root / '.tools'
    local.mkdir(exist_ok=True)
    lock = json.loads((root / 'tools/dependencies.json').read_text())
    for name in ('rexglue', 'llvm', *(['xenia'] if args.with_baseline else []), *(['umodel'] if args.with_analysis else [])):
        dep = lock[name]
        archive = local / dep['archive']
        if not archive.exists():
            partial = archive.with_suffix(archive.suffix + '.partial')
            print(f"Downloading {name} {dep['version']}", flush=True)
            urllib.request.urlretrieve(dep['url'], partial)
            if sha256(partial) != dep['sha256']:
                raise SystemExit(f'Hash mismatch: {partial}')
            partial.replace(archive)
        if sha256(archive) != dep['sha256']:
            raise SystemExit(f'Hash mismatch: {archive}')
        destination = local / dep['destination']
        marker = destination / '.archive-sha256'
        if marker.exists() and marker.read_text().strip() == dep['sha256']:
            print(f'{name}: already installed')
            continue
        if name == 'umodel':
            destination.mkdir(exist_ok=True)
            shutil.copy2(archive, destination / 'umodel.exe')
        elif archive.suffix == '.zip':
            with zipfile.ZipFile(archive) as source:
                source.extractall(destination)
        else:
            sevenzip = shutil.which('7z') or shutil.which('7z.exe')
            if not sevenzip:
                raise SystemExit('7-Zip is required to unpack the portable LLVM installer')
            subprocess.run([sevenzip, 'x', str(archive), '-o' + str(destination), '-y'], check=True)
        marker.write_text(dep['sha256'] + '\n')
        print(f'{name}: installed at {destination}')
    if args.with_source:
        source = local / 'rexglue-src'
        commit = lock['rexglue']['source_commit']
        if not source.exists():
            subprocess.run(['git', 'clone', '--no-checkout', 'https://github.com/rexglue/rexglue-sdk.git', str(source)], check=True)
            subprocess.run(['git', '-C', str(source), 'checkout', '--detach', commit], check=True)
        revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if revision != commit:
            raise SystemExit('Existing SDK source is not the pinned revision; it was left unchanged.')
        subprocess.run(['git', '-C', str(source), 'diff', 'HEAD', '--exit-code', '--quiet'], check=True)
        # The isolated GPU target uses the SDK's packaged dependency headers
        # and tracked DXBC/renderdoc sources, not the SDK's full build. Pulling
        # every submodule would needlessly clone FFmpeg and other large trees.


if __name__ == '__main__':
    main()
