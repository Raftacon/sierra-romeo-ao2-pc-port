"""Replace only exact, remapped projection snippets in captured vertex shaders.

Offline experiment. Original guest instructions, source capture, viewport
arithmetic and global refactoring policy are preserved. Unknown layouts fail.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

from equipment_projection_variants import chunks, instructions, shader_words
from native_projection_variants import disassemble


def operands(op):
    cursor = 1
    while cursor < len(op):
        start = cursor
        token = op[cursor]
        cursor += 1
        if token >> 31:
            raise ValueError('Unexpected extended snippet operand')
        kind, dimensions = (token >> 12) & 255, (token >> 20) & 3
        if kind not in (0, 1, 2, 8) or not dimensions:
            raise ValueError('Unexpected snippet operand kind')
        if any((token >> (22 + 3*i)) & 7 for i in range(dimensions)) or cursor + dimensions > len(op):
            raise ValueError('Unexpected relative/truncated operand')
        yield start, token, kind, op[cursor:cursor+dimensions]
        cursor += dimensions
    if cursor != len(op):
        raise ValueError('Operand boundary mismatch')


def snippet(data):
    parts = chunks(data)
    ops = [op for _, op in instructions(shader_words(parts))]
    body = [op for op in ops if op[0] & 2047 != 62 and not 88 <= op[0] & 2047 <= 106]
    return dict(body=body, temps=next(op[1] for op in ops if op[0] & 2047 == 104),
                globals=next(op[0] for op in ops if op[0] & 2047 == 106),
                features=struct.unpack('<2I', next(part[8:] for part in parts if part[:4] == b'SFI0')))


def match(template, actual):
    bindings = {}
    def bind(key, value):
        if key in bindings and bindings[key] != value:
            raise ValueError('Inconsistent projection binding')
        bindings[key] = value
    try:
        for expected, observed in zip(template, actual):
            if expected[0] != observed[0] or len(expected) != len(observed):
                return None
            a, b = list(operands(expected)), list(operands(observed))
            if len(a) != len(b):
                return None
            for (at, token, kind, indices), (other_at, other_token, other_kind, other_indices) in zip(a, b):
                if at != other_at or len(indices) != len(other_indices):
                    return None
                if kind == 0:
                    if token != other_token or len(indices) != 1:
                        return None
                    bind('base', other_indices[0] - indices[0])
                elif kind in (1, 2):
                    native_token = token & ~(255 << 12)
                    if other_kind != 0 or len(indices) != 1 or indices[0] != 0:
                        return None
                    if kind == 1:
                        if native_token & ~0xff0 != other_token & ~0xff0:
                            return None
                        if (token & 3) != 2 or (token >> 2) & 3 != 1:
                            return None
                        for lane in range(4):
                            bind('swizzle%d' % ((token >> (4+2*lane)) & 3),
                                 (other_token >> (4+2*lane)) & 3)
                    elif native_token != other_token:
                        return None
                    bind('world' if kind == 1 else 'position', other_indices[0])
                elif kind == 8:
                    if token != other_token or len(indices) != 3 or indices[1:] != other_indices[1:]:
                        return None
                    bind('buffer%d' % indices[1], other_indices[0])
        if set(bindings) != {'base', 'world', 'position', 'buffer0', 'buffer1',
                             'swizzle0', 'swizzle1', 'swizzle2', 'swizzle3'} or bindings['base'] < 0:
            return None
        return bindings
    except ValueError:
        return None


def remap(body, bindings):
    result = []
    for instruction in body:
        op = list(instruction)
        for at, token, kind, indices in operands(instruction):
            if kind == 0:
                op[at+1] += bindings['base']
            elif kind in (1, 2):
                op[at] &= ~(255 << 12)
                op[at+1] = bindings['world' if kind == 1 else 'position']
                if kind == 1:
                    op[at] &= ~0xff0
                    for lane in range(4):
                        op[at] |= bindings['swizzle%d' % ((token >> (4+2*lane)) & 3)] << (4+2*lane)
            elif kind == 8:
                op[at+1] = bindings['buffer%d' % indices[1]]
        result.extend(op)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--groups', type=Path, required=True)
    parser.add_argument('--snippets', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.groups.read_bytes(); groups = json.loads(raw)
    if not groups['complete']:
        raise ValueError('Incomplete capture grouping')
    old = snippet((args.snippets / 'original.dxbc').read_bytes())
    new = snippet((args.snippets / 'candidate.dxbc').read_bytes())
    if old['temps'] != new['temps'] or new['temps'] != 5:
        raise ValueError('Candidate changes temporary allocation')
    delta_flags = new['globals'] & ~old['globals']
    if delta_flags != 0x20000 or old['features'] != (1, 0) or new['features'] != (33, 0):
        raise ValueError('Unexpected shader capability change')
    hashes = sorted({event['shaders']['Vertex'] for event in groups['events']
                     if event.get('shaders', {}).get('Vertex') and
                     (groups['shaders'][event['shaders']['Vertex']].get('feature_flags') or 0) & 1})
    if not hashes:
        raise ValueError('No double-precision vertex programs')
    args.output.mkdir(parents=True, exist_ok=False)
    report = dict(complete=False, groups=str(args.groups.resolve()),
                  groups_sha256=hashlib.sha256(raw).hexdigest(), capture_sha256=groups['capture_sha256'],
                  snippets={name: hashlib.sha256((args.snippets / (name+'.dxbc')).read_bytes()).hexdigest()
                            for name in ('original', 'candidate')}, variants=[], scope=__doc__)
    try:
        for sha in hashes:
            data = (args.groups.parent / (sha+'.dxbc')).read_bytes()
            if hashlib.sha256(data).hexdigest() != sha:
                raise ValueError('Captured shader identity changed')
            parts = chunks(data); words = shader_words(parts); ops = list(instructions(words))
            matches = []
            for i in range(len(ops) - len(old['body']) + 1):
                if ops[i][1][0] != old['body'][0][0]:
                    continue
                observed = [op for _, op in ops[i:i+len(old['body'])]]
                bindings = match(old['body'], observed)
                if bindings is not None:
                    # Reconstruct the whole old block independently before changing it.
                    if remap(old['body'], bindings) != sum(observed, []):
                        raise ValueError('Original snippet reconstruction failed')
                    matches.append((ops[i][0], sum(map(len, observed)), bindings))
            if not matches or any(a[0]+a[1] > b[0] for a, b in zip(matches, matches[1:])):
                raise ValueError('Missing or overlapping projection block: ' + sha)
            for start, length, bindings in reversed(matches):
                words[start:start+length] = remap(new['body'], bindings)
            flag_at = next(at for at, op in instructions(words) if op[0] & 2047 == 106)
            original_flags = words[flag_at]
            words[flag_at] |= delta_flags
            if words[flag_at] & (1 << 11) != original_flags & (1 << 11):
                raise ValueError('Global refactoring policy changed')
            words[1] = len(words)
            payload = struct.pack('<'+'I'*len(words), *words)
            parts = [part for part in parts if part[:4] != b'STAT']
            for i, part in enumerate(parts):
                if part[:4] in (b'SHEX', b'SHDR'):
                    parts[i] = part[:4] + struct.pack('<I', len(payload)) + payload
                elif part[:4] == b'SFI0':
                    low, high = struct.unpack('<2I', part[8:])
                    parts[i] = part[:8] + struct.pack('<2I', low | 32, high)
            offsets = []; position = 32 + 4*len(parts)
            for part in parts:
                offsets.append(position); position += len(part)
            result = bytearray(data[:32] + struct.pack('<'+'I'*len(parts), *offsets) + b''.join(parts))
            struct.pack_into('<II', result, 24, len(result), len(parts))
            path = args.output / (sha+'.dxbc'); path.write_bytes(result)
            signer = Path(__file__).resolve().parents[1] / 'out/build/RelWithDebInfo/aot_dxbc_checksum.exe'
            checksum = subprocess.check_output([str(signer), str(path)], text=True, timeout=5).strip()
            if len(checksum) != 32:
                raise ValueError('Unexpected checksum')
            result[4:20] = struct.pack('<4I', *(int(checksum[i:i+8], 16) for i in range(0, 32, 8)))
            path.write_bytes(result)
            (args.output / (sha+'.asm')).write_text(disassemble(result))
            report['variants'].append(dict(original_sha256=sha, file=path.name,
                sha256=hashlib.sha256(result).hexdigest(), blocks=len(matches),
                bindings=[item[2] for item in matches]))
        report['complete'] = True
    finally:
        (args.output / 'variants.json').write_text(json.dumps(report, indent=2)+'\n')
    print('Replaced projection blocks in', len(report['variants']), 'captured vertex shaders')


if __name__ == '__main__':
    main()
