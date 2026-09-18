"""Isolated replay test: fine texture gradients for one pinned courtyard PS.

This does not establish fine gradients as correct for Xbox texture sampling.
Changes only the twelve coarse derivative opcodes and the DXBC checksum.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

from equipment_projection_variants import chunks, shader_words, instructions

ORIGINAL = '6276176da5c8cb7d8186a7e7f05b2bb254badf5015d723cb4e3ce4904c0f1ffa'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    raw = args.source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ORIGINAL:
        raise ValueError('Require exact courtyard PS')
    parts = chunks(raw)
    words = shader_words(parts)
    edits = [(at, op[0] & 2047) for at, op in instructions(words) if op[0] & 2047 in (122, 124)]
    if [op for _, op in edits] != [122, 124] * 6:
        raise ValueError('Unexpected gradient sequence')
    chunk_index = next(i for i, part in enumerate(parts) if part[:4] in (b'SHEX', b'SHDR'))
    chunk_offset = struct.unpack_from('<I', raw, 32 + 4*chunk_index)[0]
    result = bytearray(raw)
    for at, op in edits:
        struct.pack_into('<I', result, chunk_offset + 8 + at*4, (words[at] & ~2047) | (op+1))
    args.output.mkdir(parents=True, exist_ok=False)
    output = args.output / 'fine.dxbc'
    output.write_bytes(result)
    helper = Path(__file__).resolve().parents[1] / 'out/build/RelWithDebInfo/aot_dxbc_checksum.exe'
    checksum = subprocess.check_output([str(helper), str(output)], text=True, timeout=5).strip()
    if len(checksum) != 32:
        raise ValueError('Unexpected checksum')
    result[4:20] = struct.pack('<4I', *(int(checksum[i:i+8], 16) for i in range(0, 32, 8)))
    output.write_bytes(result)
    manifest = {'scope': __doc__, 'variants': [{'file': output.name,
        'sha256': hashlib.sha256(result).hexdigest(), 'original_sha256': ORIGINAL,
        'original': str(args.source.resolve()), 'opcode_edits': edits}]}
    (args.output/'variants.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(args.output)


if __name__ == '__main__':
    main()
