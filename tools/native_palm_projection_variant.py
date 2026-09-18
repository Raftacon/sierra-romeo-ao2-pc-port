"""Build the captured palm replay fixture with the native projection patcher."""
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
    sha = 'bc4bfdc82c08dce699628636a714c778ad4e086c61c0d8a5c3880dafeb17d85d'
    source = args.probe/'static-projection-pairs-001'/(sha+'.dxbc')
    if hashlib.sha256(source.read_bytes()).hexdigest() != sha:
        raise ValueError('Unexpected captured palm shader')
    helper = Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_projection_patch.exe'
    args.output.mkdir(parents=True, exist_ok=False)
    target = args.output/'palm.dxbc'
    subprocess.run([str(helper), '91B258F7198B1CE4', 'FF', str(source), str(target),
                    '11', '13', '0', '2'], check=True, timeout=10)
    report = {'guest': '91B258F7198B1CE4', 'event': 6318, 'file': target.name,
              'original_sha256': sha, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
              'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
    (args.output/'variant.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__': main()
