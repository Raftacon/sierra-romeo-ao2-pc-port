"""Build a replay fixture using the native decal patcher, plus a guard-off control."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from equipment_projection_variants import chunks, shader_words, instructions
from native_projection_variants import disassemble


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--fallback', action='store_true')
    p.add_argument('--courtyard', action='store_true', help='Use the captured courtyard impact shader')
    a = p.parse_args()
    raw = a.source.read_bytes()
    original_sha = hashlib.sha256(raw).hexdigest()
    expected = ('a7267fb37ed936e2292a79794b23943bb7a5a57bba5d7904779aec68a4f0a75c' if a.courtyard else
                'cb0554cf65e315b18b5e419f5ebc07f0100505631a03a81889516670f76b632d')
    if original_sha != expected:
        raise ValueError('Require verified captured decal shader')
    a.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    helper = root / 'out/build/RelWithDebInfo/aot_projection_patch.exe'
    output = a.output / 'decal.dxbc'
    guest, modification, position, scratch = (('6AF9B63098372D65', '7F', '9', '11') if a.courtyard else
                                             ('FB08EF4B31D5E686', '3F', '12', '14'))
    subprocess.run([str(helper), guest, modification, str(a.source), str(output),
                    position, scratch, '0', '2'], check=True, timeout=10)
    data = bytearray(output.read_bytes())
    if a.fallback:
        offset = next(o for o in struct.unpack_from('<'+'I'*struct.unpack_from('<I', data, 28)[0], data, 32)
                      if data[o:o+4] == b'SHEX')
        changed = 0
        words = shader_words(chunks(data))
        guard = next(op[1] for _, op in instructions(words) if op[0] == 0x02000068)-1
        for at, op in instructions(words):
            if op == [0x07000020,0x00100012,guard,0x0010000A,guard,0x00004001,8]:
                struct.pack_into('<I', data, offset+8+(at+6)*4, 16)
                changed += 1
        if changed != 2: raise ValueError('Require exactly two mode guards')
        output.write_bytes(data)
        signer = root / 'out/build/RelWithDebInfo/aot_dxbc_checksum.exe'
        checksum = subprocess.check_output([str(signer), str(output)], text=True, timeout=5).strip()
        if len(checksum) != 32: raise ValueError('Invalid checksum')
        data[4:20] = struct.pack('<4I', *(int(checksum[i:i+8],16) for i in range(0,32,8)))
        output.write_bytes(data)
    assembly = disassemble(data)
    if 'dmul' not in assembly or 'dadd' not in assembly: raise ValueError('Missing precise projection')
    output.with_suffix('.txt').write_text(assembly)
    manifest = {'scope': __doc__, 'fallback': a.fallback, 'courtyard': a.courtyard,
                'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
                'variants': [{'file': output.name, 'sha256': hashlib.sha256(data).hexdigest(),
                              'original': str(a.source.resolve()), 'original_sha256': original_sha}]}
    (a.output / 'variants.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(a.output)


if __name__ == '__main__': main()
