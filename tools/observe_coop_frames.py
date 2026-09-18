"""Read native co-op simulation counters over time; never write game memory.

Simulation progress is not rendered FPS or input-to-photon latency.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
from pathlib import Path
import re
import struct
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=15)
    args=parser.parse_args()
    if args.output.exists() or not 2<=args.seconds<=60: parser.error('Use a new output and 2..60 seconds')
    locator=json.loads((args.probe/'running.json').read_text())
    match=re.search(r'Guest memory arena mapped: virtual base (0x[0-9A-Fa-f]+)',
                    (args.probe/'runtime.log').read_text(errors='replace'))
    if not match: raise RuntimeError('Missing memory mapping')
    base=int(match[1],16)
    root=Path(__file__).resolve().parents[1]
    expected=(root/'out/build/RelWithDebInfo/army_of_two.exe').resolve()
    kernel=c.WinDLL('kernel32',use_last_error=True)
    def api(name,result,*params):
        fn=getattr(kernel,name);fn.restype=result;fn.argtypes=params;return fn
    op=api('OpenProcess',w.HANDLE,w.DWORD,w.BOOL,w.DWORD)
    close=api('CloseHandle',w.BOOL,w.HANDLE)
    query=api('QueryFullProcessImageNameW',w.BOOL,w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD))
    rpm=api('ReadProcessMemory',w.BOOL,w.HANDLE,c.c_void_p,c.c_void_p,c.c_size_t,c.POINTER(c.c_size_t))
    handle=op(0x1000|0x10,False,locator['pid'])
    if not handle: raise c.WinError(c.get_last_error())
    samples=[]
    try:
        image=c.create_unicode_buffer(32768);length=w.DWORD(32768)
        if not query(handle,0,image,c.byref(length)) or Path(image.value).resolve()!=expected:
            raise RuntimeError('PID is not this project executable')
        def read(address,size):
            if not 0<address<0x100000000-size or not 0<size<=0x1000: raise ValueError('Invalid read')
            buffer=c.create_string_buffer(size);received=c.c_size_t()
            if not rpm(handle,base+address,buffer,size,c.byref(received)) or received.value!=size:
                raise c.WinError(c.get_last_error())
            return buffer.raw
        if read(0x824340D0,16)!=bytes.fromhex('7d8802a69181fff8fbe1fff09421ffa0'):
            raise RuntimeError('Retail revision mismatch')
        started=time.monotonic();epoch=time.time()
        while True:
            pointer=struct.unpack('>I',read(0x831228C8,4))[0]
            raw=read(pointer,0x13C)
            now=time.monotonic()
            samples.append({'seconds':now-started,'simulation':hex(pointer),
                            'produced':struct.unpack_from('>i',raw,0x5C)[0],
                            'consumed':struct.unpack_from('>i',raw,0x64)[0],
                            'input_duration_ms':struct.unpack_from('>i',raw,0xD4)[0],
                            'buffer_timing_ms':struct.unpack_from('>i',raw,0x104)[0],
                            'consecutive_mismatches':struct.unpack_from('>I',raw,0x134)[0]})
            if now-started>=args.seconds: break
            time.sleep(.1)
        first,last=samples[0],samples[-1];elapsed=last['seconds']-first['seconds']
        if any(s['simulation']!=first['simulation'] for s in samples) or any(
                b['consumed']<a['consumed'] or b['produced']<a['produced'] for a,b in zip(samples,samples[1:])):
            raise RuntimeError('Simulation changed or reset during measurement')
        report={'pid':locator['pid'],'start_unix_seconds':epoch,
                'executable_sha256':hashlib.sha256(expected.read_bytes()).hexdigest(),
                'elapsed_seconds':elapsed,'consumed_per_second':(last['consumed']-first['consumed'])/elapsed,
                'produced_per_second':(last['produced']-first['produced'])/elapsed,
                'max_consecutive_mismatches':max(s['consecutive_mismatches'] for s in samples),
                'samples':samples,'limitation':'Read-only, non-atomic native simulation counts, not rendered FPS or end-to-end latency'}
        args.output.write_text(json.dumps(report,indent=2)+'\n')
        print(f"Native simulation consumption: {report['consumed_per_second']:.2f}/s; max consecutive mismatches: {report['max_consecutive_mismatches']}")
    finally: close(handle)


if __name__=='__main__':main()
