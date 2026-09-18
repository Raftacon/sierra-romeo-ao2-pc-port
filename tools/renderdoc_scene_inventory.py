"""Inventory an entire closed frame, including preview targets and pipeline names.

RenderDoc Python: AOT_RENDERDOC_PROBE and optional AOT_INVENTORY_NAME.
No shader replacements or live-game access.
"""
import hashlib
import json
import os
import re
import sys
import traceback
import renderdoc as rd

def main():
    root=os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    with open(os.path.join(root,'probe.json')) as f: done=json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup')!=0: raise ValueError('Require normally closed capture')
    with open(os.path.join(root,'renderdoc-capture.json')) as f: path=json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path))!=os.path.normcase(root): raise ValueError('Foreign capture')
    name=os.environ.get('AOT_INVENTORY_NAME','scene-inventory-001')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name): raise ValueError('Invalid output name')
    out=os.path.join(root,name);os.mkdir(out)
    report={'capture':path,'draws':[],'shaders':{},'textures':[],'complete':False}
    cap,ctl=rd.OpenCaptureFile(),None
    try:
        if cap.OpenFile(path,'',None)!=rd.ResultCode.Succeeded: raise RuntimeError('Open failed')
        status,ctl=cap.OpenCapture(rd.ReplayOptions(),None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        names={str(r.resourceId):r.name for r in ctl.GetResources()}
        actions=[]
        def visit(nodes):
            for a in nodes:
                if a.flags & rd.ActionFlags.Drawcall: actions.append(a)
                visit(a.children)
        visit(ctl.GetRootActions())
        if not 0<len(actions)<=20000: raise ValueError('Unbounded draw list')
        saved=set()
        for a in actions:
            ctl.SetFrameEvent(a.eventId,False);pipe=ctl.GetPipelineState()
            row={'event':a.eventId,'indices':a.numIndices,'instances':a.numInstances,
                 'index_offset':a.indexOffset,'base_vertex':a.baseVertex,
                 'pipeline':names.get(str(pipe.GetGraphicsPipelineObject()),''),
                 'viewport':rd.DumpObject(pipe.GetViewport(0)),
                 'outputs':[str(r) for r in a.outputs],'depth':str(a.depthOut),
                 'ib':rd.DumpObject(pipe.GetIBuffer()),'shaders':{},
                 'depth_state':rd.DumpObject(ctl.GetD3D12PipelineState().outputMerger.depthStencilState)}
            report['draws'].append(row)
            for stage in (rd.ShaderStage.Vertex,rd.ShaderStage.Pixel):
                reflection=pipe.GetShaderReflection(stage)
                if reflection is None: continue
                shader=str(pipe.GetShader(stage))
                if shader not in report['shaders']:
                    raw=bytes(reflection.rawBytes);sha=hashlib.sha256(raw).hexdigest()
                    report['shaders'][shader]={'sha256':sha,'stage':stage.name}
                    if sha not in saved:
                        with open(os.path.join(out,sha+'.dxbc'),'wb') as f:f.write(raw)
                        with open(os.path.join(out,sha+'.txt'),'w') as f:f.write(ctl.DisassembleShader(pipe.GetGraphicsPipelineObject(),reflection,''))
                        saved.add(sha)
                row['shaders'][stage.name]=report['shaders'][shader]['sha256']
        end=max(a.eventId for a in actions);ctl.SetFrameEvent(end,True)
        used={r for a in actions for r in a.outputs if r!=rd.ResourceId.Null()}
        for t in ctl.GetTextures():
            if t.resourceId not in used: continue
            row={'resource':str(t.resourceId),'width':t.width,'height':t.height,
                 'samples':t.msSamp,'format':t.format.Name()}
            report['textures'].append(row)
            if t.width<=4096 and t.height<=4096:
                filename=re.sub(r'[^0-9]','',str(t.resourceId))+'.png'
                save=rd.TextureSave();save.resourceId=t.resourceId;save.destType=rd.FileType.PNG
                if ctl.SaveTexture(save,os.path.join(out,filename))!=rd.ResultCode.Succeeded: raise RuntimeError('Save texture failed')
                row['image']=filename
        if ctl.GetFatalErrorStatus()!=rd.ResultCode.Succeeded: raise RuntimeError('GPU replay failed')
        report['complete']=True
    except BaseException:report['error']=traceback.format_exc()
    finally:
        if ctl is not None:ctl.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out,'inventory.json'),'w') as f:json.dump(report,f,indent=2)

try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'],'inventory-error.txt'),'w') as f:f.write(traceback.format_exc())
sys.exit(0)
