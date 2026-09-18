"""Build paired, captured-state wall depth/color precision controls."""
import argparse
import json
import sys
import equipment_projection_variants as experiment


def main():
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    xywz = experiment.HLSL
    xwyz = xywz.replace('(double)world.z;', '(double)world.y;', 1).replace(
        'mat[1] * (double)world.y;', 'mat[1] * (double)world.z;', 1)
    variants = []
    for name, directory, sha, layout, hlsl, event in [
        ('depth', 'aligned-impact-depth-source-005',
         '621f3814c3486f8a58c3bc252e7a9ab59b70b58984f66be30367d809f330c806',
         dict(position=2, world=1, scratch=4, output=0), xywz, 157448),
        ('color', 'aligned-depth-transfers',
         'ec3b0323cd07080b1196b279fd7976a3cb3585b4f181c0117de6824b697b039b',
         dict(position=9, world=7, scratch=11, output=6), xwyz, 158223),
    ]:
        experiment.LAYOUTS = {sha: dict(layout, name=name)}
        experiment.HLSL = hlsl
        sys.argv = [sys.argv[0], '--sources', str(args.probe/directory), '--output', str(args.output/name)]
        experiment.main()
        variant = json.loads((args.output/name/'variants.json').read_text())['variants'][0]
        variant['file'] = name + '/' + variant['file']
        variant['event'] = event
        variants.append(variant)
    (args.output/'variants.json').write_text(json.dumps({'scope': __doc__, 'variants': variants}, indent=2)+'\n')


if __name__ == '__main__': main()
