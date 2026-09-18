"""Export an auditable source-only installer, never a built game or Git history."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = {'.gitignore', '.gitattributes', 'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'CMakeLists.txt',
       'CMakePresets.json', 'army_of_two_manifest.toml', 'Play.cmd', 'Setup.cmd'}
DIRS = {'src', 'tools', 'tests', 'config', 'cmake', 'resources', 'release'}
TEXT = {'.cpp', '.cc', '.c', '.cs', '.h', '.hpp', '.ipp', '.inc', '.in', '.hlsl', '.py', '.ps1', '.cmd', '.def',
        '.md', '.txt', '.json', '.toml', '.script', '.cmake'}
EXCLUDE = {'src/hud_ink_bounds.h', 'tools/cleanup-obsolete-recordings.ps1',
           'tools/build_source_installer.ps1', 'release/install.ps1', 'release/Install.cmd'}
SECRET = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}')


def allowed(name: str) -> bool:
    path = Path(name)
    parts = path.parts
    if not parts or path.is_absolute() or '..' in parts or name in EXCLUDE:
        return False
    if name in TOP or name == 'generated/rexglue.cmake':
        return True
    if parts[0] not in DIRS or '__pycache__' in parts or 'dist' in parts:
        return False
    if parts[0] == 'release':
        return name in {'release/version.json',
                       'release/gui/Installer.cs', 'release/gui/install.ps1', 'release/gui/prerequisites.json',
                       'release/gui/app.manifest'}
    if parts[0] == 'resources':
        # Header is admitted only once its source/provenance is documented.
        return ((len(parts) == 3 and parts[1] == 'input' and path.suffix in {'.png', '.txt', '.md'})
                or name in {'resources/branding/sierra_romeo.png', 'resources/branding/README.md',
                            'resources/branding/sr_installer_icon.ico', 'resources/branding/sr_installer_icon.png',
                            'resources/demos/README.md', 'resources/demos/campaign-readability.gif',
                            'resources/demos/hold-b-to-skip.gif', 'resources/demos/pc-coop-connections.gif'})
    # Narrative material needs explicit review; new tracking notes must not
    # silently enter the public bundle merely by living beside build tools.
    if path.suffix in {'.md', '.txt'}:
        return (name in {'tools/setup-requirements.txt', 'tools/requirements-analysis.txt'}
                or (parts[:2] == ('src', 'vendor') and
                    (path.name.startswith(('LICENSE', 'COPYING'))
                     or name in {'src/vendor/REXGLUE-LICENSE.txt', 'src/vendor/FSR-LICENSE.txt',
                                 'src/vendor/nlohmann/README.md'})))
    return (path.suffix in TEXT or path.name.startswith(('LICENSE', 'COPYING'))) and path.name != 'CMakeUserPresets.json'


def collect(root: Path) -> dict[str, bytes]:
    root = root.resolve()
    result = {}
    candidates = [root / name for name in TOP | {'generated/rexglue.cmake'}]
    for directory in sorted(DIRS):
        folder = root / directory
        if folder.is_symlink():
            raise ValueError(f'Symlink source directory: {directory}')
        if folder.is_dir():
            candidates.extend(folder.rglob('*'))
    for path in sorted(candidates):
        name = path.relative_to(root).as_posix()
        if not allowed(name) or not path.is_file():
            continue
        if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root):
            raise ValueError(f'Symlink source: {name}')
        if not path.resolve().is_relative_to(root):
            raise ValueError(f'Source escapes checkout: {name}')
        data = path.read_bytes()
        if len(data) > (16 if path.suffix == '.gif' else 8) * 1024 * 1024:
            raise ValueError(f'Unexpectedly large source file: {name}')
        if SECRET.search(data):
            raise ValueError(f'Potential credential in {name}; contents withheld')
        if path.suffix == '.png':
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError(f'Invalid PNG source: {name}')
        elif path.suffix == '.ico':
            if not data.startswith(b'\x00\x00\x01\x00'):
                raise ValueError(f'Invalid installer icon: {name}')
        elif path.suffix == '.gif':
            if data[:6] not in (b'GIF87a', b'GIF89a'):
                raise ValueError(f'Invalid demo GIF: {name}')
        else:
            # Stable archive/index hashes across Windows and Unix checkouts.
            # Keep any UTF-8 BOM (Windows PowerShell recognizes it), normalize EOL.
            data.decode('utf-8-sig')  # Reject binary game material masquerading as text.
            data = data.replace(b'\r\n', b'\n')
        result[name] = data
    required = TOP - {'.gitignore'}
    if missing := required - result.keys():
        raise ValueError('Missing source files: ' + ', '.join(sorted(missing)))
    return result


def package(root: Path, output: Path) -> dict:
    files = collect(root)
    report = {'format': 1, 'kind': 'source-only-installer',
              'version': json.loads(files['release/version.json'])['version'],
              'files': [{'path': p, 'bytes': len(b), 'sha256': hashlib.sha256(b).hexdigest()}
                        for p, b in sorted(files.items())]}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive create avoids replacing an artifact already under review.
    with output.open('xb') as stream, zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            archive.writestr(name, data)
        archive.writestr('SOURCE-MANIFEST.json', json.dumps(report, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = package(ROOT, args.output)
    print(f"Exported {len(report['files'])} files to {args.output}; inspect SOURCE-MANIFEST.json before publication.")
