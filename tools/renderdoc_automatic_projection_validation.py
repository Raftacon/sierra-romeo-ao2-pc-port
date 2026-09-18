"""Replay a complete frame with every audited projection replacement together.

Checks original-binary controls, restoration, and unchanged non-position vertex
outputs for one representative draw per replaced shader, or every matching draw
with AOT_VALIDATE_ALL_DRAWS=1. Post-VS checks use instance zero. Visual inspection
is still required; a complete report is not whole-campaign parity.
"""
import hashlib
import json
import os
import re
import struct
import sys
import traceback
import renderdoc as rd

def main():
    root=os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    inventory=os.path.join(root,os.environ.get('AOT_INVENTORY_NAME','scene-inventory-001'))
    variants=os.path.join(root,os.environ.get('AOT_VARIANTS_NAME','automatic-variants-001'))
    with open(os.path.join(root,'probe.json')) as f:done=json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup')!=0:raise ValueError('Require normally closed capture')
    with open(os.path.join(inventory,'inventory.json')) as f:scene=json.load(f)
    with open(os.path.join(variants,'variants.json')) as f:patches=json.load(f)['variants']
    if not scene['complete'] or not patches:raise ValueError('Require complete inventory and replacements')
    snapshot_controls=all('snapshot_only_file' in p for p in patches)
    if any('snapshot_only_file' in p for p in patches) and not snapshot_controls:
        raise ValueError('Snapshot controls must cover every replacement in this comparison')
    name=os.environ.get('AOT_AUTOMATIC_VALIDATION_NAME','automatic-validation-001')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name):raise ValueError('Invalid output name')
    out=os.path.join(root,name);os.mkdir(out)
    report={'capture':scene['capture'],'runs':[],'complete':False,
            'all_matching_draws':os.environ.get('AOT_VALIDATE_ALL_DRAWS')=='1',
            'post_vs_instance':0}
    cap,ctl=rd.OpenCaptureFile(),None;replacements=[]
    def release():
        for original,replacement in replacements:ctl.RemoveReplacement(original);ctl.FreeTargetResource(replacement)
        replacements.clear()
    try:
        if cap.OpenFile(scene['capture'],'',None)!=rd.ResultCode.Succeeded:raise RuntimeError('Open failed')
        status,ctl=cap.OpenCapture(rd.ReplayOptions(),None)
        if status!=rd.ResultCode.Succeeded:raise RuntimeError(str(status))
        used={t['resource'] for t in scene['textures']}
        targets=[t for t in ctl.GetTextures() if str(t.resourceId) in used and t.width==1920 and t.height==1080 and t.format.Name()=='R10G10B10A2_UNORM']
        if len(targets)!=2:raise ValueError('Expected two spatial output targets')
        originals={};events={}
        for p in patches:
            sha=p['original_sha256'];rows=[r for r in scene['draws'] if r['shaders'].get('Vertex')==sha]
            if not rows:raise ValueError('Missing shader use')
            event=rows[0]['event'];ctl.SetFrameEvent(event,True);pipe=ctl.GetPipelineState()
            raw=bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
            if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Unexpected captured shader')
            with open(os.path.join(variants,p['file']),'rb') as f:patched=f.read()
            if hashlib.sha256(patched).hexdigest()!=p['sha256']:raise ValueError('Unexpected replacement')
            snapshot=None
            if snapshot_controls:
                with open(os.path.join(variants,p['snapshot_only_file']),'rb') as f:snapshot=f.read()
                if hashlib.sha256(snapshot).hexdigest()!=p['snapshot_only_sha256']:raise ValueError('Unexpected snapshot control')
            original=pipe.GetShader(rd.ShaderStage.Vertex)
            # A binary hash may label more than one GPU resource. Never claim
            # every matching draw was replaced if only one resource was changed.
            for row in rows:
                ctl.SetFrameEvent(row['event'],False)
                if ctl.GetPipelineState().GetShader(rd.ShaderStage.Vertex)!=original:
                    raise ValueError('Matching shader bytes use multiple GPU resources')
            originals[sha]=(original,raw,patched,snapshot)
            events[sha]=[r['event'] for r in rows] if os.environ.get('AOT_VALIDATE_ALL_DRAWS')=='1' else [event]
        def geometry(event):
            ctl.SetFrameEvent(event,True);m=ctl.GetPostVSData(0,0,rd.MeshDataStage.VSOut)
            if m.indexByteStride not in (0,2,4) or not 0<m.numIndices<=131072 or not 16<=m.vertexByteStride<=512:
                raise ValueError('Mesh bounds at event '+str(event)+': '+repr(rd.DumpObject(m)))
            if m.indexByteStride:
                ib=bytes(ctl.GetBufferData(m.indexResourceId,m.indexByteOffset,m.numIndices*m.indexByteStride))
                refs=sorted({i+m.baseVertex for i in struct.unpack('<'+str(m.numIndices)+('H' if m.indexByteStride==2 else 'I'),ib)})
            else:
                if m.indexResourceId!=rd.ResourceId.Null() or m.baseVertex!=0:
                    raise ValueError('Unexpected non-indexed post-VS layout')
                ib=b'';refs=list(range(m.numIndices))
            lo,hi=refs[0],refs[-1]
            if not 0<=lo<=hi<200000:raise ValueError('Index bounds')
            raw=bytes(ctl.GetBufferData(m.vertexResourceId,m.vertexByteOffset+lo*m.vertexByteStride,(hi-lo+1)*m.vertexByteStride))
            if len(raw)!=(hi-lo+1)*m.vertexByteStride:raise ValueError('Short vertex read')
            pos=b''.join(raw[(i-lo)*m.vertexByteStride:(i-lo)*m.vertexByteStride+16] for i in refs)
            other=b''.join(raw[(i-lo)*m.vertexByteStride+16:(i-lo+1)*m.vertexByteStride] for i in refs)
            return ib,refs,pos,other
        baseline={};frames={};end=max(r['event'] for r in scene['draws'])
        modes=('original','control')+(('snapshot-only',) if snapshot_controls else ())+('automatic','restored')
        for mode in modes:
            release()
            if mode in ('control','snapshot-only','automatic'):
                for sha,(original,raw,patched,snapshot) in originals.items():
                    data=raw if mode=='control' else snapshot if mode=='snapshot-only' else patched
                    replacement,errors=ctl.BuildTargetShader('main',rd.ShaderEncoding.DXBC,data,rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
                    if replacement==rd.ResourceId.Null() or errors:raise RuntimeError('Shader build: '+errors)
                    replacements.append((original,replacement));ctl.ReplaceResource(original,replacement)
            run={'mode':mode,'geometry':[],'frames':[]};report['runs'].append(run)
            for sha,event in ((sha,event) for sha,shader_events in events.items() for event in shader_events):
                g=geometry(event)
                if mode=='original':baseline[sha,event]=g
                b=baseline[sha,event]
                if (g[0],g[1],g[3])!=(b[0],b[1],b[3]) or mode!='automatic' and g[2]!=b[2]:
                    differences=[]
                    for offset in range(0,min(len(g[3]),len(b[3])),4):
                        before,after=b[3][offset:offset+4],g[3][offset:offset+4]
                        if before!=after:
                            differences.append({'byte_offset':offset,'before_bits':before.hex(),'after_bits':after.hex(),
                                'before_float':repr(struct.unpack('<f',before)[0]),'after_float':repr(struct.unpack('<f',after)[0])})
                    report['vertex_failure']={'event':event,'shader':sha,'mode':mode,
                        'indices_equal':g[0]==b[0] and g[1]==b[1],
                        'position_equal':g[2]==b[2],'material_changed_components':len(differences),
                        'first_differences':differences[:32]}
                    for label,data in (('before',b),('after',g)):
                        with open(os.path.join(out,label+'-positions.bin'),'wb') as f:f.write(data[2])
                        with open(os.path.join(out,label+'-materials.bin'),'wb') as f:f.write(data[3])
                    raise ValueError('Vertex/control outputs changed at event '+str(event))
                run['geometry'].append({'event':event,'shader':sha,'vertices':len(g[1]),'indexed':bool(g[0]),'positions_sha256':hashlib.sha256(g[2]).hexdigest(),'material_sha256':hashlib.sha256(g[3]).hexdigest()})
            ctl.SetFrameEvent(end,True)
            for t in targets:
                raw=bytes(ctl.GetTextureData(t.resourceId,rd.Subresource()));key=str(t.resourceId)
                if mode=='original':frames[key]=raw
                if mode in ('control','snapshot-only','restored') and frames[key]!=raw:raise ValueError('Frame control/restoration changed')
                filename=mode+'-'+re.sub(r'[^0-9]','',key)+'.png'
                save=rd.TextureSave();save.resourceId=t.resourceId;save.destType=rd.FileType.PNG
                if ctl.SaveTexture(save,os.path.join(out,filename))!=rd.ResultCode.Succeeded:raise RuntimeError('Save failed')
                run['frames'].append({'resource':key,'image':filename,'sha256':hashlib.sha256(raw).hexdigest(),'changed_bytes':sum(a!=b for a,b in zip(raw,frames[key]))})
        if ctl.GetFatalErrorStatus()!=rd.ResultCode.Succeeded:raise RuntimeError('GPU replay failed')
        report['complete']=True
    except BaseException:report['error']=traceback.format_exc()
    finally:
        if ctl is not None:release();ctl.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out,'validation.json'),'w') as f:json.dump(report,f,indent=2)

try:main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'],'automatic-validation-error.txt'),'w') as f:f.write(traceback.format_exc())
sys.exit(0)
