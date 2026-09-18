"""Build replay-only high-precision projection variants for two pinned VS programs.

This is a captured-state experiment: clip-space input flags must equal 8 under
mask 14. It is not a general shader optimizer or an installable runtime fix.
"""
import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import struct
import subprocess

HLSL = '''cbuffer System : register(b0) { float4 sys[29]; };
cbuffer Projection : register(b1) { float4 mat[256]; };
float4 main(float4 world : TEXCOORD0) : SV_Position {
  precise double4 p = (double4)mat[3] * (double)world.z;
  p = p + (double4)mat[2] * (double)world.w;
  p = p + (double4)mat[1] * (double)world.y;
  p = p + (double4)mat[0] * (double)world.x;
  p.xyz = p.xyz * (double3)sys[8].xyz + p.www * (double3)sys[9].xyz;
  return (float4)p;
}
'''
LAYOUTS = {
    '054f88253fa4c3831a5b1310a5c526a33351bc1a9fefdce67301804c4a299817':
        {'name':'color','position':14,'world':5,'scratch':16,'output':7},
    '3097f03d3ce3ebd7ea1b700f450daf6f1414c03d0b188e278604ffa172360112':
        {'name':'depth','position':10,'world':1,'scratch':12,'output':0},
}
ALLOW_SAMPLE_ALIGNMENT_LITERALS = False


def blob_bytes(blob):
    if not blob.value: return b''
    v=c.cast(blob,c.POINTER(c.POINTER(c.c_void_p))).contents
    pointer=c.WINFUNCTYPE(c.c_void_p,c.c_void_p)(v[3])
    size=c.WINFUNCTYPE(c.c_size_t,c.c_void_p)(v[4])
    release=c.WINFUNCTYPE(c.c_ulong,c.c_void_p)(v[2])
    try: return c.string_at(pointer(blob),size(blob))
    finally: release(blob)


def compile_snippet():
    f=c.WinDLL('d3dcompiler_47.dll').D3DCompile
    f.argtypes=[c.c_void_p,c.c_size_t,c.c_char_p,c.c_void_p,c.c_void_p,c.c_char_p,c.c_char_p,
                c.c_uint,c.c_uint,c.POINTER(c.c_void_p),c.POINTER(c.c_void_p)]
    f.restype=c.c_long
    code,error=c.c_void_p(),c.c_void_p()
    raw=HLSL.encode();buffer=c.create_string_buffer(raw)
    status=f(buffer,len(raw),None,None,None,b'main',b'vs_5_1',0,0,c.byref(code),c.byref(error))
    data,messages=blob_bytes(code),blob_bytes(error).decode(errors='replace')
    if status<0 or not data: raise ValueError('HLSL compile failed: '+messages)
    return data,messages


def chunks(data):
    if len(data)<32 or len(data)>1024*1024 or data[:4]!=b'DXBC':
        raise ValueError('Require a bounded DXBC container')
    count=struct.unpack_from('<I',data,28)[0]
    if count>32 or 32+4*count>len(data): raise ValueError('Invalid DXBC chunk count')
    result=[]
    for o in struct.unpack_from('<'+'I'*count,data,32):
        if o%4 or o+8>len(data): raise ValueError('Invalid DXBC chunk offset')
        size=struct.unpack_from('<I',data,o+4)[0]
        if size%4 or o+8+size>len(data): raise ValueError('Invalid DXBC chunk size')
        result.append(data[o:o+8+size])
    return result


def instructions(words):
    at=2
    while at<len(words):
        size=words[at+1] if words[at]&2047==53 else words[at]>>24&127
        if not size or at+size>len(words): raise ValueError('Invalid instruction boundary')
        yield at,words[at:at+size]
        at+=size


