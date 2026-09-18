"""Inventory captured D3D12 query API calls, without changing shaders or counters.

Run with RenderDoc Python after all native performance probes have stopped.
AOT_RENDERDOC_PROBE selects an existing capture directory; AOT_QUERY_OUTPUT
selects a new output JSON. A captured frame cannot establish all-game usage.
"""
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback
import renderdoc as rd

root=Path(os.environ['AOT_RENDERDOC_PROBE']).resolve()
out=Path(os.environ['AOT_QUERY_OUTPUT']).resolve()
if out.exists(): raise RuntimeError('Use a new output file')
report=dict(complete=False,scope=__doc__)
cap=rd.OpenCaptureFile(); controller=None
try:
    native=json.loads((root/'probe.json').read_text())
    capture=json.loads((root/'renderdoc-capture.json').read_text())
    if native['timed_out'] or native['exit_code_before_cleanup']!=0 or len(capture.get('captures',[]))!=1:
        raise RuntimeError('Require normally completed single capture')
    path=Path(capture['captures'][0]['path']).resolve()
    if path.parent!=root: raise RuntimeError('Capture outside selected fixture')
    sha=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): sha.update(block)
    report.update(capture=str(path),capture_sha256=sha.hexdigest())
    if cap.OpenFile(str(path),'',None)!=rd.ResultCode.Succeeded: raise RuntimeError('Capture open failed')
    result,controller=cap.OpenCapture(rd.ReplayOptions(),None)
    if result!=rd.ResultCode.Succeeded: raise RuntimeError(str(result))
    structured=controller.GetStructuredFile()
    frame_events={}
    def visit(actions):
        for action in actions:
            for event in action.events: frame_events[event.chunkIndex]=event.eventId
            visit(action.children)
    visit(controller.GetRootActions())
    def fields(obj,depth=0):
        record=dict(name=obj.name,type=obj.type.name,basic=str(obj.type.basetype),value=str(rd.DumpObject(obj)))
        if obj.NumChildren() and depth<4:
            record['children']=[fields(obj.GetChild(i),depth+1) for i in range(min(obj.NumChildren(),32))]
        return record
    calls=[]; counts=Counter()
    for index,chunk in enumerate(structured.chunks):
        if 'Query' not in chunk.name: continue
        counts[chunk.name]+=1
        calls.append(dict(chunk=index,event=frame_events.get(index),name=chunk.name,
                          parameters=fields(chunk)))
    if controller.GetFatalErrorStatus()!=rd.ResultCode.Succeeded:
        raise RuntimeError('Fatal replay error')
    report.update(structured_chunks=len(structured.chunks),frame_api_events=len(frame_events),
        query_calls=calls,query_call_counts=dict(counts),
        frame_query_call_counts=dict(Counter(call['name'] for call in calls if call['event'] is not None)),
        complete=True)
except BaseException:
    report['error']=traceback.format_exc()
finally:
    if controller is not None: controller.Shutdown()
    cap.Shutdown()
    out.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='query_calls'},indent=2))
sys.exit(0 if report['complete'] else 1)
