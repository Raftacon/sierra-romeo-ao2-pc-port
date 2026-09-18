"""Create replay-only UV-offset or lighting variants of the equipment shader.

Requires aot_dxbc_checksum. Never installs shaders or changes game assets.
The exact shader hash and instruction bytes are pinned; this is not a general
DXBC editor. Literalizing the observed value provides a round-trip control.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

EXPECTED = 'adc3a7e10f7260fc3dd98bfe97428dee5d339c7a7df25bfdf53a1374fa4c9b1e'
ANCHOR = [0x09000000, 0x00100032, 15, 0x00100046, 0, 0x00308046, 1, 1, 4]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shader', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--lighting', action='store_true',
                        help='Isolate the main normal map and sharp reflected-light term')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    checksum_tool = root / 'out/build/RelWithDebInfo/aot_dxbc_checksum.exe'
    data = args.shader.read_bytes()
    if hashlib.sha256(data).hexdigest() != EXPECTED:
        raise ValueError('Require the identified AC04C3073E20412D equipment shader')
    args.output.mkdir(parents=True, exist_ok=False)

    def checksum(path):
        result = subprocess.run([str(checksum_tool), str(path)], capture_output=True,
                                text=True, check=True, timeout=5)
        value = result.stdout.strip()
        if len(value) != 32:
            raise ValueError('Invalid checksum result')
        return struct.pack('<4I', *(int(value[i:i+8], 16) for i in range(0, 32, 8)))

    if checksum(args.shader) != data[4:20]:
        raise ValueError('Original DXBC checksum does not match')
    count = struct.unpack_from('<I', data, 28)[0]
    offsets = struct.unpack_from('<' + 'I' * count, data, 32)
    chunks = [data[o:o+8+struct.unpack_from('<I', data, o+4)[0]] for o in offsets]
    shader_index = next(i for i, chunk in enumerate(chunks) if chunk[:4] == b'SHEX')
    words = list(struct.unpack('<' + 'I' * ((len(chunks[shader_index])-8)//4), chunks[shader_index][8:]))
    anchors, boundaries, offset = [], [], 2
    while offset < len(words):
        length = words[offset+1] if words[offset] & 2047 == 53 else (words[offset] >> 24) & 127
        if not length or offset + length > len(words):
            raise ValueError('Invalid instruction boundary')
        if words[offset:offset+length] == ANCHOR:
            anchors.append(offset)
        boundaries.append(offset)
        offset += length
    if len(anchors) != 1:
        raise ValueError('Require exactly one C6 offset instruction')
    at = anchors[0]
    baseline_y = struct.unpack('<f', struct.pack('<I', 1051566176))[0]
    def emit(changed, filename):
        changed[1] = len(changed)
        payload = struct.pack('<' + 'I' * len(changed), *changed)
        new_chunks = list(chunks)
        new_chunks[shader_index] = b'SHEX' + struct.pack('<I', len(payload)) + payload
        position, new_offsets = 32+4*count, []
        for chunk in new_chunks:
            new_offsets.append(position)
            position += len(chunk)
        result = bytearray(data[:32] + struct.pack('<'+'I'*count, *new_offsets) + b''.join(new_chunks))
        struct.pack_into('<I', result, 24, len(result))
        path = args.output / filename
        path.write_bytes(result)
        result[4:20] = checksum(path)
        path.write_bytes(result)
        return {'file': path.name, 'sha256': hashlib.sha256(result).hexdigest()}

    variants = []
    deltas = [0] if args.lighting else [0, -.001, .001, -.003, .003, -.01, .01, -.05, .05, .25, .5]
    for index, delta in enumerate(deltas):
        x, y = 0., baseline_y + delta
        literal = list(struct.unpack('<4I', struct.pack('<4f', x, y, x, x)))
        replacement = [0x0A000000, *ANCHOR[1:5], 0x00004E46, *literal]
        changed = words[:at] + replacement + words[at+len(ANCHOR):]
        variants.append(dict(emit(changed, f'phase-{index:02d}.dxbc'),
                             delta_y=delta, literal_xy_bits=literal[:2]))
    if args.lighting:
        # Translated instructions 548 and 1146. Require exact, unique decoded
        # instructions, not byte substrings that could occur inside operands.
        normal = [0x05000036, 0x00100072, 12, 0x00100246, 15]
        specular = [0x05000019, 0x00100012, 16, 0x0010001A, 5]
        replacements = {}
        for label, anchor, replacement in [
            ('flat-normal', normal,
             [0x08000036, 0x00100072, 12, 0x00004E46,
              0x3F000000, 0x3F000000, 0x3F800000, 0]),
            ('no-sharp-specular', specular,
             [0x08000036, 0x00100012, 16, 0x00004E46, 0, 0, 0, 0]),
        ]:
            matches = [i for i in boundaries if words[i:i+len(anchor)] == anchor]
            if len(matches) != 1:
                raise ValueError('Require exactly one '+label+' instruction')
            replacements[label] = (matches[0], anchor, replacement)
        for labels in [('flat-normal',), ('no-sharp-specular',),
                       ('flat-normal', 'no-sharp-specular')]:
            changed = list(words)
            patches = [replacements[label] for label in labels]
            for index, anchor, replacement in sorted(patches, reverse=True):
                changed[index:index+len(anchor)] = replacement
            variants.append(dict(emit(changed, '+'.join(labels)+'.dxbc'),
                                 isolated_terms=list(labels),
                                 instruction_word_offsets=[p[0] for p in patches]))
    # Positive control: original shader with only its final color export replaced.
    if words[-6:] != [0x05000036, 0x001020F2, 0, 0x00100E46, 14, 0x0100003E]:
        raise ValueError('Unexpected original color export')
    black = words[:-6] + [0x08000036, 0x001020F2, 0, 0x00004E46, 0, 0, 0, 0, 0x0100003E]
    variants.append(dict(emit(black, 'black-control.dxbc'), control='zero output'))
    (args.output / 'variants.json').write_text(json.dumps({
        'original': str(args.shader.resolve()), 'original_sha256': EXPECTED,
        'scope': __doc__.strip(), 'instruction_word_offset': at,
        'experiment': 'lighting' if args.lighting else 'uv-phase',
        'baseline_y': baseline_y, 'variants': variants}, indent=2)+'\n')
    print(args.output)


if __name__ == '__main__':
    main()
