"""Replay paired depth/color precision variants with GPU output controls.

Requires AOT_RENDERDOC_PROBE (closed equipment sequence) and
AOT_PROJECTION_VARIANTS (new directory containing variants.json).
"""
import hashlib
import json
import math
import os
import struct
import sys
import traceback
import renderdoc as rd

GEOMETRY=[(4902,570),(4918,573),(7428,570),(7456,573),
          (18941,570),(18957,573),(21488,570),(21516,573)]
READBACK=[7456,21516,35541,49575]


def main():
    global GEOMETRY, READBACK
    probe=os.environ['AOT_RENDERDOC_PROBE'];out=os.environ['AOT_PROJECTION_VARIANTS']
    crate = os.environ.get('AOT_PROJECTION_CASE') == 'crate'
    if crate:
        GEOMETRY = [(121346,6),(122074,6),(124970,6),(132867,6)]
        with open(os.path.join(probe,'crate-material-color/color.json')) as source:
            color = json.load(source)
        if not color.get('complete') or color.get('error'): raise ValueError('Require completed crate baseline')
        READBACK = [f['event'] for f in color['frames']]
        if len(READBACK) != 24: raise ValueError('Require 24 crate frames')
    report_path=os.path.join(out,'replay.json')
    if os.path.exists(report_path): raise ValueError('Require new replay output')
    with open(os.path.join(probe,'probe.json')) as source: done=json.load(source)
    if done.get('timed_out') or done.get('exit_code_before_cleanup')!=0: raise ValueError('Require closed native probe')
    with open(os.path.join(probe,'renderdoc-capture.json')) as source: path=json.load(source)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path))!=os.path.normcase(os.path.abspath(probe)): raise ValueError('Capture must belong to probe')
    with open(os.path.join(out,'variants.json')) as source: manifest=json.load(source)
    if [v['layout']['name'] for v in manifest['variants']]!=(['color','depth','crate'] if crate else ['color','depth']): raise ValueError('Require paired variants')
    # Check the captured-state assumption against each inventoried corresponding
    # pass, including secondary uses of the color shader in these frames.
    inventory=os.path.join(probe,'crate-corresponding-passes' if crate else 'equipment-passes-002')
    with open(os.path.join(inventory,'passes.json')) as source: passes=json.load(source)
    if passes.get('error') or passes['capture']!=path: raise ValueError('Require matching pass inventory')
    originals={v['original_sha256'] for v in manifest['variants']}
    checked=[]
    for row in passes['draws']:
        if row['shaders']['Vertex']['sha256'] not in originals: continue
        cb=next(b for b in row['vertex_constants'] if b['name']=='xe_system_cbuffer')
        with open(os.path.join(inventory,cb['file']),'rb') as source: raw=source.read()
        if hashlib.sha256(raw).hexdigest()!=cb['sha256'] or struct.unpack_from('<I',raw)[0]&14!=8:
            raise ValueError('Unsupported position flags or changed constant record')
        checked.append(row['event'])
    if len(checked)!=(4 if crate else 20): raise ValueError('Missing corresponding-pass flag checks')
    report={'capture':path,'roi':[1140,360,140,200] if crate else [285,410,48,40],
            'sample':0,'checked_position_flags':checked,'runs':[], 'complete':False}
    wide = crate and os.environ.get('AOT_PROJECTION_WIDE') == '1'
    report['wide_enabled'] = wide
    if crate and (color['capture']!=path or color['query']['crop']!=report['roi']):
        raise ValueError('Crate baseline capture/crop mismatch')
    cap,controller=rd.OpenCaptureFile(),None
    owned=[]
    try:
        status=cap.OpenFile(path,'',None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status,controller=cap.OpenCapture(rd.ReplayOptions(),None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        for variant,event in zip(manifest['variants'],[122074,121346,124970] if crate else [7428,4902]):
            controller.SetFrameEvent(event,True);pipe=controller.GetPipelineState()
            reflection=pipe.GetShaderReflection(rd.ShaderStage.Vertex)
            if crate and any('ClipDistance' in str(s.systemValue) or 'CullDistance' in str(s.systemValue) for s in reflection.outputSignature):
                raise ValueError('Crate experiment requires no user clip/cull outputs')
            raw=bytes(reflection.rawBytes)
            if hashlib.sha256(raw).hexdigest()!=variant['original_sha256']: raise ValueError('Original VS mismatch')
            owned.append({'original':pipe.GetShader(rd.ShaderStage.Vertex),'replacement':None,'variant':variant})
        texture=next(t for t in controller.GetTextures() if str(t.resourceId)==('ResourceId::4309' if crate else 'ResourceId::10779'))
        if texture.msSamp!=(1 if crate else 2) or texture.format.Name()!='R16G16B16A16_FLOAT': raise ValueError('Unexpected HDR target')
        def geometry(event,primitive):
            controller.SetFrameEvent(event,True)
            mesh=controller.GetPostVSData(0,0,rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2,4) or not 16<=mesh.vertexByteStride<=(256 if crate else 128): raise ValueError('Unexpected mesh')
            idx=bytes(controller.GetBufferData(mesh.indexResourceId,mesh.indexByteOffset+primitive*3*mesh.indexByteStride,3*mesh.indexByteStride))
            vertices=[]
            for index in struct.unpack('<3'+('H' if mesh.indexByteStride==2 else 'I'),idx):
                index+=mesh.baseVertex
                if not 0<=index<100000: raise ValueError('Invalid vertex index')
                raw=bytes(controller.GetBufferData(mesh.vertexResourceId,mesh.vertexByteOffset+index*mesh.vertexByteStride,mesh.vertexByteStride))
                pos=struct.unpack_from('<4f',raw)
                if len(raw)!=mesh.vertexByteStride or not all(math.isfinite(v) for v in pos): raise ValueError('Invalid vertex data')
                vertices.append({'index':index,'position':pos,'other_hex':raw[16:].hex()})
            return {'event':event,'primitive':primitive,'vertices':vertices}
        def readback(event):
            controller.SetFrameEvent(event,True);sub=rd.Subresource();sub.sample=0
            data=bytes(controller.GetTextureData(texture.resourceId,sub))
            if len(data)!=texture.width*texture.height*8: raise ValueError('Unexpected HDR readback')
            x,y,w,h=report['roi']
            raw=b''.join(data[((y+j)*texture.width+x)*8:((y+j)*texture.width+x+w)*8] for j in range(h))
            values=struct.unpack('<'+'e'*(len(raw)//2),raw)
            if not all(math.isfinite(v) for i,v in enumerate(values) if i%4!=3): raise ValueError('Nonfinite RGB')
            return raw
        def extra_checks(mode):
            if not wide: return None
            with open(os.path.join(probe,'crate-stages/regions.json')) as source:
                stages=json.load(source)
            if not stages.get('complete') or stages['capture']!=path: raise ValueError('Missing stage inventory')
            ldr=next(t for t in controller.GetTextures() if str(t.resourceId)=='ResourceId::559')
            if ldr.width!=1280 or ldr.height!=2048 or ldr.msSamp!=1 or ldr.format.Name()!='R8G8B8A8_UNORM':
                raise ValueError('Unexpected full-scene LDR target')
            result={'readbacks':[], 'pixels':[]}
            for frame in (0,15,16,23):
                stage=next(s for s in stages['samples'] if s['frame']==frame and s['stage']=='ldr')
                controller.SetFrameEvent(stage['event'],True)
                data=bytes(controller.GetTextureData(ldr.resourceId,rd.Subresource()))
                if len(data)!=1280*2048*4:raise ValueError('Unexpected LDR layout')
                data=data[:1280*720*4]
                with open(os.path.join(out,'%s-wide-%d.rgba8'%(mode,frame)),'wb') as target:target.write(data)
                result['readbacks'].append({'frame':frame,'event':stage['event'],'sha256':hashlib.sha256(data).hexdigest()})
            for x,y in ((1264,504),(1268,490),(1240,440)):
                controller.SetFrameEvent(132867,True)
                history=controller.PixelHistory(texture.resourceId,x,y,rd.Subresource(),rd.CompType.Float)
                result['pixels'].append({'xy':[x,y],'history':[
                    {'event':h.eventId,'primitive':h.primitiveID,'passed':h.Passed(),'details':rd.DumpObject(h)}
                    for h in history if h.eventId in (124970,132867)]})
            return result
        baseline_geo=[geometry(e,p) for e,p in GEOMETRY]
        baseline=[readback(e) for e in READBACK]
        if crate and [hashlib.sha256(raw).hexdigest() for raw in baseline]!=[f['sha256'] for f in color['frames']]:
            raise ValueError('Original crate color did not reproduce')
        report['baseline_geometry']=baseline_geo
        baseline_extra=extra_checks('baseline')
        report['baseline_wide']=baseline_extra
        for event,raw in zip(READBACK,baseline):
            with open(os.path.join(out,'baseline-%d.bin'%event),'wb') as target: target.write(raw)
        modes=['unchanged','precise']
        if all('fallback_file' in item['variant'] for item in owned): modes.append('fallback')
        for mode in modes:
            run={'mode':mode,'builds':[],'readbacks':[]};report['runs'].append(run)
            try:
                for item in owned:
                    variant=item['variant'];filename=variant['original'] if mode=='unchanged' else os.path.join(out,variant['fallback_file' if mode=='fallback' else 'file'])
                    with open(filename,'rb') as source: data=source.read()
                    if hashlib.sha256(data).hexdigest()!=variant['original_sha256' if mode=='unchanged' else 'fallback_sha256' if mode=='fallback' else 'sha256']:
                        raise ValueError('Variant file hash mismatch')
                    replacement,errors=controller.BuildTargetShader('main',rd.ShaderEncoding.DXBC,data,rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
                    if replacement==rd.ResourceId.Null(): raise RuntimeError('Build failed: '+errors)
                    item['replacement']=replacement
                    if errors: raise RuntimeError('Shader build diagnostics: '+errors)
                    controller.ReplaceResource(item['original'],replacement)
                    run['builds'].append({'name':variant['layout']['name'],'messages':errors})
                run['geometry']=[geometry(e,p) for e,p in GEOMETRY]
                for before,after in zip(baseline_geo,run['geometry']):
                    if mode!='precise' and before!=after: raise ValueError('Original/fallback geometry control failed')
                    for a,b in zip(before['vertices'],after['vertices']):
                        if a['index']!=b['index'] or a['other_hex']!=b['other_hex']: raise ValueError('Non-position vertex data changed')
                run['depth_color_position_matches']=[]
                for depth,color_index in ([(0,1)] if crate else [(0,2),(1,3),(4,6),(5,7)]):
                    a,b=run['geometry'][depth],run['geometry'][color_index]
                    exact=[v['position'] for v in a['vertices']]==[v['position'] for v in b['vertices']]
                    run['depth_color_position_matches'].append(exact)
                    if not crate and not exact:
                        raise ValueError('Depth/color positions disagree')
                for event,original in zip(READBACK,baseline):
                    raw=readback(event)
                    if mode!='precise' and raw!=original: raise ValueError('Original/fallback HDR control failed')
                    with open(os.path.join(out,'%s-%d.bin'%(mode,event)),'wb') as target: target.write(raw)
                    run['readbacks'].append({'event':event,'sha256':hashlib.sha256(raw).hexdigest(),'changed':raw!=original})
                run['wide']=extra_checks(mode)
                if mode!='precise' and run['wide']!=baseline_extra:raise ValueError('Original/fallback full-scene control failed')
            finally:
                for item in owned:
                    if item['replacement'] is not None:
                        controller.RemoveReplacement(item['original']);controller.FreeTargetResource(item['replacement']);item['replacement']=None
        if [geometry(e,p) for e,p in GEOMETRY]!=baseline_geo or [readback(e) for e in READBACK]!=baseline:
            raise ValueError('Restoration control failed')
        if extra_checks('restored')!=baseline_extra:raise ValueError('Full-scene restoration failed')
        report['restoration_exact']=True
        status=controller.GetFatalErrorStatus()
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        report['complete']=True
    except BaseException:
        report['error']=traceback.format_exc()
    finally:
        if controller: controller.Shutdown()
        cap.Shutdown()
        with open(report_path,'w') as target: json.dump(report,target,indent=2)


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_PROJECTION_VARIANTS'],'launch-error.txt'),'w') as target: target.write(traceback.format_exc())
sys.exit(0)
