"""Read-only, bounded transition watcher for a specific local native installation."""
import argparse, ctypes as c, hashlib, json, struct, subprocess, time
from ctypes import wintypes as w
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--installation',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--seconds',type=int,default=900)
a=p.parse_args()
if not 10<=a.seconds<=1800:p.error('seconds must be 10..1800')
root=a.installation.resolve();expected=root/'out/build/RelWithDebInfo/army_of_two.exe'
a.output.mkdir(parents=True,exist_ok=False)
k=c.WinDLL('kernel32',use_last_error=True)
def api(n,r,*args):
 f=getattr(k,n);f.restype=r;f.argtypes=args;return f
op=api('OpenProcess',w.HANDLE,w.DWORD,w.BOOL,w.DWORD)
close=api('CloseHandle',w.BOOL,w.HANDLE)
query=api('QueryFullProcessImageNameW',w.BOOL,w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD))
rpm=api('ReadProcessMemory',w.BOOL,w.HANDLE,c.c_void_p,c.c_void_p,c.c_size_t,c.POINTER(c.c_size_t))
exitcode=api('GetExitCodeProcess',w.BOOL,w.HANDLE,c.POINTER(w.DWORD))
ps=c.WinDLL('psapi');ps.EnumProcesses.argtypes=[c.POINTER(w.DWORD),w.DWORD,c.POINTER(w.DWORD)]
def locate():
 ids=(w.DWORD*8192)();size=w.DWORD()
 if not ps.EnumProcesses(ids,c.sizeof(ids),c.byref(size)):return None
 for pid in ids[:size.value//4]:
  h=op(0x1010,False,pid)
  if not h:continue
  path=c.create_unicode_buffer(32768);length=w.DWORD(len(path))
  if query(h,0,path,c.byref(length)) and Path(path.value).resolve()==expected:return pid,h
  close(h)
 return None
end=time.monotonic()+a.seconds
print('Waiting for',expected,flush=True)
found=None
while time.monotonic()<end and found is None:
 found=locate()
 if found is None:time.sleep(2)
if found is None:raise SystemExit('No matching game launched before deadline')
pid,h=found
(a.output/'running.json').write_text(json.dumps({'pid':pid,'executable':str(expected),'sha256':hashlib.sha256(expected.read_bytes()).hexdigest()},indent=2))
print('Watching PID',pid,flush=True)
base=0x100000000
budget=0
def read(address,n):
 global budget
 if not 0<n<=4*1024*1024 or not 0<=address<=0xFFFFFFFF-n:raise ValueError('Invalid read range')
 budget+=n
 if budget>24*1024*1024:raise ValueError('Snapshot read budget exceeded')
 b=c.create_string_buffer(n);got=c.c_size_t()
 if not rpm(h,base+address,b,n,c.byref(got)) or got.value!=n:raise ValueError('Unreadable guest span')
 return b.raw
def u(address):return struct.unpack('>I',read(address,4))[0]
def snapshot():
 global budget
 budget=0
 if read(0x824340D0,16).hex()!='7d8802a69181fff8fbe1fff09421ffa0':raise ValueError('Retail signature not ready or unsupported')
 names,nc=struct.unpack('>II',read(0x83101004,8));objects,oc=struct.unpack('>II',read(0x8311471C,8))
 if not 0<nc<=500000 or not 0<oc<=500000:raise ValueError('Invalid table counts')
 np=struct.unpack('>'+str(nc)+'I',read(names,nc*4));ops=struct.unpack('>'+str(oc)+'I',read(objects,oc*4));cache={}
 def name(i):
  if not 0<=i<nc:return '<invalid>'
  if i not in cache:cache[i]=read(np[i]+16,128).split(b'\0')[0].decode('ascii',errors='replace')
  return cache[i]
 def label(ptr):
  if not ptr:return None
  words=struct.unpack('>14I',read(ptr,56));n=name(words[11]);num=words[12]
  return n+('_'+str(num-1) if num else '')
 def path(ptr):
  parts=[];seen=set()
  while ptr and ptr not in seen and len(parts)<12:
   seen.add(ptr);parts.append(label(ptr));ptr=u(ptr+40)
  return '.'.join(reversed(parts))
 result={'world':hex(u(0x83122588)),'streaming':[],'checkpoints':[],'active_sequences':[]}
 for ptr in ops:
  if not ptr:continue
  try:
   b=struct.unpack('>14I',read(ptr,56));cls=name(u(b[13]+44)) if b[13] else '';n=name(b[11])
   if n.startswith('Default__'):continue
   if cls in ('LevelStreamingKismet','LevelStreamingPersistent','LevelStreamingDistance'):
    result['streaming'].append({'object':hex(ptr),'package':name(u(ptr+60)),'level':hex(u(ptr+68)),'flags':hex(u(ptr+96))})
   elif cls=='AO2CheckpointManager':
    result['checkpoints'].append({'object':hex(ptr),'current':path(u(ptr+0x1c4))})
   elif cls.startswith(('SeqAct_','AO2SeqAct_')) and u(ptr+0x84)&0x80000000:
    if len(result['active_sequences'])<128:result['active_sequences'].append(path(ptr))
  except ValueError:continue
 rawptr,size,capacity=struct.unpack('>III',read(0x830AB2E4,12))
 if 0<size<=capacity<=4*1024*1024:
  raw=read(rawptr,size);result['checkpoint_bytes_sha256']=hashlib.sha256(raw).hexdigest()
 result['object_count']=oc
 return result
last=None;count=0
try:
 with (a.output/'states.jsonl').open('w') as out:
  while time.monotonic()<end:
   code=w.DWORD()
   if not exitcode(h,c.byref(code)) or code.value!=259:break
   try:state=snapshot()
   except (ValueError,OSError) as e:state={'error':str(e)}
   encoded=json.dumps(state,sort_keys=True)
   if encoded!=last:
    out.write(json.dumps({'unix_seconds':time.time(),'state':state})+'\n');out.flush();last=encoded;count+=1
    print('State',count,'objects',state.get('object_count'),'checkpoints',state.get('checkpoints'),flush=True)
   time.sleep(2)
finally:
 close(h)
 (a.output/'completed.json').write_text(json.dumps({'states':count,'finished_unix_seconds':time.time(),'read_only':True},indent=2))
print('Watcher finished; game left running if still active.',flush=True)
