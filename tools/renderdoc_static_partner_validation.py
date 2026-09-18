"""Check remaining static projections in a closed capture, including visible PIP.

RenderDoc Python: AOT_RENDERDOC_PROBE, AOT_STATIC_MATERIAL_VARIANTS, optional
AOT_STATIC_MATERIAL_NAME. Prior material/lighting corrections remain applied
throughout. Different camera positions are never compared with each other.
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
    variant_dir = os.environ['AOT_STATIC_MATERIAL_VARIANTS']
    name = os.environ.get('AOT_STATIC_MATERIAL_NAME', 'static-partner-validation-001')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name): raise ValueError('Invalid output name')
    with open(os.path.join(root, 'probe.json')) as f: done = json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0: raise ValueError('Require normally closed native capture')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f: path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root): raise ValueError('Foreign capture')
    with open(os.path.join(variant_dir, 'variants.json')) as f: variants = json.load(f)['variants']
    expected = [('87C093F46637D13F',7962), ('D66FF6280606248C',8933), ('B22EF913802807F8',9119)]
    if [(v['guest'],v['event']) for v in variants] != expected: raise ValueError('Unexpected variants')
    pairs = ((4771,7962), (4737,8933), (5196,9119))
    pip_events = (1298,1310,1373)
    events = tuple(e for p in pairs for e in p)+pip_events
    queries = ((1298,147,94,0),(1298,146,96,0), (1310,20,78,0),(1310,27,82,0),
               (1373,182,4,0),(1373,143,14,0), (7962,57,391,0),(7962,57,391,1),
               (8933,504,398,0),(8933,504,398,1), (9119,1197,141,0),(9119,1197,141,1))
    out = os.path.join(root, name); os.mkdir(out)
    report = {'capture':path, 'variants':variants, 'runs':[], 'complete':False}
    cap, ctl, fixed, targets = rd.OpenCaptureFile(), None, [], []
    baseline_mesh, baseline_images = {}, {}
    def save():
        with open(os.path.join(out,'validation.json'),'w') as f: json.dump(report,f,indent=2)
    def build(raw):
        rid, errors = ctl.BuildTargetShader('main',rd.ShaderEncoding.DXBC,raw,rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
        if rid == rd.ResourceId.Null() or errors: raise RuntimeError('Shader build: '+errors)
        return rid
    def source(v, directory):
        ctl.SetFrameEvent(v['event'],True); pipe = ctl.GetPipelineState()
        raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
        if hashlib.sha256(raw).hexdigest() != v['original_sha256']: raise ValueError('Unexpected captured shader')
        with open(os.path.join(directory,v['file']),'rb') as f: precise = f.read()
        if hashlib.sha256(precise).hexdigest() != v['sha256']: raise ValueError('Changed replacement')
        return pipe.GetShader(rd.ShaderStage.Vertex), raw, precise
    def release():
        for target in targets:
            if target['replacement'] is not None:
                ctl.RemoveReplacement(target['original']); ctl.FreeTargetResource(target['replacement']); target['replacement'] = None
    try:
        if cap.OpenFile(path,'',None) != rd.ResultCode.Succeeded: raise RuntimeError('Capture open failed')
        status, ctl = cap.OpenCapture(rd.ReplayOptions(),None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        base_expected = {
            '3306D6C23B238BE6':'f348a828675d9b0ef98fc3e8a7a9fc2620014f5409df1090cfd04b5032ff5644',
            'AC2A17351535ED19':'f96c864fcdda78946182db9a29360464dc45e6e659fa706f98649a3ba7adfd41',
            '807B2A09A19C3B15':'245e3d60a82295a0b76253150d1bfa49232b7f44e1a9cf6296f759373f5a4f75',
            '494DCD69B7BA177C':'78ec8e2c6fa90109dee8574e8b475963c940b92d7eab9476d8f862dbcb87f815'}
        report['fixed_baseline_variants'] = []
        for folder in ('precise-static-materials-001','precise-static-lighting-001'):
            directory = os.path.join(root,folder)
            with open(os.path.join(directory,'variants.json')) as f: base = json.load(f)['variants']
            for v in base:
                if base_expected.pop(v['guest'],None) != v['sha256']: raise ValueError('Unexpected baseline correction')
                original, _, precise = source(v,directory); replacement = build(precise)
                fixed.append((original,replacement)); ctl.ReplaceResource(original,replacement)
                report['fixed_baseline_variants'].append(v)
        if base_expected: raise ValueError('Missing baseline correction')
        for v in variants:
            original, raw, precise = source(v,variant_dir)
            targets.append({'original':original,'raw':raw,'precise':precise,'replacement':None})
        textures = {str(t.resourceId):t for t in ctl.GetTextures()}
        def last(nodes): return max([0]+[max(a.eventId,last(a.children)) for a in nodes])
        end = last(ctl.GetRootActions())
        def geometry(event):
            ctl.SetFrameEvent(event,True); mesh = ctl.GetPostVSData(0,0,rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2,4) or not 0 < mesh.numIndices <= 32768 or not 16 <= mesh.vertexByteStride <= 256: raise ValueError('Invalid mesh bounds')
            ib = bytes(ctl.GetBufferData(mesh.indexResourceId,mesh.indexByteOffset,mesh.numIndices*mesh.indexByteStride))
            indices = struct.unpack('<'+str(mesh.numIndices)+('H' if mesh.indexByteStride == 2 else 'I'),ib)
            refs = sorted({i+mesh.baseVertex for i in indices}); low, high = refs[0],refs[-1]
            size = (high-low+1)*mesh.vertexByteStride
            if not 0 <= low <= high < 100000 or size > 16*1024*1024: raise ValueError('Invalid vertex range')
            raw = bytes(ctl.GetBufferData(mesh.vertexResourceId,mesh.vertexByteOffset+low*mesh.vertexByteStride,size))
            if len(raw) != size: raise ValueError('Short vertex read')
            pos, other = bytearray(),bytearray()
            for i in refs:
                at = (i-low)*mesh.vertexByteStride
                pos.extend(raw[at:at+16]); other.extend(raw[at+16:at+mesh.vertexByteStride])
            return ib,refs,bytes(pos),bytes(other)
        for mode in ('original','control','87c0','d66f','b22e','all','restored'):
            release()
            active = {0,1,2} if mode == 'all' else {('87c0','d66f','b22e').index(mode)} if mode in ('87c0','d66f','b22e') else set()
            for i,target in enumerate(targets):
                if i not in active and mode != 'control': continue
                replacement = build(target['raw'] if mode == 'control' else target['precise'])
                target['replacement'] = replacement; ctl.ReplaceResource(target['original'],replacement)
            run = {'mode':mode,'geometry':[],'pixels':[],'images':[]}; report['runs'].append(run)
            current = {}
            for event in events:
                current[event] = geometry(event); ib,refs,pos,other = current[event]
                if mode == 'original': baseline_mesh[event] = current[event]
                b = baseline_mesh[event]
                changed = any(event == pairs[i][1] or i == 0 and event in pip_events for i in active)
                if (ib,refs,other) != (b[0],b[1],b[3]) or not changed and pos != b[2]: raise ValueError('Material output/control/restoration changed')
                filename = mode+'-'+str(event)+'-positions.bin'
                with open(os.path.join(out,filename),'wb') as f: f.write(pos)
                run['geometry'].append({'event':event,'vertices':len(refs),'position_file':filename,
                    'position_sha256':hashlib.sha256(pos).hexdigest(),'material_sha256':hashlib.sha256(other).hexdigest()})
            for i in active:
                depth,material = pairs[i]
                if current[depth][:3] != current[material][:3]: raise ValueError('Main-view paired indices/positions differ: '+str(material))
            for event,x,y,sample in queries:
                ctl.SetFrameEvent(event,True); pipe = ctl.GetPipelineState(); sub = rd.Subresource(); sub.sample = sample
                history = ctl.PixelHistory(pipe.GetOutputTargets()[0].resource,x,y,sub,rd.CompType.Float)
                hits = [{'primitive':h.primitiveID,'passed':h.Passed(),'details':rd.DumpObject(h)} for h in history if h.eventId == event]
                if not hits: raise ValueError('Missing pixel control')
                run['pixels'].append({'event':event,'x':x,'y':y,'sample':sample,'hits':hits})
            for label,event,resource,size in (('pip',1373,'ResourceId::6398',1280*2048*8),('display',end,'ResourceId::2657',1280*2048*4)):
                ctl.SetFrameEvent(event,True); tex = textures[resource]
                raw = bytes(ctl.GetTextureData(tex.resourceId,rd.Subresource()))
                if len(raw) != size: raise ValueError('Unexpected image packing')
                if mode == 'original': baseline_images[label] = raw
                if mode in ('control','restored') and raw != baseline_images[label]: raise ValueError('Image control/restoration changed')
                with open(os.path.join(out,mode+'-'+label+'.raw'),'wb') as f: f.write(raw)
                item = rd.TextureSave(); item.resourceId = tex.resourceId; item.destType = rd.FileType.PNG
                if ctl.SaveTexture(item,os.path.join(out,mode+'-'+label+'.png')) != rd.ResultCode.Succeeded: raise RuntimeError('Image save failed')
                run['images'].append({'label':label,'event':event,'resource':resource,'sha256':hashlib.sha256(raw).hexdigest()})
            if mode != 'original':
                for b,a in zip(report['runs'][0]['pixels'],run['pixels']):
                    if [(h['primitive'],h['passed']) for h in b['hits']] != [(h['primitive'],h['passed']) for h in a['hits']]: raise ValueError('Passing/hidden fragment control changed')
            save()
        if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('Replay GPU error')
        report.update(complete=True,paired_vertices=sum(len(current[p[0]][1]) for p in pairs),
            pip_vertices=sum(len(current[e][1]) for e in pip_events),material_outputs_unchanged=True,controls_preserved=True,restoration_exact=True)
    except BaseException: report['error'] = traceback.format_exc()
    finally:
        if ctl is not None:
            release()
            for original,replacement in fixed: ctl.RemoveReplacement(original); ctl.FreeTargetResource(replacement)
            ctl.Shutdown()
        cap.Shutdown(); save()


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'],'static-partner-validation-error.txt'),'w') as f: f.write(traceback.format_exc())
sys.exit(0)
