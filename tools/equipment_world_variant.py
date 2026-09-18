"""Emit a replay-only, hash-pinned VS that exports pre-projection world values.

Never install this shader: its position output is diagnostic data, not clip space.
Other outputs and all preceding calculations are preserved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

EXPECTED = '054f88253fa4c3831a5b1310a5c526a33351bc1a9fefdce67301804c4a299817'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--shader', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    data = args.shader.read_bytes()
    if hashlib.sha256(data).hexdigest() != EXPECTED:
        raise ValueError('Require the identified 480333F4AFCF0E1E vertex shader')
    count = struct.unpack_from('<I', data, 28)[0]
    offsets = struct.unpack_from('<'+'I'*count, data, 32)
    chunks = [data[o:o+8+struct.unpack_from('<I', data, o+4)[0]] for o in offsets]
    slot = next(i for i,c in enumerate(chunks) if c[:4] == b'SHEX')
    words = list(struct.unpack('<'+'I'*((len(chunks[slot])-8)//4), chunks[slot][8:]))
    if (words[3878:3883] != [0x05000036,0x001000F2,14,0x00100E46,16]
            or words[7385] != 0x01000016
            or words[7386:7395] != [0x09000001,0x00100012,16,0x0030800A,0,0,0,0x00004001,8]
            or words[7466:] != [0x05000036,0x001020F2,7,0x00100E46,14,0x0100003E]):
        raise ValueError('Unexpected position export or epilogue')
    # Capture r5 at guest instruction 47 instead of the projected r16 value.
    # r5 holds world X, world Y, homogeneous 1, world Z for this shader.
    words[3882] = 5
    # Bypass all host clip/viewport conversion: the exported values are data.
    words[7386:7466] = []
    words[1] = len(words)
    payload = struct.pack('<'+'I'*len(words), *words)
    chunks[slot] = b'SHEX'+struct.pack('<I',len(payload))+payload
    offsets, position = [], 32+4*count
    for chunk in chunks:
        offsets.append(position)
        position += len(chunk)
    result = bytearray(data[:32]+struct.pack('<'+'I'*count,*offsets)+b''.join(chunks))
    struct.pack_into('<I', result, 24, len(result))
    args.output.mkdir(parents=True, exist_ok=False)
    path = args.output/'world-output.dxbc'
    path.write_bytes(result)
    helper = Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_dxbc_checksum.exe'
    checksum = subprocess.check_output([str(helper),str(path)],text=True,timeout=5).strip()
    if len(checksum) != 32:
        raise ValueError('Unexpected DXBC checksum')
    result[4:20] = struct.pack('<4I',*(int(checksum[i:i+8],16) for i in range(0,32,8)))
    path.write_bytes(result)
    (args.output/'variant.json').write_text(json.dumps({'original':str(args.shader.resolve()),
        'original_sha256':EXPECTED,'file':path.name,'sha256':hashlib.sha256(result).hexdigest(),
        'scope':__doc__.strip()},indent=2)+'\n')
    print(path)


if __name__ == '__main__':
    main()