def shader_words(parts):
    part=next(p for p in parts if p[:4] in (b'SHEX',b'SHDR'))
    return list(struct.unpack('<'+'I'*((len(part)-8)//4),part[8:]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',type=Path,required=True,help='Hash-named DXBC directory from equipment pass inventory')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    snippet,messages=compile_snippet()
    snippet_parts=chunks(snippet);snip=shader_words(snippet_parts)
    declarations=[a for _,a in instructions(snip) if a[0]&2047==104]
    if len(declarations)!=1: raise ValueError('Unexpected snippet temporary declaration')
    extra_temps=declarations[0][1]
    globals_=next(a[0] for _,a in instructions(snip) if a[0]&2047==106)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'projection.hlsl').write_text(HLSL)
    (args.output/'snippet.dxbc').write_bytes(snippet)
    report={'scope':__doc__.strip(),'compiler_messages':messages,'variants':[]}
    for sha,layout in LAYOUTS.items():
        source=args.sources/(sha+'.dxbc');data=source.read_bytes()
        if hashlib.sha256(data).hexdigest()!=sha: raise ValueError('Original VS hash mismatch')
        parts=chunks(data);words=shader_words(parts);ops=list(instructions(words))
        temps_at,temps=next((at,a[1]) for at,a in ops if a[0]&2047==104)
        anchor=[0x05000036,0x001000F2,layout['position'],0x00100E46,layout['scratch']]
        matches=[at for at,a in ops if a==anchor]
        if len(matches)!=1: raise ValueError('Expected one original position assignment')
        epilogue=len(words)-86
        if (words[epilogue:epilogue+9] != [0x09000001,0x00100012,layout['scratch'],0x0030800A,0,0,0,0x00004001,8]
                or words[-6:] != [0x05000036,0x001020F2,layout['output'],0x00100E46,layout['position'],0x0100003E]):
            raise ValueError('Unexpected host position epilogue')
        body=[]
        for _,original in instructions(snip):
            opcode=original[0]&2047
            if 88<=opcode<=106 or opcode==62: continue
            if opcode==53: raise ValueError('Unexpected custom-data instruction')
            op=list(original);cursor=1
            while cursor<len(op):
                token_at=cursor;token=op[cursor];cursor+=1
                operand_type=(token>>12)&255
                if operand_type in (4,5):
                    count = {0x00004001: 1, 0x00005002: 4}.get(token)
                    if not ALLOW_SAMPLE_ALIGNMENT_LITERALS or count is None or cursor+count>len(op):
                        raise ValueError('Unexpected literal operand in snippet')
                    cursor += count
                    continue
                if token>>31:
                    while op[cursor]>>31: cursor+=1
                    cursor+=1
                dimensions=(token>>20)&3
                if any((token>>(22+3*i))&7 for i in range(dimensions)):
                    raise ValueError('Unexpected relative operand in snippet')
                if operand_type==0: op[cursor]+=temps
                elif operand_type in (1,2):
                    if dimensions!=1 or op[cursor]!=0: raise ValueError('Unexpected snippet input/output')
                    op[token_at]=token & ~(255<<12)
                    op[cursor]=layout['world'] if operand_type==1 else layout['position']
                elif operand_type==8:
                    if dimensions!=3 or op[cursor+1] not in (0,1): raise ValueError('Unexpected snippet constant buffer')
                    op[cursor]=0 if op[cursor+1]==0 else 2
                else: raise ValueError('Unexpected operand type '+str(operand_type))
                cursor+=dimensions
            if cursor!=len(op): raise ValueError('Operand decoding mismatch')
            body.extend(op)
        words[epilogue:-6]=[]
        words[matches[0]+5:matches[0]+5]=body
        words[temps_at+1]=temps+extra_temps
        flag_at=next(at for at,a in ops if a[0]&2047==106)
        words[flag_at]|=globals_
        words[1]=len(words)
        payload=struct.pack('<'+'I'*len(words),*words)
        for i,part in enumerate(parts):
            if part[:4]==b'SHEX': parts[i]=b'SHEX'+struct.pack('<I',len(payload))+payload
            if part[:4]==b'SFI0':
                extra=next(p for p in snippet_parts if p[:4]==b'SFI0')
                flags=int.from_bytes(part[8:],'little')|int.from_bytes(extra[8:],'little')
                parts[i]=part[:8]+flags.to_bytes(len(part)-8,'little')
        offsets,position=[],32+4*len(parts)
        for part in parts: offsets.append(position);position+=len(part)
        result=bytearray(data[:32]+struct.pack('<'+'I'*len(parts),*offsets)+b''.join(parts))
        struct.pack_into('<I',result,24,len(result))
        path=args.output/(layout['name']+'.dxbc');path.write_bytes(result)
        helper=Path(__file__).resolve().parents[1]/'out/build/RelWithDebInfo/aot_dxbc_checksum.exe'
        checksum=subprocess.check_output([str(helper),str(path)],text=True,timeout=5).strip()
        if len(checksum)!=32: raise ValueError('Unexpected checksum result')
        result[4:20]=struct.pack('<4I',*(int(checksum[i:i+8],16) for i in range(0,32,8)))
        path.write_bytes(result)
        report['variants'].append({'file':path.name,'sha256':hashlib.sha256(result).hexdigest(),
            'original':str(source.resolve()),'original_sha256':sha,'layout':layout,'extra_temps':extra_temps})
    (args.output/'variants.json').write_text(json.dumps(report,indent=2)+'\n')
    print(args.output)


if __name__=='__main__': main()
