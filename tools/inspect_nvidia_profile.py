"""Read NVIDIA frame-limit settings without modifying or saving driver profiles.

ABI and IDs: NVIDIA/nvapi nvapi.h, nvapi_interface.h, NvApiDriverSettings.h.
Only Initialize, Unload and read/session-management entry points are bound.
"""
import argparse
import ctypes as c
import json
from pathlib import Path


class Value(c.Union):
    _pack_=4
    _fields_=[('dword',c.c_uint32),('storage',c.c_ubyte*4100)]


class Setting(c.Structure):
    _pack_=4
    _fields_=[('version',c.c_uint32),('name',c.c_uint16*2048),('id',c.c_uint32),
              ('kind',c.c_uint32),('location',c.c_uint32),('current_predefined',c.c_uint32),
              ('predefined_valid',c.c_uint32),('predefined',Value),('current',Value)]


class Application(c.Structure):
    _fields_=[('version',c.c_uint32),('predefined',c.c_uint32),
              ('name',c.c_uint16*2048),('friendly',c.c_uint16*2048),
              ('launcher',c.c_uint16*2048),('file',c.c_uint16*2048),
              ('flags',c.c_uint32),('command_line',c.c_uint16*2048)]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--exe',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists() or not a.exe.is_file():p.error('Require an existing executable and new output')
    assert c.sizeof(Setting)==12320 and c.sizeof(Application)==20492
    dll=c.WinDLL('nvapi64.dll')
    query=dll.nvapi_QueryInterface;query.restype=c.c_void_p;query.argtypes=[c.c_uint32]
    def function(identity,*types):
        address=query(identity)
        if not address:raise RuntimeError('Missing NVAPI entry '+hex(identity))
        return c.CFUNCTYPE(c.c_int,*types)(address)
    def check(status):
        if status:raise RuntimeError('NVAPI status '+str(status))
    init=function(0x0150e828);unload=function(0xd22bdd7e)
    create=function(0x0694d52e,c.POINTER(c.c_void_p))
    destroy=function(0xdad9cff8,c.c_void_p)
    load=function(0x375dbd6b,c.c_void_p)
    base=function(0xda8466a0,c.c_void_p,c.POINTER(c.c_void_p))
    find=function(0xeee566b2,c.c_void_p,c.c_wchar_p,c.POINTER(c.c_void_p),c.POINTER(Application))
    get=function(0x73bf8338,c.c_void_p,c.c_void_p,c.c_uint32,c.POINTER(Setting))
    check(init());session=c.c_void_p()
    report={'exe':str(a.exe.resolve()),'read_only':True,'profiles':[]}
    try:
        check(create(c.byref(session)));check(load(session))
        for label in ('base',a.exe.name,str(a.exe.resolve())):
            profile=c.c_void_p()
            if label=='base':status=base(session,c.byref(profile))
            else:
                app=Application();app.version=c.sizeof(app)|(4<<16)
                status=find(session,label,c.byref(profile),c.byref(app))
            item={'lookup':label,'status':status,'settings':[]};report['profiles'].append(item)
            if status:continue
            for key,identity in (('frame_limit',0x10835002),('background_limit',0x10835016),
                                 ('background_timeout',0x10835017),('vsync',0x00a879cf)):
                setting=Setting();setting.version=c.sizeof(setting)|(1<<16)
                status=get(session,profile,identity,c.byref(setting))
                value={'key':key,'status':status};item['settings'].append(value)
                if status:continue
                if setting.kind!=0:raise RuntimeError('Expected DWORD setting')
                value.update(current=setting.current.dword,location=setting.location,
                             predefined=bool(setting.current_predefined))
    finally:
        if session.value:check(destroy(session))
        check(unload())
    a.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
