"""Exercise the native C++ projection patcher on captured translator output."""
import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from equipment_projection_variants import blob_bytes, chunks, shader_words, instructions


def disassemble(data):
    f = c.WinDLL('d3dcompiler_47.dll').D3DDisassemble
    f.argtypes = [c.c_void_p, c.c_size_t, c.c_uint, c.c_char_p, c.POINTER(c.c_void_p)]
    f.restype = c.c_long
    blob = c.c_void_p()
    source = c.create_string_buffer(bytes(data))
    status = f(source, len(data), 0, None, c.byref(blob))
    text = blob_bytes(blob).decode(errors='replace').rstrip('\0')
    if status < 0 or not text:
        raise ValueError('Independent DXBC disassembly failed')
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shaders', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--translated-layout', action='store_true', help='Exercise metadata-based variant support')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    helper = root / 'out/build/RelWithDebInfo/aot_projection_patch.exe'
    signer = root / 'out/build/RelWithDebInfo/aot_dxbc_checksum.exe'
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(), 'variants': [], 'controls': []}
    for name, guest, mod in [('color', '480333F4AFCF0E1E', '7F'), ('depth', 'D6E05D80EF7DEBF8', '0'),
                             ('color-no-interpolators', '480333F4AFCF0E1E', '0')]:
        source = (args.shaders / f'shader_{guest}_{int(mod,16):016X}.d3d12.bin.vert').resolve()
        output = args.output / (name + '.dxbc')
        metadata = (['14','16','0','2'] if name.startswith('color') else ['10','12','0','2']) if args.translated_layout else []
        subprocess.run([str(helper), guest, mod, str(source), str(output), *metadata], check=True, timeout=10)
        raw = output.read_bytes()
        text = disassemble(raw)
        (output.with_suffix('.dxbc.txt')).write_text(text)
        if text.count('if_nz') < 2 or 'dmul' not in text or 'dadd' not in text:
            raise ValueError('Expected guarded double projection')
        variant = {'file': output.name, 'sha256': hashlib.sha256(raw).hexdigest(),
                   'original': str(source), 'original_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                   'layout': {'name': name}, 'guest': guest, 'modification': mod}
        # Force both guards to use an impossible masked value. This exercises
        # the unmodified host epilogue on the actual GPU, with original inputs.
        data = bytearray(raw)
        offset = next(o for o in struct.unpack_from('<'+'I'*struct.unpack_from('<I', data, 28)[0], data, 32)
                      if data[o:o+4] == b'SHEX')
        words = shader_words(chunks(data))
        changed = 0
        for at, op in instructions(words):
            if (op[0] in (0x07000020, 0x07000027) and op[1] == 0x00100012
                    and op[2] in ((25, 29) if args.translated_layout else (24, 28)) and op[3:5] == [0x0010000A, op[2]] and op[5:] == [0x00004001, 8]):
                struct.pack_into('<I', data, offset+8+(at+6)*4, 16)
                changed += 1
        if changed != 2:
            raise ValueError('Expected precisely two new guards')
        fallback = args.output / (name + '-fallback.dxbc')
        fallback.write_bytes(data)
        checksum = subprocess.check_output([str(signer), str(fallback)], text=True, timeout=5).strip()
        data[4:20] = struct.pack('<4I', *(int(checksum[i:i+8], 16) for i in range(0, 32, 8)))
        fallback.write_bytes(data)
        disassemble(data)
        variant.update(fallback_file=fallback.name, fallback_sha256=hashlib.sha256(data).hexdigest())
        report['variants'].append(variant)
        if name == 'color':
            corrupted = args.output / 'corrupted-input.dxbc'
            bad = bytearray(source.read_bytes()); bad[-1] ^= 1; corrupted.write_bytes(bad)
            for test, test_guest, test_mod, input_, expected in [
                ('corrupt', guest, mod, corrupted, 5),
                ('unrelated-shader', '12345678', mod, source, 4), ('already-patched', guest, mod, output, 5)]:
                dest = args.output / (test + '-must-not-exist.dxbc')
                result = subprocess.run([str(helper), test_guest, test_mod, str(input_), str(dest), *metadata],
                                        capture_output=True, text=True, timeout=10)
                if result.returncode != expected or dest.exists():
                    raise ValueError('Rejection control failed: ' + test)
                report['controls'].append({'name': test, 'exit_code': result.returncode})
            if not args.translated_layout:
                result = subprocess.run([str(helper),guest,'FF',str(source),str(args.output/'unknown-must-not-exist.dxbc')], capture_output=True, timeout=10)
                if result.returncode!=5 or (args.output/'unknown-must-not-exist.dxbc').exists():
                    raise ValueError('Unknown-modification rejection failed')
                report['controls'].append({'name':'unknown-modification','exit_code':result.returncode})
    # Main replay examines the paired color/depth programs; preserve the third
    # independently decoded variant separately for the no-interpolator pass.
    report['additional_variants'] = report['variants'][2:]
    report['variants'] = report['variants'][:2]
    (args.output / 'variants.json').write_text(json.dumps(report, indent=2)+'\n')
    print(args.output)


if __name__ == '__main__':
    main()
