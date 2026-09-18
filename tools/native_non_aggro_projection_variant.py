"""Build the captured non-aggro depth fixture with the native patcher."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    sha = 'dbb9a4ba90d049f9349bd7aacb08bd27aa1d0b59371b218b48dabcb24801abed'
    source = args.probe/'non-aggro-pairs-001'/(sha+'.dxbc')
    if hashlib.sha256(source.read_bytes()).hexdigest() != sha:
        raise ValueError('Unexpected captured depth shader')
    helper = Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_projection_patch.exe'
    args.output.mkdir(parents=True, exist_ok=False)
    target = args.output/'non-aggro-depth.dxbc'
    subprocess.run([str(helper), 'A7E5F6317B4316DB', '0', str(source), str(target),
                    '14', '16', '0', '2'], check=True, timeout=10)
    report = {'guest': 'A7E5F6317B4316DB', 'event': 20439, 'file': target.name,
              'original_sha256': sha, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
              'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
    (args.output/'variant.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__': main()
