"""Build both captured static material fixtures with the native patcher."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--lighting', action='store_true')
    parser.add_argument('--partner', action='store_true')
    parser.add_argument('--car', action='store_true')
    parser.add_argument('--neighbor', action='store_true')
    args = parser.parse_args()
    if sum((args.lighting,args.partner,args.car,args.neighbor)) > 1: parser.error('Choose one fixture family')
    helper = Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_projection_patch.exe'
    args.output.mkdir(parents=True, exist_ok=False)
    variants = []
    layouts = (
        ('3306D6C23B238BE6', '1F', 8969, 8, 10, '4a985b920fa13d0d3b6c6e0b90750e8107b11268dfc4d4a6e12ef689b2d627f5'),
        ('AC2A17351535ED19', '7F', 16293, 11, 13, '8c8c8b9c2e799d11e1bb2dbecb3bcafd3245ff63a376ace03745debeaa527df5'))
    if args.lighting:
        layouts = (
            ('807B2A09A19C3B15', '3F', 16548, 10, 12, 'fc77ef6b3cabdbf165fbf6798c8b78977656d0eed4191c3d8b3bbfb67912415f'),
            ('494DCD69B7BA177C', '3F', 16513, 11, 13, 'c1fa2fb250194fd8d206dac24b81466f04746a7c3d67f4a90a9ef08cd99340ec'))
    if args.partner:
        layouts = (
            ('87C093F46637D13F', '1FF', 7962, 12, 14, '01389226d3e7818ce46af7291ddc362aad8d83513be5e6c7d058f4ffe2eeacb0'),
            ('D66FF6280606248C', '7F', 8933, 9, 11, 'd4aed78629d55c0c164a1716344d7edcfd530163db9f3d6c9a1f8f7423454963'),
            ('B22EF913802807F8', '3F', 9119, 9, 11, 'c2ac7862362afe9c8df6e2c2d72201e955d41717f8f22a14a89809c589f08743'))
    if args.car:
        layouts = (('998F2B953D9B74FD', '1FF', 6976, 12, 14, 'd522a2fad820373689ce053c20be40fd68f7a504f7b78034e1a99a87d126133b'),)
    if args.neighbor:
        layouts = (('CB7E063397190431', '7F', 6961, 10, 12, 'b81e7f67bf18bdaacfd9927686149a96a2da0493e469d647c23e99d53edc0064'),)
    for guest, mod, event, position, scratch, sha in layouts:
        source = args.probe/('static-neighbor-pairs-001' if args.neighbor else 'car-pixels-001' if args.car else 'static-partner-pairs-002' if args.partner else 'static-lighting-pairs-001' if args.lighting else 'static-visible-pairs-001')/(sha+'.dxbc')
        if hashlib.sha256(source.read_bytes()).hexdigest() != sha: raise ValueError('Unexpected captured shader')
        target = args.output/(guest+'.dxbc')
        subprocess.run([str(helper), guest, mod, str(source), str(target), str(position), str(scratch), '0', '2'], check=True, timeout=10)
        variants.append({'guest': guest, 'event': event, 'file': target.name,
                         'original_sha256': sha, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    (args.output/'variants.json').write_text(json.dumps({'variants': variants,
        'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest()}, indent=2)+'\n')


if __name__ == '__main__': main()
