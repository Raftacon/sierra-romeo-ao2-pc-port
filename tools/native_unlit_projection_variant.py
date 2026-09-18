"""Build the captured unlit replay fixture with the native projection patcher."""
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
    sha = '7455815e6e0ca341e456923f9f6f93d375983a412b06260d3b18e631317ccd53'
    source = args.probe/'static-projection-pairs-001'/(sha+'.dxbc')
    if hashlib.sha256(source.read_bytes()).hexdigest() != sha:
        raise ValueError('Unexpected captured unlit shader')
    helper = Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_projection_patch.exe'
    args.output.mkdir(parents=True, exist_ok=False)
    target = args.output/'unlit.dxbc'
    subprocess.run([str(helper), 'D66D9932DC2EC8D8', '7', str(source), str(target),
                    '5', '7', '0', '2'], check=True, timeout=10)
    report = {'guest': 'D66D9932DC2EC8D8', 'event': 10072, 'file': target.name,
              'original_sha256': sha, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
              'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}
    (args.output/'variant.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__': main()
