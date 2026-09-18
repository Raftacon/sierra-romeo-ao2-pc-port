"""Compare complete projected decal footprints using RenderDoc's depth overlay.

Set AOT_RENDERDOC_PROBE, AOT_DECAL_FOOTPRINT_QUERY. Query fields: name,
geometry (successful inventory basename), events, and variant (absolute native
precision fixture directory, optional). Without a variant, repeat the original
readbacks as a consistency check. The overlay substitutes a fixed-color PS, so this
measures triangle visibility/depth coverage, not the bullet texture's opacity.
An optional replacement_event selects an upstream vertex shader instead of
the impact shader; impact shader and vertex bytes must then remain unchanged.
"""
import hashlib
import json
import math
import os
import re
import struct
import sys
import traceback
import renderdoc as rd


def main():
    root=os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    with open(os.path.join(root,'probe.json')) as f: done=json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup')!=0: raise ValueError('Require normally closed probe')
    with open(os.path.join(root,'renderdoc-capture.json')) as f: path=json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path))!=os.path.normcase(root): raise ValueError('Capture must belong to probe')
    with open(os.environ['AOT_DECAL_FOOTPRINT_QUERY']) as f: query=json.load(f)
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',query[k]) for k in ('name','geometry')):
        raise ValueError('Require simple basenames')
    stride=query.get('vertex_stride',112)
    if type(stride) is not int or not 16<=stride<=256 or stride%16:
        raise ValueError('Require bounded float4 vertex stride')
    with open(os.path.join(root,query['geometry']+'.json'),'rb') as f: raw=f.read()
    geometry=json.loads(raw)
    if geometry.get('error') or geometry['capture']!=path: raise ValueError('Invalid geometry inventory')
    events=query['events']
    draws={d['event']:d for d in geometry['draws']}
    if not 1<=len(events)<=48 or len(set(events))!=len(events) or any(e not in draws for e in events):
        raise ValueError('Require one to 48 unique inventoried events')
    references={}
    for mode,key in [('original','depth_original'),('precise','depth_precise')]:
        if key not in query: continue
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',query[key]): raise ValueError('Require simple reference basename')
        with open(os.path.join(root,query[key]+'.json'),'rb') as f: data=f.read()
        reference=json.loads(data)
        if not reference.get('complete') or reference.get('error') or reference['capture']!=path:
            raise ValueError('Invalid depth-history reference')
        references[mode]={'sha256':hashlib.sha256(data).hexdigest(),'samples':reference['samples']}
    out=os.path.join(root,query['name']);os.mkdir(out)
    report={'capture':path,'query':query,'geometry_sha256':hashlib.sha256(raw).hexdigest(),
            'complete':False,'runs':[],'depth_references':{m:r['sha256'] for m,r in references.items()}}
    def save():
        with open(os.path.join(out,'footprints.json'),'w') as f: json.dump(report,f,indent=2)
    cap,controller,output,replacement,original=rd.OpenCaptureFile(),None,None,None,None
    extra_owned = []
    baseline={}
    try:
        status=cap.OpenFile(path,'',None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status,controller=cap.OpenCapture(rd.ReplayOptions(),None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        tex=next(t for t in controller.GetTextures() if str(t.resourceId)==geometry['query']['resource'])
        if tex.msSamp!=1 or tex.format.Name()!='R16G16B16A16_FLOAT': raise ValueError('Require single-sample HDR target')
        output=controller.CreateOutput(rd.CreateHeadlessWindowingData(1280,720),rd.ReplayOutputType.Texture)
        display=rd.TextureDisplay()
        display.resourceId=tex.resourceId
        display.overlay=rd.DebugOverlay.Depth
        output.SetTextureDisplay(display)
        variant = None
        if 'variant' in query:
            with open(os.path.join(query['variant'],'variants.json')) as f: variants=json.load(f)['variants']
            if len(variants) != (2 if query.get('paired_upstream') else 1):
                raise ValueError('Unexpected variant count')
            variant = variants[0]
        upstream = query.get('replacement_event')
        if upstream is not None:
            if not variant or type(upstream) is not int or not 0 < upstream < max(events):
                raise ValueError('Require an earlier upstream replacement event and variant')
            controller.SetFrameEvent(upstream, True)
            pipe = controller.GetPipelineState()
            reflection = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
            if hashlib.sha256(bytes(reflection.rawBytes)).hexdigest() != variant['original_sha256']:
                raise ValueError('Unexpected upstream vertex shader')
            original = pipe.GetShader(rd.ShaderStage.Vertex)
        if query.get('paired_upstream'):
            if upstream is None: raise ValueError('Paired control requires upstream replacement')
            extra = variants[1]
            extra_event = extra.get('event')
            if type(extra_event) is not int or not 0 < extra_event < max(events):
                raise ValueError('Invalid paired replacement event')
            controller.SetFrameEvent(extra_event, True)
            pipe = controller.GetPipelineState()
            if hashlib.sha256(bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)).hexdigest() != extra['original_sha256']:
                raise ValueError('Unexpected paired shader')
            extra_original = pipe.GetShader(rd.ShaderStage.Vertex)
            if extra_original == original: raise ValueError('Duplicate paired shader')
            report['paired_variant'] = extra
        report['variant']=variant
        for mode in (('original','precise','restored') if variant else ('original','repeated')):
            if mode=='precise':
                with open(os.path.join(query['variant'],variant['file']),'rb') as f: code=f.read()
                if hashlib.sha256(code).hexdigest()!=variant['sha256']: raise ValueError('Variant hash mismatch')
                replacement,errors=controller.BuildTargetShader('main',rd.ShaderEncoding.DXBC,code,rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
                if replacement==rd.ResourceId.Null() or errors: raise RuntimeError('Shader build failed: '+errors)
                controller.ReplaceResource(original,replacement)
                if query.get('paired_upstream'):
                    with open(os.path.join(query['variant'], extra['file']), 'rb') as f: code = f.read()
                    if hashlib.sha256(code).hexdigest() != extra['sha256']: raise ValueError('Changed paired variant')
                    target, errors = controller.BuildTargetShader('main', rd.ShaderEncoding.DXBC, code, rd.ShaderCompileFlags(), rd.ShaderStage.Vertex)
                    if target != rd.ResourceId.Null(): extra_owned.append((extra_original, target))
                    if target == rd.ResourceId.Null() or errors: raise RuntimeError('Paired build failed: '+errors)
                    controller.ReplaceResource(extra_original, target)
            elif mode=='restored':
                controller.RemoveReplacement(original);controller.FreeTargetResource(replacement);replacement=None
                for source, target in extra_owned:
                    controller.RemoveReplacement(source); controller.FreeTargetResource(target)
                extra_owned = []
            run={'mode':mode,'draws':[]};report['runs'].append(run)
            for event in events:
                controller.SetFrameEvent(event,True)
                pipe=controller.GetPipelineState()
                native=controller.GetD3D12PipelineState()
                depth=native.outputMerger.depthStencilState
                if not depth.depthEnable or depth.depthWrites or depth.stencilEnable or depth.depthBoundsEnable:
                    raise ValueError('Require read-only depth testing without stencil/bounds tests')
                ps=pipe.GetShaderReflection(rd.ShaderStage.Pixel)
                if hashlib.sha256(bytes(ps.rawBytes)).hexdigest()!=draws[event]['shaders']['Pixel']['sha256']:
                    raise ValueError('Unexpected pixel shader')
                if any('DepthOutput' in str(s.systemValue) for s in ps.outputSignature):
                    raise ValueError('Require rasterizer depth, not shader-written depth')
                if mode=='original':
                    reflection=pipe.GetShaderReflection(rd.ShaderStage.Vertex)
                    expected_sha = variant['original_sha256'] if variant and upstream is None else draws[event]['shaders']['Vertex']['sha256']
                    if hashlib.sha256(bytes(reflection.rawBytes)).hexdigest()!=expected_sha:
                        raise ValueError('Unexpected original shader')
                    shader=pipe.GetShader(rd.ShaderStage.Vertex)
                    if upstream is None:
                        if original is not None and shader!=original: raise ValueError('Different original shader resources')
                        original=shader
                if upstream is not None:
                    reflection = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
                    if hashlib.sha256(bytes(reflection.rawBytes)).hexdigest() != draws[event]['shaders']['Vertex']['sha256']:
                        raise ValueError('Upstream control changed the impact shader')
                mesh=controller.GetPostVSData(0,0,rd.MeshDataStage.VSOut)
                keys=sorted(int(i) for i in draws[event]['positions'])
                if not 3<=len(keys)<=16 or keys!=list(range(len(keys))) or mesh.vertexByteStride!=stride:
                    raise ValueError('Require bounded contiguous decal vertices with known output layout')
                expected=draws[event].get('adjusted_indices',draws[event]['indices'])
                if mesh.indexByteStride not in (2,4): raise ValueError('Unexpected index width')
                ib=bytes(controller.GetBufferData(mesh.indexResourceId,mesh.indexByteOffset,len(expected)*mesh.indexByteStride))
                indices=struct.unpack('<'+str(len(expected))+('H' if mesh.indexByteStride==2 else 'I'),ib)
                if [i+mesh.baseVertex for i in indices]!=expected: raise ValueError('Changed triangle indices')
                vb=bytes(controller.GetBufferData(mesh.vertexResourceId,mesh.vertexByteOffset,len(keys)*stride))
                if len(vb)!=len(keys)*stride: raise ValueError('Short vertex readback')
                if upstream is not None:
                    vertex_key = ('vertices', event)
                    if mode == 'original': baseline[vertex_key] = vb
                    elif vb != baseline[vertex_key]: raise ValueError('Upstream control changed impact vertices')
                vp=pipe.GetViewport(0)
                points=[]
                for i in keys:
                    x,y,z,w=struct.unpack_from('<4f',vb,i*stride)
                    if not all(math.isfinite(v) for v in (x,y,z,w)) or w<=0 or not 0<=z<=w:
                        raise ValueError('Require finite, unclipped positive-W quad')
                    points.append([vp.x+(x/w+1)*vp.width/2,vp.y+(1-y/w)*vp.height/2])
                box=[math.floor(min(p[0] for p in points))-2,math.floor(min(p[1] for p in points))-2,
                     math.ceil(max(p[0] for p in points))+2,math.ceil(max(p[1] for p in points))+2]
                if not 0<=box[0]<box[2]<=tex.width or not 0<=box[1]<box[3]<=tex.height:
                    raise ValueError('Require on-target quad bounds')
                # GetDebugOverlayTexID refreshes the overlay at the current event.
                overlay=output.GetDebugOverlayTexID()
                if overlay==rd.ResourceId.Null(): raise ValueError('No depth overlay')
                data=bytes(controller.GetTextureData(overlay,rd.Subresource()))
                if len(data)!=tex.width*tex.height*8: raise ValueError('Unexpected RGBA16F overlay size')
                crop=b''.join(data[(y*tex.width+box[0])*8:(y*tex.width+box[2])*8] for y in range(box[1],box[3]))
                colors={}
                for pixel in struct.iter_unpack('<4e',crop): colors[pixel]=colors.get(pixel,0)+1
                red,green,clear=(1.0,0.0,0.0,1.0),(0.0,1.0,0.0,1.0),(0.0,0.0,0.0,0.0)
                if any(c not in (red,green,clear) for c in colors) or not colors.get(red,0)+colors.get(green,0):
                    raise ValueError('Unexpected or empty depth overlay')
                width,height=box[2]-box[0],box[3]-box[1]
                def pixel_at(x,y): return struct.unpack_from('<4e',crop,(y*width+x)*8)
                if (any(pixel_at(x,y)!=clear for y in (0,height-1) for x in range(width)) or
                        any(pixel_at(x,y)!=clear for x in (0,width-1) for y in range(height))):
                    raise ValueError('Footprint touches crop border')
                filename='%s-%d-rgba16f.bin'%(mode,event)
                with open(os.path.join(out,filename),'wb') as f: f.write(crop)
                item={'event':event,'box':box,'points':points,'file':filename,
                      'sha256':hashlib.sha256(crop).hexdigest(),
                      'colors':[{'rgba':list(c),'count':n} for c,n in sorted(colors.items())],
                      'passing':colors.get(green,0),'rejected':colors.get(red,0),
                      'rasterizer':rd.DumpObject(native.rasterizer.state),'depth_stencil':rd.DumpObject(depth)}
                reference=references.get('original' if mode in ('restored','repeated') else mode)
                if reference:
                    checked=[]
                    for s in reference['samples']:
                        if s['event']!=event: continue
                        if len(s['matches'])!=1: raise ValueError('Ambiguous history reference')
                        x,y=s['pixel']
                        if not box[0]<=x<box[2] or not box[1]<=y<box[3]: raise ValueError('Reference outside footprint')
                        if pixel_at(x-box[0],y-box[1])!=(green if s['matches'][0]['passed'] else red):
                            raise ValueError('Depth overlay disagrees with pixel history')
                        checked.append(s['pixel'])
                    if len(checked)!=2: raise ValueError('Require two independent interior history samples per draw')
                    item['history_checks']=checked
                if mode=='original': baseline[event]=(box,crop)
                elif mode in ('restored','repeated') and (box,crop)!=baseline[event]: raise ValueError('Overlay restoration/repeat mismatch')
                run['draws'].append(item);save()
        status=controller.GetFatalErrorStatus()
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        report['complete']=True
        report['restoration_exact' if variant else 'repeat_exact']=True
    except BaseException:
        report['error']=traceback.format_exc()
    finally:
        if output is not None: output.Shutdown()
        if controller is not None:
            for source, target in extra_owned:
                controller.RemoveReplacement(source); controller.FreeTargetResource(target)
            if replacement is not None and replacement!=rd.ResourceId.Null():
                controller.RemoveReplacement(original);controller.FreeTargetResource(replacement)
            controller.Shutdown()
        cap.Shutdown();save()


main()
sys.exit(0)
