"""Validate the missing courtyard-car material against its captured prepass.

RenderDoc Python: AOT_RENDERDOC_PROBE, AOT_STATIC_MATERIAL_VARIANTS and optional
AOT_CAR_VALIDATION_NAME. Exact replacement/restoration, complete paired geometry,
unchanged material outputs, recovered visible fragments and hidden controls.
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
    root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    directory = os.environ['AOT_STATIC_MATERIAL_VARIANTS']
    kind = os.environ.get('AOT_CAR_VALIDATION_KIND','car')
    if kind not in ('car','neighbor'): raise ValueError('Unknown material kind')
    neighbor = kind=='neighbor'
    depth_event,material_event = (4870,6961) if neighbor else (4124,6976)
    expected = ('CB7E063397190431',6961,'b81e7f67bf18bdaacfd9927686149a96a2da0493e469d647c23e99d53edc0064') if neighbor else ('998F2B953D9B74FD',6976,'d522a2fad820373689ce053c20be40fd68f7a504f7b78034e1a99a87d126133b')
    with open(os.path.join(root,'probe.json')) as f: done = json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup')!=0: raise ValueError('Require normally closed capture')
    with open(os.path.join(root,'renderdoc-capture.json')) as f: path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path))!=os.path.normcase(root): raise ValueError('Foreign capture')
    with open(os.path.join(directory,'variants.json')) as f: variants = json.load(f)['variants']
    if len(variants)!=1 or (variants[0]['guest'],variants[0]['event'],variants[0]['original_sha256'])!=expected: raise ValueError('Unexpected material fixture')
    v = variants[0]
    name = os.environ.get('AOT_CAR_VALIDATION_NAME','car-material-validation-001')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name): raise ValueError('Invalid output name')
    out = os.path.join(root,name); os.mkdir(out)
    report = {'capture':path,'kind':kind,'variant':v,'runs':[],'complete':False}
    cap,ctl,replacement,original = rd.OpenCaptureFile(),None,None,None
    fixed = None
    def save():
        with open(os.path.join(out,'validation.json'),'w') as f: json.dump(report,f,indent=2)
    def release():
        nonlocal replacement
        if replacement is not None:
            ctl.RemoveReplacement(original); ctl.FreeTargetResource(replacement); replacement = None
    try:
        if cap.OpenFile(path,'',None)!=rd.ResultCode.Succeeded: raise RuntimeError('Capture open failed')
        status,ctl = cap.OpenCapture(rd.ReplayOptions(),None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        if neighbor:
            base = os.path.join(root,'precise-car-material-001','998F2B953D9B74FD.dxbc')
            with open(base,'rb') as f: base_raw = f.read()
            if hashlib.sha256(base_raw).hexdigest()!='77096ef52a892afb01a485bb5c26a04f40a1d3da43745e6e3277d178d7bfaebd': raise ValueError('Changed fixed car correction')
            ctl.SetFrameEvent(6976,True); p = ctl.GetPipelineState()
            if hashlib.sha256(bytes(p.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)).hexdigest()!='d522a2fad820373689ce053c20be40fd68f7a504f7b78034e1a99a87d126133b': raise ValueError('Unexpected base car shader')
            base_original = p.GetShader(rd.ShaderStage.Vertex)
            base_replacement,errors = ctl.BuildTargetShader('main',rd.ShaderEncoding.DXBC,base_raw,rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
            if base_replacement==rd.ResourceId.Null() or errors: raise RuntimeError('Base car shader build failed')
            fixed = base_original,base_replacement; ctl.ReplaceResource(*fixed)
            report['fixed_car_sha256'] = hashlib.sha256(base_raw).hexdigest()
        ctl.SetFrameEvent(depth_event,True)
        raw_depth = bytes(ctl.GetPipelineState().GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
        if hashlib.sha256(raw_depth).hexdigest()!='850505c384a4a7d36dff0a49a71a2ed2e7edb37d7aa572fa718a62162c33b926': raise ValueError('Unexpected car prepass')
        ctl.SetFrameEvent(material_event,True); pipe = ctl.GetPipelineState(); original = pipe.GetShader(rd.ShaderStage.Vertex)
        raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
        if hashlib.sha256(raw).hexdigest()!=v['original_sha256']: raise ValueError('Unexpected car material')
        with open(os.path.join(directory,v['file']),'rb') as f: precise = f.read()
        if hashlib.sha256(precise).hexdigest()!=v['sha256']: raise ValueError('Changed replacement')
        textures = {str(t.resourceId):t for t in ctl.GetTextures()}
        color,display = textures['ResourceId::6361'],textures['ResourceId::2771']
        if (color.width,color.height,color.msSamp,color.format.Name())!=(1280,1024,2,'R16G16B16A16_FLOAT') or (display.width,display.height,display.format.Name())!=(1280,2048,'R8G8B8A8_UNORM'): raise ValueError('Unexpected target layout')
        def geometry(event):
            ctl.SetFrameEvent(event,True); mesh = ctl.GetPostVSData(0,0,rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2,4) or not 0<mesh.numIndices<=32768 or not 16<=mesh.vertexByteStride<=256: raise ValueError('Invalid mesh bounds')
            ib = bytes(ctl.GetBufferData(mesh.indexResourceId,mesh.indexByteOffset,mesh.numIndices*mesh.indexByteStride))
            indices = struct.unpack('<'+str(mesh.numIndices)+('H' if mesh.indexByteStride==2 else 'I'),ib)
            refs = sorted({i+mesh.baseVertex for i in indices}); low,high = refs[0],refs[-1]; size = (high-low+1)*mesh.vertexByteStride
            if not 0<=low<=high<100000 or size>16*1024*1024: raise ValueError('Invalid vertex span')
            data = bytes(ctl.GetBufferData(mesh.vertexResourceId,mesh.vertexByteOffset+low*mesh.vertexByteStride,size))
            if len(data)!=size: raise ValueError('Short vertex readback')
            pos,other = bytearray(),bytearray()
            for i in refs:
                at = (i-low)*mesh.vertexByteStride; pos.extend(data[at:at+16]); other.extend(data[at+16:at+mesh.vertexByteStride])
            return ib,refs,bytes(pos),bytes(other)
        baseline,baseline_frame = {},None
        for mode in ('original','control','precise','restored'):
            release()
            if mode in ('control','precise'):
                replacement,errors = ctl.BuildTargetShader('main',rd.ShaderEncoding.DXBC,raw if mode=='control' else precise,rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
                if replacement==rd.ResourceId.Null() or errors: raise RuntimeError('Shader build: '+errors)
                ctl.ReplaceResource(original,replacement)
            run = {'mode':mode,'geometry':[],'pixels':[]}; report['runs'].append(run); current = {}
            for event in (depth_event,material_event):
                current[event] = geometry(event); ib,refs,pos,other = current[event]
                if mode=='original': baseline[event] = current[event]
                b = baseline[event]
                if (ib,refs,other)!=(b[0],b[1],b[3]) or (mode!='precise' or event==depth_event) and pos!=b[2]: raise ValueError('Material/control geometry changed')
                filename = mode+'-'+str(event)+'-positions.bin'
                with open(os.path.join(out,filename),'wb') as f: f.write(pos)
                run['geometry'].append({'event':event,'vertices':len(refs),'position_file':filename,'position_sha256':hashlib.sha256(pos).hexdigest(),'material_sha256':hashlib.sha256(other).hexdigest()})
            if mode=='precise' and current[depth_event][:3]!=current[material_event][:3]: raise ValueError('Corrected material/prepass positions or indices differ')
            if neighbor:
                results = ctl.FetchCounters([rd.GPUCounter.SamplesPassed])
                counts = {r.eventId:int(r.value.u64) for r in results if r.eventId in (depth_event,material_event)}
                if set(counts)!={depth_event,material_event}: raise ValueError('Missing coverage counters')
                run['samples_passed'] = counts
                if mode!='original' and counts!=report['runs'][0]['samples_passed']: raise ValueError('Offscreen material coverage changed')
            for x,y in (() if neighbor else ((910,410),(935,372),(1120,390))):
                for sample in (0,1):
                    ctl.SetFrameEvent(material_event,True); sub = rd.Subresource(); sub.sample = sample
                    hits = [{'primitive':h.primitiveID,'passed':h.Passed(),'details':rd.DumpObject(h)} for h in ctl.PixelHistory(color.resourceId,x,y,sub,rd.CompType.Float) if h.eventId==material_event]
                    if not hits: raise ValueError('Missing car pixel history')
                    run['pixels'].append({'x':x,'y':y,'sample':sample,'hits':hits})
            ctl.SetFrameEvent(14337,True); frame = bytes(ctl.GetTextureData(display.resourceId,rd.Subresource()))
            if len(frame)!=1280*2048*4: raise ValueError('Unexpected display packing')
            if mode=='original': baseline_frame = frame
            if (mode in ('control','restored') or neighbor) and frame!=baseline_frame: raise ValueError('Display control/restoration changed')
            with open(os.path.join(out,mode+'.rgba'),'wb') as f: f.write(frame)
            item = rd.TextureSave(); item.resourceId = display.resourceId; item.destType = rd.FileType.PNG
            if ctl.SaveTexture(item,os.path.join(out,mode+'.png'))!=rd.ResultCode.Succeeded: raise RuntimeError('Image save failed')
            run['frame_sha256'] = hashlib.sha256(frame).hexdigest()
            for before,after in zip(report['runs'][0]['pixels'],run['pixels']):
                b = {h['primitive']:h for h in before['hits']}; a = {h['primitive']:h for h in after['hits']}
                if set(b)!=set(a): raise ValueError('Car fragment footprint changed at control')
                for primitive in b:
                    recovery = before['x']==910 and primitive==6801
                    if recovery:
                        if b[primitive]['passed'] or b[primitive]['details']['depthTestFailed']!='True': raise ValueError('Fixture did not reproduce car rejection')
                        if a[primitive]['passed']!=(mode=='precise'): raise ValueError('Car material recovery/control failed')
                    elif b[primitive]['passed']!=a[primitive]['passed']: raise ValueError('Passing/hidden car fragment changed')
            save()
        if ctl.GetFatalErrorStatus()!=rd.ResultCode.Succeeded: raise RuntimeError('Replay GPU error')
        report.update(complete=True,paired_vertices=len(current[depth_event][1]),material_outputs_unchanged=True,restored_samples=0 if neighbor else 2,controls_preserved=True,restoration_exact=True)
    except BaseException: report['error'] = traceback.format_exc()
    finally:
        if ctl is not None:
            release()
            if fixed is not None: ctl.RemoveReplacement(fixed[0]); ctl.FreeTargetResource(fixed[1])
            ctl.Shutdown()
        cap.Shutdown(); save()


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'],'car-validation-error.txt'),'w') as f: f.write(traceback.format_exc())
sys.exit(0)
