"""Build paired wall replay fixtures with the exact guarded native patcher."""
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
    args.output.mkdir(parents=True, exist_ok=False)
    helper = Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_projection_patch.exe'
    variants = []
    for name, directory, sha, guest, mod, position, scratch, event in [
        ('depth', 'aligned-impact-depth-source-005',
         '621f3814c3486f8a58c3bc252e7a9ab59b70b58984f66be30367d809f330c806',
         '813A25A25223C7F5', '0', '2', '4', 157448),
        ('color', 'aligned-depth-transfers',
         'ec3b0323cd07080b1196b279fd7976a3cb3585b4f181c0117de6824b697b039b',
         '5E20CD3F81F88801', '3F', '9', '11', 158223),
    ]:
        source = args.probe/directory/(sha+'.dxbc')
        if hashlib.sha256(source.read_bytes()).hexdigest() != sha: raise ValueError('Changed captured shader')
        target = args.output/(name+'.dxbc')
        subprocess.run([str(helper), guest, mod, str(source), str(target), position, scratch, '0', '2'], check=True, timeout=10)
        variants.append({'file': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'original': str(source.resolve()), 'original_sha256': sha, 'event': event, 'guest': guest})
    (args.output/'variants.json').write_text(json.dumps({'scope': __doc__,
        'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(), 'variants': variants}, indent=2)+'\n')


if __name__ == '__main__': main()
