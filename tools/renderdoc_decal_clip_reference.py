"""Record clipping-test references after a verified native decal projection replay."""
import hashlib
import json
import os
import struct
import sys
import traceback
import renderdoc as rd


def main():
    root=os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    with open(os.path.join(root,'probe.json')) as f: done=json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup')!=0: raise ValueError('Require closed native probe')
    with open(os.path.join(root,'renderdoc-capture.json')) as f: path=json.load(f)['captures'][0]['path']
    courtyard=os.environ.get('AOT_PROJECTION_CLIP_KIND')=='courtyard'
    with open(os.path.join(root,'impact-color-native/color.json' if courtyard else 'depth-native.json'),'rb') as f: raw=f.read()
    native=json.loads(raw)
    if (native.get('error') or not native.get('complete') or not native.get('restoration_exact') or
            native['capture']!=path or os.path.normcase(os.path.dirname(path))!=os.path.normcase(root)):
        raise ValueError('Require completed native precision replay')
    prior={v['event']:v for v in native['vertices']}
    output=os.path.join(root,'courtyard-clip-reference' if courtyard else 'decal-clip-reference');os.mkdir(output)
    report={'capture':path,'native_reference_sha256':hashlib.sha256(raw).hexdigest(),
            'restoration_exact':False,'runs':[{'mode':'baseline','draws':[]},{'mode':'precise','draws':[]}]}
    cap,controller,replacement,original=rd.OpenCaptureFile(),None,None,None
    try:
        status=cap.OpenFile(path,'',None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status,controller=cap.OpenCapture(rd.ReplayOptions(),None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        stride=128 if courtyard else 112
        if courtyard:
            variant_root=os.path.join(root,native['query']['variant'])
            with open(os.path.join(variant_root,'variants.json')) as f: variant=json.load(f)['variants'][0]
            with open(os.path.join(variant_root,variant['file']),'rb') as f: code=f.read()
            if hashlib.sha256(code).hexdigest()!=variant['sha256']: raise ValueError('Variant hash mismatch')
            replacement,errors=controller.BuildTargetShader('main',rd.ShaderEncoding.DXBC,code,rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
            if replacement==rd.ResourceId.Null() or errors: raise RuntimeError('Shader build failed: '+errors)
        for event in ([4881,4888] if courtyard else [11781,11796]):
            controller.SetFrameEvent(event,True)
            mesh=controller.GetPostVSData(0,0,rd.MeshDataStage.VSOut)
            if mesh.vertexByteStride!=stride or mesh.indexByteStride not in (2,4): raise ValueError('Unexpected mesh layout')
            ib=bytes(controller.GetBufferData(mesh.indexResourceId,mesh.indexByteOffset,6*mesh.indexByteStride))
            indices=sorted({i+mesh.baseVertex for i in struct.unpack('<6'+('H' if mesh.indexByteStride==2 else 'I'),ib)})
            if indices!=[0,1,2,3]: raise ValueError('Unexpected indices')
            vb=bytes(controller.GetBufferData(mesh.vertexResourceId,mesh.vertexByteOffset,4*stride))
            if len(vb)!=4*stride: raise ValueError('Short mesh data')
            pos=b''.join(vb[i*stride:i*stride+16] for i in range(4))
            other=b''.join(vb[i*stride+16:(i+1)*stride] for i in range(4))
            if courtyard:
                if event not in prior or not prior[event]['interpolators_exact']: raise ValueError('Unverified native event')
                pipe=controller.GetPipelineState()
                if hashlib.sha256(bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)).hexdigest()!=variant['original_sha256']:
                    raise ValueError('Unexpected original shader')
                original=pipe.GetShader(rd.ShaderStage.Vertex)
                controller.ReplaceResource(original,replacement)
                controller.SetFrameEvent(event,True)
                changed_mesh=controller.GetPostVSData(0,0,rd.MeshDataStage.VSOut)
                if changed_mesh.vertexByteStride!=stride: raise ValueError('Changed vertex layout')
                changed=bytes(controller.GetBufferData(changed_mesh.vertexResourceId,changed_mesh.vertexByteOffset,4*stride))
                if len(changed)!=len(vb) or b''.join(changed[i*stride+16:(i+1)*stride] for i in range(4))!=other:
                    raise ValueError('Changed interpolators')
                precise=b''.join(changed[i*stride:i*stride+16] for i in range(4))
                controller.RemoveReplacement(original)
                controller.SetFrameEvent(event,True)
                restored=controller.GetPostVSData(0,0,rd.MeshDataStage.VSOut)
                if bytes(controller.GetBufferData(restored.vertexResourceId,restored.vertexByteOffset,4*stride))!=vb:
                    raise ValueError('Vertex restoration mismatch')
                prior[event].update(original_positions=struct.unpack('<16f',pos),precise_positions=struct.unpack('<16f',precise))
            elif pos!=struct.pack('<16f',*prior[event]['original_positions']): raise ValueError('Changed original positions')
            for run,field in zip(report['runs'],['original_positions','precise_positions']):
                run['draws'].append({'event':event,'indices':6,'stride':stride,'referenced_indices':indices,
                    'index_sha256':hashlib.sha256(ib).hexdigest(),'other_sha256':hashlib.sha256(other).hexdigest(),
                    'position_sha256':hashlib.sha256(struct.pack('<16f',*prior[event][field])).hexdigest()})
        status=controller.GetFatalErrorStatus()
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        report['restoration_exact']=True
    except BaseException:
        report['error']=traceback.format_exc()
    finally:
        if controller:
            if replacement is not None and replacement!=rd.ResourceId.Null():
                if original is not None: controller.RemoveReplacement(original)
                controller.FreeTargetResource(replacement)
            controller.Shutdown()
        cap.Shutdown()
        with open(os.path.join(output,'vertices.json'),'w') as f: json.dump(report,f,indent=2)


main()
sys.exit(0)
