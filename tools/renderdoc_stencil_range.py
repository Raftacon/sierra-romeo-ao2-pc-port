"""Inspect complete source stencil planes for captured eight-pass transfers.

Read-only replay, no replacement shaders or performance measurement. Full-source
value ranges conservatively include texels outside each transfer rectangle.
A narrow range in one capture does not authorize dropping bits in production.
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
source=Path(os.environ['AOT_STENCIL_COSTS']).resolve()
out=Path(os.environ['AOT_STENCIL_OUTPUT']).resolve()
if out.exists():raise ValueError('Require a new output file')
report=dict(complete=False,scope=__doc__,transfers=[])
cap=rd.OpenCaptureFile();ctl=None
try:
    costs=json.loads(source.read_text())
    native=json.loads((root/'probe.json').read_text())
    if native['timed_out'] or native['exit_code_before_cleanup']!=0:raise ValueError('Capture process did not close normally')
    path=Path(json.loads((root/'renderdoc-capture.json').read_text())['captures'][0]['path']).resolve()
    if path.parent!=root:raise ValueError('Foreign capture')
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    groups_path=Path(costs['source']);groups_bytes=groups_path.read_bytes()
    groups=json.loads(groups_bytes)
    if not groups['complete'] or digest.hexdigest()!=groups['capture_sha256']:raise ValueError('Grouping/capture mismatch')
    report.update(capture=str(path),capture_sha256=digest.hexdigest(),groups_sha256=hashlib.sha256(groups_bytes).hexdigest())
    if cap.OpenFile(str(path),'',None)!=rd.ResultCode.Succeeded:raise RuntimeError('Capture open failed')
    status,ctl=cap.OpenCapture(rd.ReplayOptions(),None)
    if status!=rd.ResultCode.Succeeded:raise RuntimeError(str(status))
    textures={str(t.resourceId):t for t in ctl.GetTextures()}
    events=costs['groups']['depth']['events']
    if not events or len(events)%8:raise ValueError('Not complete eight-pass groups')
    records={r['event']:r for r in groups['events']}
    for at in range(0,len(events),8):
        batch=events[at:at+8]
        shader=records[batch[0]]['shaders']['Pixel']
        if any(records[e]['shaders']['Pixel']!=shader for e in batch):raise ValueError('Mixed shader group')
        ctl.SetFrameEvent(batch[0],True)
        pipe=ctl.GetPipelineState();reflection=pipe.GetShaderReflection(rd.ShaderStage.Pixel)
        if hashlib.sha256(bytes(reflection.rawBytes)).hexdigest()!=shader:raise ValueError('Shader mismatch')
        candidates={}
        for access in ctl.GetDescriptorAccess():
            if access.stage!=rd.ShaderStage.Pixel or access.type!=rd.DescriptorType.Image:continue
            region=rd.DescriptorRange()
            region.offset,region.descriptorSize,region.count,region.type=access.byteOffset,access.byteSize,1,access.type
            for desc in ctl.GetDescriptors(access.descriptorStore,[region]):
                candidates[str(desc.resource)]=desc.resource
        if len(candidates)!=1:raise ValueError('Require exactly one transfer source image')
        resource=next(iter(candidates.values()));t=textures[str(resource)]
        packing={'D32S8_TYPELESS':(8,4),'D24S8_TYPELESS':(4,3)}
        if t.format.Name() not in packing or t.depth!=1 or t.arraysize!=1 or t.width*t.height*8>256*1024**2:
            raise ValueError('Unexpected stencil source format/shape: '+t.format.Name())
        stride,stencil_offset=packing[t.format.Name()]
        row=dict(events=batch,source=str(resource),width=t.width,height=t.height,samples=t.msSamp,
                 format=t.format.Name(),sample_ranges=[],cost_ms=sum(records[e]['median_ms'] for e in batch))
        report['transfers'].append(row)
        combined=0
        for sample in range(t.msSamp):
            sub=rd.Subresource();sub.sample=sample
            raw=bytes(ctl.GetTextureData(resource,sub))
            if len(raw)!=t.width*t.height*stride:raise ValueError('Unexpected depth/stencil packing')
            # RenderDoc interleaves D32S8 as float depth then the stencil byte
            # plus three unused bytes, matching its normalized texture export.
            # D24S8 instead has three depth bytes followed by stencil.
            counts=Counter(raw[stencil_offset::stride]);mask=0
            for value in counts:mask|=value
            combined|=mask
            row['sample_ranges'].append(dict(sample=sample,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),
                stencil_or=mask,minimum=min(counts),maximum=max(counts),histogram=dict(sorted(counts.items()))))
        row['source_stencil_or']=combined
        dest_id=pipe.GetDepthTarget().resource
        dest=textures[str(dest_id)]
        if dest.format.Name() not in packing or dest.depth!=1 or dest.arraysize!=1 or dest.width*dest.height*8>256*1024**2:
            raise ValueError('Unexpected destination shape/format')
        dest_stride,dest_offset=packing[dest.format.Name()]
        # Read immediately before the first bit draw and after the eighth.
        # All intervening draws must belong to this transfer batch.
        between=[e['event'] for e in groups['events'] if batch[0]<=e['event']<=batch[-1]]
        if between!=batch:raise ValueError('Transfer batch contains another GPU action')
        before=[]
        ctl.SetFrameEvent(batch[0]-1,True)
        for sample in range(dest.msSamp):
            sub=rd.Subresource();sub.sample=sample
            raw=bytes(ctl.GetTextureData(dest_id,sub))
            if len(raw)!=dest.width*dest.height*dest_stride:raise ValueError('Destination packing mismatch')
            before.append(raw[dest_offset::dest_stride])
        ctl.SetFrameEvent(batch[-1],True)
        row['destination']=dict(resource=str(dest_id),format=dest.format.Name(),width=dest.width,height=dest.height,samples=[])
        changed_or=0
        for sample,old in enumerate(before):
            sub=rd.Subresource();sub.sample=sample
            raw=bytes(ctl.GetTextureData(dest_id,sub))
            if len(raw)!=dest.width*dest.height*dest_stride:raise ValueError('Destination packing mismatch')
            new=raw[dest_offset::dest_stride]
            deltas=Counter(a^b for a,b in zip(old,new));mask=0
            for value in deltas:mask|=value
            changed_or|=mask
            row['destination']['samples'].append(dict(sample=sample,changed_bits_or=mask,
                changed_pixels=sum(n for value,n in deltas.items() if value),xor_histogram=dict(sorted(deltas.items())),
                before_sha256=hashlib.sha256(old).hexdigest(),after_sha256=hashlib.sha256(new).hexdigest()))
        row['destination_changed_bits_or']=changed_or
        out.write_text(json.dumps(report,indent=2)+'\n')
    if ctl.GetFatalErrorStatus()!=rd.ResultCode.Succeeded:raise RuntimeError('Replay fatal error')
    report['complete']=True
except BaseException:
    report['error']=traceback.format_exc()
finally:
    if ctl is not None:ctl.Shutdown()
    cap.Shutdown()
    out.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(dict(complete=report['complete'],transfers=len(report['transfers']),error=report.get('error'))))
sys.exit(0 if report['complete'] else 1)
