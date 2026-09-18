"""Build replay-only bounded-division variants of captured transfer PS programs.

Only the wrapped 11-bit source-tile / 10-bit source-pitch divide is replaced.
The source pitch must be nonzero. Guest material shaders and other divides are
untouched. No production binary or default is changed by this experiment.
"""
import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from equipment_projection_variants import blob_bytes, chunks, instructions, shader_words
from native_projection_variants import disassemble


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--groups', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    group_bytes = args.groups.read_bytes()
    groups = json.loads(group_bytes)
    if not groups['complete'] or groups.get('error'):
        raise ValueError('Require completed shader grouping')
    args.output.mkdir(parents=True, exist_ok=False)
    hlsl = Path(__file__).with_name('transfer_tile_division.hlsl').read_text()
    hlsl += '\nuint2 main(nointerpolation uint2 value : TEXCOORD0) : SV_Target0 { return AotDivideTile(value.x, value.y); }\n'
    compile_shader = c.WinDLL('d3dcompiler_47.dll').D3DCompile
    compile_shader.argtypes = [c.c_void_p,c.c_size_t,c.c_char_p,c.c_void_p,c.c_void_p,c.c_char_p,c.c_char_p,
                              c.c_uint,c.c_uint,c.POINTER(c.c_void_p),c.POINTER(c.c_void_p)]
    compile_shader.restype = c.c_long
    raw = hlsl.encode(); buffer = c.create_string_buffer(raw); code, errors = c.c_void_p(), c.c_void_p()
    status = compile_shader(buffer,len(raw),None,None,None,b'main',b'ps_5_1',0,0,c.byref(code),c.byref(errors))
    snippet, messages = blob_bytes(code), blob_bytes(errors).decode(errors='replace')
    if status < 0 or not snippet:
        raise ValueError('Snippet compile failed: ' + messages)
    (args.output/'snippet.hlsl').write_text(hlsl)
    (args.output/'snippet.dxbc').write_bytes(snippet)
    (args.output/'snippet.asm').write_text(disassemble(snippet))
    snippet_ops = list(instructions(shader_words(chunks(snippet))))
    extra_temps = next(op[1] for _,op in snippet_ops if op[0]&2047 == 104)
    originals = sorted({e['shaders']['Pixel'] for e in groups['events']
                        if e.get('shaders',{}).get('Vertex') == 'e385943b38a4483c6472fe1a463bd6588ab5ddb4d92b9fc11056b29ffb26e10e'})
    report = {'scope': __doc__, 'groups': str(args.groups.resolve()),
              'groups_sha256': hashlib.sha256(group_bytes).hexdigest(), 'capture_sha256': groups['capture_sha256'],
              'variants': [], 'compiler_messages': messages}
    anchor = [0x0900004e,0x00100082,1,0x00100012,2,0x00100ff6,1,0x00100006,2]
    masked = [0x07000001,0x00100082,1,0x0010003a,1,0x00004001,2047]
    for sha in originals:
        original = args.groups.parent/(sha+'.dxbc'); data = original.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha or b'xe_transfer_address' not in data:
            raise ValueError('Unexpected transfer shader')
        parts = chunks(data); words = shader_words(parts); ops = list(instructions(words))
        found = [i for i,(_,op) in enumerate(ops) if op == anchor]
        if len(found) != 1:
            raise ValueError('Require unique bounded source-pitch divide: '+sha)
        index = found[0]
        extract = ops[index-1][1]
        if (ops[index-2][1] != masked or len(extract) != 11 or
                extract[:7] != [0x0b00008a,0x00100012,2,0x00004001,10,0x00004001,10] or
                extract[7] != 0x0030800a):
            raise ValueError('Bounded numerator / denominator provenance mismatch')
        at = ops[index][0]
        temp_at, temps = next((offset,op[1]) for offset,op in ops if op[0]&2047 == 104)
        if temps != 3:
            raise ValueError('Unexpected transfer scratch allocation')
        input_reg, output_reg = temps + extra_temps, temps + extra_temps + 1
        body = [0x05000036,0x00100012,input_reg,0x0010003a,1,
                0x05000036,0x00100022,input_reg,0x0010000a,2]
        for _,original_op in snippet_ops:
            opcode = original_op[0]&2047
            if 88 <= opcode <= 106 or opcode == 62:
                continue
            if opcode not in (0,14,28,35,54,86):
                raise ValueError('Unexpected snippet instruction '+str(opcode))
            op = list(original_op); cursor = 1
            while cursor < len(op):
                token_at = cursor; token = op[cursor]; cursor += 1
                kind = (token >> 12)&255
                if token >> 31:
                    while op[cursor] >> 31: cursor += 1
                    cursor += 1
                if kind == 4:
                    count = 1 if token & 3 == 1 else 4 if token & 3 == 2 else 0
                    if not count: raise ValueError('Unexpected immediate')
                    cursor += count
                    continue
                dimensions = (token >> 20)&3
                if dimensions != 1 or (token >> 22)&7:
                    raise ValueError('Unexpected operand addressing')
                if kind == 0: op[cursor] += temps
                elif kind in (1,2):
                    if op[cursor] != 0: raise ValueError('Unexpected snippet interface register')
                    op[token_at] = token & ~(255 << 12)
                    op[cursor] = input_reg if kind == 1 else output_reg
                else: raise ValueError('Unexpected snippet operand type')
                cursor += dimensions
            if cursor != len(op): raise ValueError('Operand length mismatch')
            body.extend(op)
        body.extend([0x05000036,0x00100082,1,0x0010000a,output_reg,
                     0x05000036,0x00100012,2,0x0010001a,output_reg])
        words[at:at+len(anchor)] = body
        words[temp_at+1] = temps + extra_temps + 2
        words[1] = len(words)
        payload = struct.pack('<'+'I'*len(words),*words)
        # STAT is optional compiler metadata; remove stale counts after editing.
        parts = [part for part in parts if part[:4] != b'STAT']
        for i,part in enumerate(parts):
            if part[:4] in (b'SHEX',b'SHDR'):
                parts[i] = part[:4]+struct.pack('<I',len(payload))+payload
        offsets=[]; position=32+4*len(parts)
        for part in parts: offsets.append(position); position += len(part)
        result=bytearray(data[:32]+struct.pack('<'+'I'*len(parts),*offsets)+b''.join(parts))
        struct.pack_into('<II',result,24,len(result),len(parts))
        path=args.output/(sha+'.dxbc');path.write_bytes(result)
        signer=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_dxbc_checksum.exe'
        checksum=subprocess.check_output([str(signer),str(path)],text=True,timeout=5).strip()
        if len(checksum)!=32:raise ValueError('Invalid checksum')
        result[4:20]=struct.pack('<4I',*(int(checksum[i:i+8],16) for i in range(0,32,8)))
        path.write_bytes(result)
        (args.output/(sha+'.asm')).write_text(disassemble(result))
        report['variants'].append({'original':str(original.resolve()),'original_sha256':sha,
                                  'file':path.name,'sha256':hashlib.sha256(result).hexdigest()})
    (args.output/'variants.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Built',len(report['variants']),'replay-only transfer shader variants')


if __name__=='__main__':main()
