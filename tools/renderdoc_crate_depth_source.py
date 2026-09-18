"""Trace the captured crate's resolved depth input without modifying GPU state."""
import hashlib
import json
import os
import sys
import traceback
import renderdoc as rd


def main():
    root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    with open(os.path.join(root,'probe.json')) as f: native=json.load(f)
    if native.get('timed_out') or native.get('exit_code_before_cleanup')!=0:
        raise ValueError('Require closed native probe')
    with open(os.path.join(root,'crate-depth-copy.json')) as f: prior=json.load(f)
    if not prior.get('complete') or prior.get('error'): raise ValueError('Require verified depth copy')
    path=prior['capture']
    if os.path.normcase(os.path.dirname(path))!=os.path.normcase(root): raise ValueError('Foreign capture')
    out=os.path.join(root,'crate-depth-source');os.mkdir(out)
    report={'capture':path,'complete':False,'uses':[]}
    cap,controller=rd.OpenCaptureFile(),None
    try:
        status=cap.OpenFile(path,'',None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status,controller=cap.OpenCapture(rd.ReplayOptions(),None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        tex=next(t for t in controller.GetTextures() if str(t.resourceId)=='ResourceId::4526')
        if tex.width!=1280 or tex.height!=720 or tex.msSamp!=1 or tex.format.Name()!='R32_FLOAT':
            raise ValueError('Unexpected resolved depth texture')
        for use in controller.GetUsage(tex.resourceId):
            if not 119016<=use.eventId<=123783: continue
            row={'event':use.eventId,'usage':str(use.usage),'bindings':[],'shaders':{}}
            report['uses'].append(row)
            controller.SetFrameEvent(use.eventId,True)
            pipe=controller.GetPipelineState()
            for stage in (rd.ShaderStage.Vertex,rd.ShaderStage.Pixel,rd.ShaderStage.Compute):
                reflection=pipe.GetShaderReflection(stage)
                if reflection is None: continue
                raw=bytes(reflection.rawBytes);sha=hashlib.sha256(raw).hexdigest()
                row['shaders'][stage.name]=sha
                with open(os.path.join(out,sha+'.dxbc'),'wb') as f:f.write(raw)
                dis=controller.DisassembleShader(pipe.GetComputePipelineObject() if stage==rd.ShaderStage.Compute else pipe.GetGraphicsPipelineObject(),reflection,'')
                with open(os.path.join(out,sha+'.txt'),'w') as f:f.write(dis)
            for access in controller.GetDescriptorAccess():
                if access.type not in (rd.DescriptorType.Image,rd.DescriptorType.ReadWriteImage,rd.DescriptorType.Buffer,
                                        rd.DescriptorType.ReadWriteBuffer,rd.DescriptorType.ConstantBuffer):continue
                region=rd.DescriptorRange();region.offset=access.byteOffset;region.descriptorSize=access.byteSize;region.count=1;region.type=access.type
                for desc in controller.GetDescriptors(access.descriptorStore,[region]):
                    row['bindings'].append({'access':rd.DumpObject(access),'descriptor':rd.DumpObject(desc)})
        controller.SetFrameEvent(123783,True)
        raw=bytes(controller.GetTextureData(tex.resourceId,rd.Subresource()))
        if len(raw)!=1280*720*4:raise ValueError('Unexpected R32 readback')
        crop=b''.join(raw[((502+y)*1280+1262)*4:((502+y)*1280+1267)*4] for y in range(5))
        with open(os.path.join(out,'source-5x5.r32'),'wb') as f:f.write(crop)
        report['crop']={'xywh':[1262,502,5,5],'sha256':hashlib.sha256(crop).hexdigest()}
        status=controller.GetFatalErrorStatus()
        if status!=rd.ResultCode.Succeeded:raise RuntimeError(str(status))
        report['complete']=True
    except BaseException:report['error']=traceback.format_exc()
    finally:
        if controller:controller.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out,'source.json'),'w') as f:json.dump(report,f,indent=2)
    return int('error' in report)


sys.exit(main())
