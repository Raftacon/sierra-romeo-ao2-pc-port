"""Compare native translator variants against the separately validated FMA splice.

Require identical unpatched translations, matching variant coverage, unchanged
non-projection chunks and exact program-token equality after the offline splice.
This checks translator integration, not rendered output or native frame timing.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import struct

from equipment_projection_variants import chunks, instructions, shader_words
from projection_fma_variants import snippet, match, remap


def expected_words(data, old, new):
    words = shader_words(chunks(data))
    ops = list(instructions(words))
    matches = []
    for i in range(len(ops) - len(old['body']) + 1):
        if ops[i][1][0] != old['body'][0][0]:
            continue
        actual = [op for _, op in ops[i:i+len(old['body'])]]
        bindings = match(old['body'], actual)
        if bindings is not None:
            if remap(old['body'], bindings) != sum(actual, []):
                raise ValueError('Original block reconstruction differs')
            matches.append((ops[i][0], sum(map(len, actual)), bindings))
    if len(matches) != 1:
        raise ValueError('Require exactly one projection block')
    start, length, bindings = matches[0]
    words[start:start+length] = remap(new['body'], bindings)
    flag_at = next(at for at, op in instructions(words) if op[0] & 2047 == 106)
    words[flag_at] |= 0x20000
    words[1] = len(words)
    return words


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('control', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--snippets', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Require a new output file')
    def table(root):
        with (root / 'translations.tsv').open() as f:
            return list(csv.DictReader(f, delimiter='\t'))
    a, b = table(args.control), table(args.candidate)
    if not a or len(a) != len(b):
        raise ValueError('Variant coverage differs')
    for control, candidate in zip(a, b):
        if {k:v for k,v in control.items() if k != 'after_xxh3'} != {
                k:v for k,v in candidate.items() if k != 'after_xxh3'}:
            raise ValueError('Variant metadata or original translation differs')
        if control['before_xxh3'] == control['after_xxh3'] and control != candidate:
            raise ValueError('FMA changed an unrecognized program')
    names = sorted(p.name for p in args.control.glob('*-precise.dxbc'))
    if not names or names != sorted(p.name for p in args.candidate.glob('*-precise.dxbc')):
        raise ValueError('Saved representative coverage differs')
    old = snippet((args.snippets / 'original.dxbc').read_bytes())
    new = snippet((args.snippets / 'candidate.dxbc').read_bytes())
    records = []
    for name in names:
        control = (args.control / name).read_bytes()
        candidate = (args.candidate / name).read_bytes()
        original_name = name.replace('-precise.dxbc', '-original.dxbc')
        if (args.control / original_name).read_bytes() != (args.candidate / original_name).read_bytes():
            raise ValueError('Saved unpatched program differs: ' + name)
        cp, fp = chunks(control), chunks(candidate)
        if [p[:4] for p in cp] != [p[:4] for p in fp]:
            raise ValueError('Container chunks differ: ' + name)
        has_projection = bool(struct.unpack('<2I', next(p[8:] for p in cp if p[:4] == b'SFI0'))[0] & 1)
        if not has_projection:
            if control != candidate:
                raise ValueError('Unrecognized program changed: ' + name)
        else:
            if expected_words(control, old, new) != shader_words(fp):
                raise ValueError('Native FMA program differs from validated splice: ' + name)
            for before, after in zip(cp, fp):
                if before[:4] in (b'SHDR', b'SHEX'):
                    continue
                if before[:4] == b'SFI0':
                    low, high = struct.unpack('<2I', before[8:])
                    before = before[:8] + struct.pack('<2I', low | 32, high)
                if before != after:
                    raise ValueError('Unrelated chunk changed: ' + name)
        records.append(dict(file=name, corrected=has_projection,
                            control_sha256=hashlib.sha256(control).hexdigest(),
                            candidate_sha256=hashlib.sha256(candidate).hexdigest()))
    report = dict(complete=True, scope=__doc__, variants=len(a),
                  representatives=records, corrected=sum(r['corrected'] for r in records))
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({k:v for k,v in report.items() if k != 'representatives'}, indent=2))


if __name__ == '__main__':
    main()
