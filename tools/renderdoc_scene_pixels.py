"""Inspect bounded scene pixels, including shaders of depth-rejected fragments.

RenderDoc Python: AOT_RENDERDOC_PROBE, AOT_SCENE_PIXEL_QUERY (JSON with name
and 1-8 [x,y] pairs in the 1280x720 scene). Discovers unique main HDR targets
by dimensions/format; records their identities. Never runs shader simulation.
"""
import hashlib
import json
import os
import re
import sys
import traceback
import renderdoc as rd


def main():
    root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    with open(os.path.join(root,'probe.json')) as f: done = json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0: raise ValueError('Require normally closed capture')
    with open(os.path.join(root,'renderdoc-capture.json')) as f: path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root): raise ValueError('Foreign capture')
    with open(os.environ['AOT_SCENE_PIXEL_QUERY']) as f: query = json.load(f)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',query['name']): raise ValueError('Invalid output name')
    if not 1 <= len(query['pixels']) <= 8 or any(len(p)!=2 or any(type(v)!=int for v in p) or not 0<=p[0]<1280 or not 0<=p[1]<720 for p in query['pixels']): raise ValueError('Invalid scene coordinates')
    out = os.path.join(root,query['name']); os.mkdir(out)
    report = {'capture':path,'query':query,'textures':[],'draws':{},'pixels':[],'complete':False}
    cap,ctl = rd.OpenCaptureFile(),None
    try:
        if cap.OpenFile(path,'',None)!=rd.ResultCode.Succeeded: raise RuntimeError('Capture open failed')
        status,ctl = cap.OpenCapture(rd.ReplayOptions(),None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        def last(nodes): return max([0]+[max(a.eventId,last(a.children)) for a in nodes])
        end = last(ctl.GetRootActions()); report['last_event'] = end
        tex = list(ctl.GetTextures())
        targets = []
        for label,height,samples,fmt in (('msaa',1024,2,'R16G16B16A16_FLOAT'),('hdr',2048,1,'R16G16B16A16_FLOAT'),('display',2048,1,'R8G8B8A8_UNORM')):
            matches = [t for t in tex if (t.width,t.height,t.msSamp,t.format.Name())==(1280,height,samples,fmt) and t.creationFlags & rd.TextureCategory.ColorTarget]
            if len(matches)!=1: raise ValueError('Ambiguous '+label+' target: '+str([str(t.resourceId) for t in matches]))
            t = matches[0]; targets.append((label,t)); report['textures'].append({'label':label,'resource':str(t.resourceId),'samples':samples,'format':fmt,'height':height})
        saved = set()
        for label,t in targets:
            if label!='display':
                for x,y in query['pixels']:
                    for sample in range(t.msSamp):
                        ctl.SetFrameEvent(end,True); sub = rd.Subresource(); sub.sample = sample
                        history = ctl.PixelHistory(t.resourceId,x,y,sub,rd.CompType.Float)
                        pixel = {'target':label,'x':x,'y':y,'sample':sample,'history':[]}; report['pixels'].append(pixel)
                        if len(history)>2000: raise ValueError('Unbounded pixel history')
                        for h in history:
                            pixel['history'].append({'event':h.eventId,'primitive':h.primitiveID,'passed':h.Passed(),'details':rd.DumpObject(h)})
                            if h.eventId in report['draws']: continue
                            ctl.SetFrameEvent(h.eventId,True); pipe = ctl.GetPipelineState()
                            row = {'event':h.eventId,'viewport':rd.DumpObject(pipe.GetViewport(0)),
                                   'ib':rd.DumpObject(pipe.GetIBuffer()),'shaders':{},
                                   'depth_state':rd.DumpObject(ctl.GetD3D12PipelineState().outputMerger.depthStencilState)}
                            report['draws'][h.eventId] = row
                            for stage in (rd.ShaderStage.Vertex,rd.ShaderStage.Pixel):
                                reflection = pipe.GetShaderReflection(stage)
                                if reflection is None: continue
                                raw = bytes(reflection.rawBytes); sha = hashlib.sha256(raw).hexdigest()
                                row['shaders'][stage.name] = sha
                                if sha not in saved:
                                    with open(os.path.join(out,sha+'.dxbc'),'wb') as f: f.write(raw)
                                    with open(os.path.join(out,sha+'.txt'),'w') as f: f.write(ctl.DisassembleShader(pipe.GetGraphicsPipelineObject(),reflection,''))
                                    saved.add(sha)
            ctl.SetFrameEvent(end,True); save = rd.TextureSave(); save.resourceId = t.resourceId; save.destType = rd.FileType.PNG
            if ctl.SaveTexture(save,os.path.join(out,label+'.png'))!=rd.ResultCode.Succeeded: raise RuntimeError('Image save failed')
        if ctl.GetFatalErrorStatus()!=rd.ResultCode.Succeeded: raise RuntimeError('Replay GPU error')
        report['complete'] = True
    except BaseException: report['error'] = traceback.format_exc()
    finally:
        if ctl is not None: ctl.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out,'pixels.json'),'w') as f: json.dump(report,f,indent=2)


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'],'scene-pixels-error.txt'),'w') as f: f.write(traceback.format_exc())
sys.exit(0)
