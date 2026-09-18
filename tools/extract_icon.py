"""Extract the original title tile from a local loaded XEX image into Windows icons."""
import argparse
import io
import struct
from pathlib import Path
from PIL import Image

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('image', type=Path)
parser.add_argument('--output', type=Path, default=Path('artifacts/branding'))
args = parser.parse_args()
data = args.image.read_bytes()
position = data.find(b'XDBF')
if position < 0:
    raise SystemExit('No XDBF resource in local image')
magic, version, capacity, count, free_capacity, free_count = struct.unpack_from('>6I', data, position)
if count > capacity or capacity > 10000 or free_capacity > 10000:
    raise SystemExit('Invalid XDBF directory')
base = position + 24 + capacity * 18 + free_capacity * 8
for index in range(count):
    section, identity, offset, size = struct.unpack_from('>HQII', data, position + 24 + index * 18)
    if section == 2 and identity == 0x8000:
        png = data[base + offset:base + offset + size]
        if len(png) != size:
            raise SystemExit('Truncated title image')
        icon = Image.open(io.BytesIO(png)).convert('RGBA')
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / 'title.png').write_bytes(png)
        icon.save(args.output / 'army_of_two.ico', sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
        print(f'Original title tile: {icon.size}, extracted to {args.output}')
        break
else:
    raise SystemExit('Title icon not found')
