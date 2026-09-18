"""Exercise the GUI EXE's real extraction path and verify its source payload.

Does not run dependency installers or the game. Use a new output directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    installer, source, output = args.installer.resolve(), args.source.resolve(), args.output.resolve()
    if output.exists():
        parser.error('Choose a new verification directory')
    output.mkdir(parents=True)
    extracted = output / 'extracted source with spaces'
    command = [str(installer), '--verify-extract', str(extracted)]
    subprocess.run(command, check=True, capture_output=True)
    with zipfile.ZipFile(source) as archive:
        expected = set(archive.namelist())
        actual = {p.relative_to(extracted).as_posix() for p in extracted.rglob('*') if p.is_file()}
        assert actual == expected, 'Unexpected extracted inventory'
        for name in expected:
            assert (extracted / name).read_bytes() == archive.read(name), name
    before = hashlib.sha256((extracted / 'README.md').read_bytes()).hexdigest()
    result = subprocess.run(command, capture_output=True)
    assert result.returncode != 0, 'Existing destination was overwritten'
    assert hashlib.sha256((extracted / 'README.md').read_bytes()).hexdigest() == before
    report = {'payload_files': len(expected), 'payload_matches_source': True,
              'existing_destination_rejected': True, 'path_with_spaces_passed': True,
              'installer_sha256': hashlib.sha256(installer.read_bytes()).hexdigest(),
              'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    (output / 'verification.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
