"""Compile an offline projection candidate; never replace a runtime shader.

Only world dot-product terms formed from two float32 inputs are fused. Their
finite products are exact in float64. The viewport terms multiply a previously
rounded float64 accumulator, so that arithmetic deliberately stays separate.
Compilation and instruction counts do not establish hardware parity or speed.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import struct

import equipment_projection_variants as projection
from equipment_projection_variants import chunks, instructions, shader_words
from native_projection_variants import disassemble


def candidate_source(original):
    result = original
    for row, component in ((2, 'w'), (1, 'y'), (0, 'x')):
        old = f'  p = p + (double4)mat[{row}] * (double)world.{component};'
        if result.count(old) != 1:
            raise ValueError('Projection arithmetic anchor changed')
        result = result.replace(old, f'  p = fma((double4)mat[{row}], (double)world.{component}, p);')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    original = projection.HLSL
    report = dict(complete=False, scope=__doc__, variants={})
    try:
        for name, hlsl in (('original', original), ('candidate', candidate_source(original))):
            projection.HLSL = hlsl
            data, messages = projection.compile_snippet()
            if messages:
                raise ValueError('Unexpected compiler diagnostic: ' + messages)
            parts = chunks(data)
            ops = [op for _, op in instructions(shader_words(parts))]
            assembly = disassemble(data)
            (args.output / (name + '.hlsl')).write_text(hlsl)
            (args.output / (name + '.dxbc')).write_bytes(data)
            (args.output / (name + '.asm')).write_text(assembly)
            body = [op for op in ops if not (88 <= op[0] & 2047 <= 106)]
            report['variants'][name] = dict(sha256=hashlib.sha256(data).hexdigest(),
                instructions=len(body), opcode_counts=dict(Counter(op[0] & 2047 for op in body)),
                temps=next(op[1] for op in ops if op[0] & 2047 == 104),
                global_flags=next(op[0] for op in ops if op[0] & 2047 == 106),
                feature_flags=list(struct.unpack('<2I', next(part[8:] for part in parts if part[:4] == b'SFI0'))))
        if 'dfma' not in (args.output / 'candidate.asm').read_text():
            raise ValueError('Compiler did not emit double fused multiply-add')
        report['complete'] = True
    finally:
        projection.HLSL = original
        (args.output / 'compilation.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
