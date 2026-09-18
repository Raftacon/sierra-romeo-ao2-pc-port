"""Captured 720p control: align 2x MSAA Y without changing projection arithmetic."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
from equipment_projection_variants import chunks, shader_words, instructions
from native_projection_variants import disassemble


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    result={'scope':__doc__,'sample_align_720p_control':True,'alignment_only':True,'variants':[]}
    for name,sha,pos,scratch,out in [
        ('color','716b8733296b8b65c3946768dc076baceb32ad70af4ae4d2776feb42f340a50a',11,13,8),
        ('depth','6f5ac15a6b8ecf42d54123dac9b79da1e503ab0af86289780ec8a1b5f610e743',2,4,0),
        ('crate','d398177c40a1c874c502f1626b346ccfda6afacf0143b7129ecbb598d001e0af',5,7,3),
    ]:
        source=a.sources/(sha+'.dxbc');original=source.read_bytes()
        if hashlib.sha256(original).hexdigest()!=sha:raise ValueError('Unexpected captured shader')
        data=bytearray(original)
        if name!='crate':
            parts=chunks(data);words=shader_words(parts);ops=list(instructions(words))
            end=[0x05000036,0x001020F2,out,0x00100E46,pos,0x0100003E]
            if words[-6:]!=end:raise ValueError('Unexpected final position export')
            delta=struct.unpack('<I',struct.pack('<f',1/720))[0]
            body=[0x09000020,0x00100012,scratch,0x0030802A,0,0,13,0x00004001,1,
                  0x0304001F,0x0010000A,scratch,
                  0x09000032,0x00100022,pos,0x0010003A,pos,0x00004001,delta,0x0010001A,pos,
                  0x01000015]
            patched=words[:-6]+body+end;patched[1]=len(patched)
            list(instructions(patched))
            if patched[2:len(words)-6]!=words[2:-6]:raise ValueError('Changed original arithmetic')
            payload=struct.pack('<'+'I'*len(patched),*patched)
            parts=[b'SHEX'+struct.pack('<I',len(payload))+payload if c[:4]==b'SHEX' else c for c in parts]
            offsets=[];offset=32+4*len(parts)
            for c in parts:offsets.append(offset);offset+=len(c)
            data=bytearray(original[:32]+struct.pack('<'+'I'*len(parts),*offsets)+b''.join(parts))
            struct.pack_into('<I',data,24,len(data))
        output=a.output/(name+'.dxbc');output.write_bytes(data)
        if name!='crate':
            helper=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_dxbc_checksum.exe'
            checksum=subprocess.check_output([str(helper),str(output)],text=True,timeout=5).strip()
            if len(checksum)!=32:raise ValueError('Bad checksum')
            data[4:20]=struct.pack('<4I',*(int(checksum[i:i+8],16) for i in range(0,32,8)))
            output.write_bytes(data)
        assembly=disassemble(data)
        if name!='crate' and ('CB0[0][13].z' not in assembly or 'if_nz' not in assembly):
            raise ValueError('Missing MSAA guard')
        output.with_suffix('.txt').write_text(assembly)
        result['variants'].append({'file':output.name,'sha256':hashlib.sha256(data).hexdigest(),
            'original':str(source.resolve()),'original_sha256':sha,'layout':{'name':name}})
    (a.output/'variants.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
