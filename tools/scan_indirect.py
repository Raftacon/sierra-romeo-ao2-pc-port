"""Find conservative entrypoint candidates in pointer tables without RTTI.

This produces analysis hints for review, not proven function boundaries. It
requires consecutive executable pointers in .rdata and a return, indirect tail
branch, or padding word immediately before the candidate. Exception funclets and
interior-label candidates without that evidence are deliberately left out.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import struct

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('image', type=Path)
parser.add_argument('--known', type=Path, required=True, help='Generated *_init.cpp')
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--evidence', type=Path, required=True)
args = parser.parse_args()
data = args.image.read_bytes()
pe = struct.unpack_from('<I', data, 0x3C)[0]
if data[pe:pe + 4] != b'PE\0\0':
    raise SystemExit('Invalid loaded PE image')
base = struct.unpack_from('<I', data, pe + 24 + 28)[0]
count = struct.unpack_from('<H', data, pe + 6)[0]
optional_size = struct.unpack_from('<H', data, pe + 20)[0]
sections = []
for i in range(count):
    offset = pe + 24 + optional_size + 40 * i
    name, size, rva = struct.unpack_from('<8sII', data, offset)
    flags = struct.unpack_from('<I', data, offset + 36)[0]
    sections.append((name.rstrip(b'\0'), rva, size, flags))
known = {int(s, 16) for s in re.findall(r'\{ 0x([0-9A-F]{8}),', args.known.read_text())}


def executable(address):
    return address % 4 == 0 and any(base + rva <= address < base + rva + size for _, rva, size, flags in sections if flags & 0x20000000)


references = defaultdict(list)
for name, rva, size, flags in sections:
    if name not in (b'.rdata', b'.data', b'BINKDATA'):
        continue
    words = struct.unpack_from('>' + str(size // 4) + 'I', data, rva)
    for i, word in enumerate(words):
        if word in known or not executable(word):
            continue
        if not ((i > 0 and executable(words[i - 1])) or (i + 1 < len(words) and executable(words[i + 1]))):
            continue
        previous = struct.unpack_from('>I', data, word - base - 4)[0]
        if previous in (0x4E800020, 0x4E800420, 0x60000000, 0x7D084378, 0) or previous & 0xFC000001 == 0x48000000:
            references[word].append(base + rva + i * 4)
lines = ['# Conservative candidates from tools/scan_indirect.py; runtime coverage pending.',
         '# Exact retail executable required. Each entry has a data table',
         '# reference and a preceding return/tail-branch/padding boundary.', '[functions]']
evidence = []
for address, slots in sorted(references.items()):
    lines.append(f'# table slot 0x{slots[0]:08X}')
    lines.append(f'0x{address:08X} = {{ name = "sub_{address:08X}" }}')
    evidence.append({'address': f'{address:08X}', 'slots': [f'{s:08X}' for s in slots],
                     'previous_word': data[address-base-4:address-base].hex()})
args.output.write_text('\n'.join(lines) + '\n')
args.evidence.write_text(json.dumps(evidence, indent=2) + '\n')
print(f'{len(references)} candidate entrypoints written to {args.output}')
