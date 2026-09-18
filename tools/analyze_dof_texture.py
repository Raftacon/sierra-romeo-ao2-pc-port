"""Decode captured unsigned RGBA16 blur or RGBA8 sharp textures for border inspection."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import struct
from PIL import Image


def tiled_offset(x, y, pitch, bpp_log2=3):
    # Pinned SDK texture_util::GetTiledOffset2D.
    pitch = (pitch + 31) & ~31
    macro = ((x >> 5) + (y >> 5) * (pitch >> 5)) << (bpp_log2 + 7)
    micro = ((x & 7) + ((y & 14) << 2)) << bpp_log2
    offset = macro + ((micro & ~15) << 1) + (micro & 15) + ((y & 1) << 4)
    return ((offset & ~511) << 3) + ((y & 16) << 7) + ((offset & 448) << 2) + \
        (((((y & 8) >> 2) + (x >> 3)) & 3) << 6) + (offset & 63)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    parser.add_argument('--draw', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--kind', choices=('dof', 'blur-input', 'sharp'), default='dof')
    args = parser.parse_args()
    slot, fmt, bpp_log2, gain = {'dof': (2, 26, 3, 8), 'blur-input': (0, 26, 3, 8),
                                'sharp': (1, 6, 2, 1)}[args.kind]
    entries = [r for r in csv.DictReader(Path(str(args.capture) + '.fetch.csv').open())
               if int(r['draw']) == args.draw and int(r['slot']) == slot]
    if len(entries) != 1:
        parser.error(f'Expected exactly one texture slot {slot} for this draw')
    w = [int(entries[0][f'word{i}']) for i in range(6)]
    swizzle = [(w[3] >> (1 + 3 * i)) & 7 for i in range(4)]
    if (w[0] & 1023 != 2 or not w[0] >> 31 or w[1] & 63 != fmt or
            (w[1] >> 10) & 1 or (w[5] >> 9) & 3 != 1 or w[3] & 1 or
            (w[3] >> 13) & 63 or any(v > 5 for v in swizzle)):
        parser.error('Expected non-stacked tiled unsigned normalized texture of the selected format, with supported swizzle and zero exponent')
    width, height, pitch = (w[2] & 8191) + 1, ((w[2] >> 13) & 8191) + 1, ((w[0] >> 22) & 511) * 32
    raw_path = Path(str(args.capture) + f'.draw-{args.draw}.{args.kind}.bin')
    raw = raw_path.read_bytes()
    endian = (w[1] >> 6) & 3
    order = [(0, 1, 2, 3), (1, 0, 3, 2), (3, 2, 1, 0), (2, 3, 0, 1)][endian]
    bpp, maximum = 1 << bpp_log2, 65535 if fmt == 26 else 255
    pixels = []
    offsets = set()
    for y in range(height):
        for x in range(width):
            offset = tiled_offset(x, y, pitch, bpp_log2)
            if offset < 0 or offset + bpp > len(raw) or offset in offsets:
                parser.error('Incomplete or inconsistent tiled texture snapshot')
            offsets.add(offset)
            swapped = bytes(raw[offset + b + j] for b in range(0, bpp, 4) for j in order)
            components = (*struct.unpack('<4H' if fmt == 26 else '<4B', swapped), 0, maximum)
            pixels.append(tuple(components[c] for c in swizzle))
    def stats(samples):
        return {'rgba_nonzero': sum(any(p) for p in samples),
                'alpha_nonzero': sum(p[3] != 0 for p in samples),
                'alpha_max': max(p[3] for p in samples)}
    result = {'draw': args.draw, 'kind': args.kind, 'slot': slot, 'format': fmt, 'swizzle': swizzle, 'width': width, 'height': height, 'pitch': pitch,
              'raw_sha256': hashlib.sha256(raw).hexdigest(), 'raw_bytes': len(raw),
              'columns': [stats(pixels[x::width]) for x in range(width)],
              'rows': [stats(pixels[y * width:(y + 1) * width]) for y in range(height)],
              'edge_samples': {str(y): {str(x): pixels[y * width + x]
                                      for x in range(max(0, width - 32), width)}
                               for y in sorted({0, height // 4, height // 2, height * 3 // 4, height - 1})},
              'preview_gain': gain,
              'interpretation': 'CPU readback snapshot, not a GPU texture capture. PNG previews use the recorded gain and descriptor swizzle without display color conversion; no frame parity is inferred.'}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'texture-analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    rgb = Image.new('RGB', (width, height))
    rgb.putdata([tuple(min(255, v * gain * 255 // maximum) for v in p[:3]) for p in pixels])
    rgb.save(args.output / f'rgb-gain{gain}.png')
    alpha = Image.new('L', (width, height))
    alpha.putdata([min(255, p[3] * gain * 255 // maximum) for p in pixels])
    alpha.save(args.output / f'alpha-gain{gain}.png')
    print(json.dumps({'width': width, 'height': height, 'pitch': pitch,
                      'right_columns': result['columns'][-10:], 'bottom_rows': result['rows'][-4:]}, indent=2))


if __name__ == '__main__':
    main()
