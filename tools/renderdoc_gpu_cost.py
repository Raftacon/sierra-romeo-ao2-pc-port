"""Rank GPU event durations in a normally closed native capture, without edits.

Run through RenderDoc Python with AOT_RENDERDOC_PROBE. Three counter passes
retain per-event variation; these replay measurements are not native frame time.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import traceback
import renderdoc as rd


root=Path(os.environ['AOT_RENDERDOC_PROBE']).resolve()
out=Path(os.environ.get('AOT_GPU_COST_OUTPUT',str(root/'gpu-cost-001'))).resolve()
out.mkdir(parents=True,exist_ok=False)
report={'complete':False,'limits':__doc__}
cap=rd.OpenCaptureFile();ctl=None
try:
    native=json.loads((root/'probe.json').read_text())
    scenario=json.loads((root/'checkpoint-gpu.json').read_text())
    travel=json.loads((root/'travel.json').read_text())
    captures=json.loads((root/'renderdoc-capture.json').read_text())
    if (native['timed_out'] or native['exit_code_before_cleanup']!=0 or not scenario['complete']
        or not travel['source_profile_unchanged'] or not travel['retail_checkpoints_unchanged']
        or captures.get('error') or len(captures['captures'])!=1):
        raise ValueError('Require completed native single capture and source preservation')
    path=Path(captures['captures'][0]['path']).resolve()
    if path.parent!=root:raise ValueError('Capture outside selected probe')
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
    report.update(capture=str(path),capture_sha256=digest.hexdigest(),
                  executable_sha256=native['executable_sha256'],gpu_plugin=native['gpu_plugin'])
    if cap.OpenFile(str(path),'',None)!=rd.ResultCode.Succeeded:raise RuntimeError('Capture open failed')
    status,ctl=cap.OpenCapture(rd.ReplayOptions(),None)
    if status!=rd.ResultCode.Succeeded:raise RuntimeError(str(status))
    counter=rd.GPUCounter.EventGPUDuration
    if counter not in ctl.EnumerateCounters():raise ValueError('GPU event duration counter unavailable')
    desc=ctl.DescribeCounter(counter)
    report['counter']={'name':desc.name,'description':desc.description,'unit':str(desc.unit),
                       'result_type':str(desc.resultType),'bytes':desc.resultByteWidth}
    if desc.unit!=rd.CounterUnit.Seconds or desc.resultByteWidth!=8:
        raise ValueError('Unexpected GPU duration counter representation')
    passes=[]
    for repeat in range(3):
        rows={r.eventId:float(r.value.d)*1000 for r in ctl.FetchCounters([counter])}
        if not rows or max(rows.values())<=0 or any(not math.isfinite(v) or v<0 for v in rows.values()):
            raise ValueError('Missing or invalid counter duration')
        if passes and rows.keys()!=passes[0].keys():raise ValueError('Counter event scope changed')
        passes.append(rows)
        (out/('counter-pass-%d.json'%repeat)).write_text(json.dumps(rows)+'\n')
    report['sum_event_gpu_ms']=[sum(p.values()) for p in passes]
    report['event_count']=len(passes[0])
    actions={}
    def visit(nodes):
        for a in nodes:actions[a.eventId]=a;visit(a.children)
    visit(ctl.GetRootActions())
    timings=[]
    for event in passes[0]:
        samples=[p[event] for p in passes]
        timings.append({'event':event,'median_ms':statistics.median(samples),'samples_ms':samples})
    timings.sort(key=lambda r:r['median_ms'],reverse=True)
    report['ranked_events']=timings
    textures={str(t.resourceId):t for t in ctl.GetTextures()}
    structured=ctl.GetStructuredFile();shaders={};top=[]
    for row in timings[:50]:
        event=row['event'];action=actions.get(event)
        if action is None:raise ValueError('Counter event has no action')
        item=dict(row,name=action.GetName(structured),flags=str(action.flags),
                  indices=action.numIndices,instances=action.numInstances,outputs=[])
        for target in list(action.outputs)+[action.depthOut]:
            t=textures.get(str(target))
            if t is not None:item['outputs'].append({'id':str(target),'width':t.width,'height':t.height,'format':t.format.Name()})
        if action.flags & (rd.ActionFlags.Drawcall|rd.ActionFlags.Dispatch):
            ctl.SetFrameEvent(event,False);pipe=ctl.GetPipelineState();item['shaders']={}
            stages=(rd.ShaderStage.Compute,) if action.flags & rd.ActionFlags.Dispatch else (rd.ShaderStage.Vertex,rd.ShaderStage.Pixel)
            for stage in stages:
                sid=str(pipe.GetShader(stage));ref=pipe.GetShaderReflection(stage)
                if ref is None:continue
                if sid not in shaders:
                    raw=bytes(ref.rawBytes);sha=hashlib.sha256(raw).hexdigest()
                    (out/(sha+'.dxbc')).write_bytes(raw)
                    shaders[sid]={'sha256':sha,'bytes':len(raw)}
                item['shaders'][str(stage)]=dict(id=sid,**shaders[sid])
        top.append(item)
    report['top_events']=top
    if ctl.GetFatalErrorStatus()!=rd.ResultCode.Succeeded:raise RuntimeError('Fatal GPU replay error')
    report['complete']=True
except BaseException:
    report['error']=traceback.format_exc()
finally:
    if ctl is not None:ctl.Shutdown()
    cap.Shutdown()
    (out/'gpu-cost.json').write_text(json.dumps(report,indent=2)+'\n')
sys.exit(0 if report['complete'] else 1)
