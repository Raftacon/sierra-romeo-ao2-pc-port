"""Build captured-state precision variants for crate depth, color and modulation."""
import argparse
import json
from pathlib import Path
import sys
import equipment_projection_variants as experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--sample-align', action='store_true',
                        help='Replay-only 720p control: move 2x MSAA geometry up half a pixel')
    args = parser.parse_args()
    experiment.ALLOW_SAMPLE_ALIGNMENT_LITERALS = args.sample_align
    args.output.mkdir(parents=True, exist_ok=False)
    xywz = experiment.HLSL
    xwyz = xywz.replace('(double)world.z;', '(double)world.y;', 1).replace(
        'mat[1] * (double)world.y;', 'mat[1] * (double)world.z;', 1)
    variants = []
    for name, sha, layout, hlsl in [
        ('color', '716b8733296b8b65c3946768dc076baceb32ad70af4ae4d2776feb42f340a50a',
         dict(position=11, world=9, scratch=13, output=8), xwyz),
        ('depth', '6f5ac15a6b8ecf42d54123dac9b79da1e503ab0af86289780ec8a1b5f610e743',
         dict(position=2, world=1, scratch=4, output=0), xywz),
        ('crate', 'd398177c40a1c874c502f1626b346ccfda6afacf0143b7129ecbb598d001e0af',
         dict(position=5, world=4, scratch=7, output=3), xwyz),
    ]:
        experiment.HLSL = hlsl
        if args.sample_align and name != 'crate':
            if hlsl.count('  return (float4)p;') != 1: raise ValueError('Unexpected projection snippet')
            experiment.HLSL = hlsl.replace('  return (float4)p;',
                '  if (asuint(sys[13].z) == 1) p.y = p.y + p.w * (1.0 / 720.0);\n  return (float4)p;')
        experiment.LAYOUTS = {sha: dict(layout, name=name)}
        sys.argv = [sys.argv[0], '--sources', str(args.sources), '--output', str(args.output/name)]
        experiment.main()
        item = json.loads((args.output/name/'variants.json').read_text())['variants'][0]
        item['file'] = name + '/' + item['file']
        variants.append(item)
    (args.output/'variants.json').write_text(json.dumps({'scope': __doc__,
        'sample_align_720p_control': args.sample_align, 'variants': variants}, indent=2)+'\n')


if __name__ == '__main__':
    main()
