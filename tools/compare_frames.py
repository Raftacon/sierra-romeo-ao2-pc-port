"""Compare manually matched captures. Metrics do not establish gameplay parity."""
import argparse
import json
import math
from pathlib import Path
from PIL import Image, ImageChops, ImageStat


def crop_value(value):
    x, y, width, height = map(int, value.split(','))
    if min(x, y) < 0 or min(width, height) <= 0:
        raise argparse.ArgumentTypeError('Expected nonnegative x,y and positive width,height')
    return x, y, x + width, y + height


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('baseline', type=Path)
    parser.add_argument('native', type=Path)
    parser.add_argument('--baseline-crop', type=crop_value)
    parser.add_argument('--native-crop', type=crop_value)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    a, b = Image.open(args.baseline).convert('RGB'), Image.open(args.native).convert('RGB')
    for crop, source in ((args.baseline_crop, a), (args.native_crop, b)):
        if crop and (crop[2] > source.width or crop[3] > source.height):
            parser.error('Crop extends outside image')
    if args.baseline_crop: a = a.crop(args.baseline_crop)
    if args.native_crop: b = b.crop(args.native_crop)
    if a.size != b.size:
        parser.error(f'Capture dimensions differ: {a.size} vs {b.size}; supply matching crops')
    diff = ImageChops.difference(a, b)
    stats = ImageStat.Stat(diff)
    result = {
        'baseline': str(args.baseline), 'native': str(args.native),
        'baseline_crop': args.baseline_crop, 'native_crop': args.native_crop,
        'size': a.size, 'mean_absolute_channel_error_0_255': sum(stats.mean) / 3,
        'root_mean_square_channel_error_0_255': math.sqrt(sum(x*x for x in stats.rms) / 3),
        'pixel_identical': diff.getbbox() is None,
        'interpretation': 'Diagnostic only. Requires matching scene, phase, resolution and settings; no gameplay pass inferred.'
    }
    args.output.mkdir(parents=True, exist_ok=False)
    a.save(args.output / 'baseline.png')
    b.save(args.output / 'native.png')
    diff.save(args.output / 'absolute-difference.png')
    (args.output / 'comparison.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
