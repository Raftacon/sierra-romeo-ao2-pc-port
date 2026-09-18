"""Prepare GPU controls with six nonzero planes; never changes the native game."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess

from equipment_projection_variants import chunks, shader_words, instructions
from native_projection_variants import disassemble

PLANES = [[1,0,0,0], [0,1,0,0], [0,0,1,0], [0,0,0,1],
          [.5,-.25,.125,-.0625], [-.25,.5,.25,1]]


def write_shader(source, path, faulty=False):
    data = source.read_bytes()
    parts = chunks(data)
    words = shader_words(parts)
    temps = next(op[1] for _, op in instructions(words) if op[0] == 0x02000068)
    new, plane_indices, injected = words[:2], [], 0
    for _, op in instructions(words):
        if op[0] == 0x09000011 and op[3] == 0x00100E46 and op[5:8] == [0x00308E46,0,0] and 2 <= op[8] <= 7:
            plane_indices.append(op[8] - 2)
            if faulty and not injected:
                # v2's saved host position is the penultimate allocated temp.
                # Inject just before clip distances, retaining its later final
                # position assignment. This isolates wrong-space clipping.
                new.extend([0x05000036,0x001000F2,op[4],0x00100E46,temps-2])
                injected += 1
            literal = list(struct.unpack('<4I', struct.pack('<4f', *PLANES[op[8]-2])))
            op = [0x0A000011, *op[1:5], 0x00004002, *literal]
        new.extend(op)
    if plane_indices != list(range(6)) or injected != int(faulty):
        raise ValueError('Expected precisely six ordered user planes and the requested control')
    new[1] = len(new)
    payload = struct.pack('<'+'I'*len(new), *new)
    parts = [b'SHEX'+struct.pack('<I',len(payload))+payload if p[:4] == b'SHEX' else p for p in parts]
    offsets, pos = [], 32+4*len(parts)
    for part in parts:
        offsets.append(pos)
        pos += len(part)
    result = bytearray(data[:32]+struct.pack('<'+'I'*len(parts),*offsets)+b''.join(parts))
    struct.pack_into('<I',result,24,len(result))
    path.write_bytes(result)
    signer = Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_dxbc_checksum.exe'
    checksum = subprocess.check_output([str(signer),str(path)],text=True,timeout=5).strip()
    if len(checksum)!=32: raise ValueError('Invalid checksum result')
    result[4:20] = struct.pack('<4I',*(int(checksum[i:i+8],16) for i in range(0,32,8)))
    path.write_bytes(result)
    assembly = disassemble(result)
    path.with_suffix('.txt').write_text(assembly)
    return {'file':path.name, 'sha256':hashlib.sha256(result).hexdigest(),
            'source':str(source.resolve()), 'source_sha256':hashlib.sha256(data).hexdigest()}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--matrix',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--decal',action='store_true',help='Use the captured decal program and draw pair')
    p.add_argument('--courtyard',action='store_true',help='Use the captured courtyard impact draw pair')
    a=p.parse_args()
    if a.decal and a.courtyard: p.error('Choose one captured decal case')
    a.output.mkdir(parents=True,exist_ok=False)
    report={'planes':PLANES,'cases':[],'scope':__doc__.strip(),'decal':a.decal,'courtyard':a.courtyard}
    # Neither target guest shader writes vertex kill. The real pipeline cannot
    # request vertex_kill_and for them, even when the raster register selects it.
    for kind,high in [('clip',0x60000),('cull',0xE0000)]:
        case={'kind':kind,'programs':[]}
        programs=([('courtyard','6AF9B63098372D65',0x7F,[4881,4888])] if a.courtyard else
                  [('decal','FB08EF4B31D5E686',0x3F,[11781,11796])] if a.decal else
                  [('color','480333F4AFCF0E1E',0x7F,[7428,7456]),
                   ('depth','D6E05D80EF7DEBF8',0,[4902,4918])])
        for name,guest,mask,events in programs:
            stem=f'{guest}-{high|mask:016X}'
            program={'name':name,'guest':guest,'events':events}
            for mode in ['original','precise','faulty']:
                source=a.matrix/(stem+('-original.dxbc' if mode=='original' else '-precise.dxbc'))
                program[mode]=write_shader(source,a.output/f'{kind}-{name}-{mode}.dxbc',mode=='faulty')
            case['programs'].append(program)
        report['cases'].append(case)
    (a.output/'clip-variants.json').write_text(json.dumps(report,indent=2)+'\n')
    print(a.output)


if __name__=='__main__': main()
