"""Inventory corresponding equipment depth/color draws in the captured flash.

Run in RenderDoc Python with AOT_RENDERDOC_PROBE naming the closed sequence.
Only the first two guest frames' matching index counts are inspected.
"""
import hashlib
import json
import os
import re
import sys
import traceback
import renderdoc as rd


def main():
    directory = os.environ['AOT_RENDERDOC_PROBE']
    with open(os.path.join(directory,'probe.json')) as source:
        done=json.load(source)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    with open(os.path.join(directory,'renderdoc-capture.json')) as source:
        path=json.load(source)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(os.path.abspath(directory)):
        raise ValueError('Capture must belong to probe')
    name=os.environ.get('AOT_EQUIPMENT_PASSES_NAME','equipment-passes')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name): raise ValueError('Require a simple output name')
    requested = None
    if os.environ.get('AOT_EQUIPMENT_PASSES_QUERY'):
        with open(os.environ['AOT_EQUIPMENT_PASSES_QUERY']) as source:
            requested = json.load(source)['events']
        if (not 1 <= len(requested) <= 96 or len(set(requested)) != len(requested) or
                any(type(e) is not int or e <= 0 for e in requested)):
            raise ValueError('Require 1-96 unique positive draw events')
    output=os.path.join(directory,name)
    os.mkdir(output)
    report={'capture':path,'draws':[], 'requested_events':requested, 'complete':False}
    cap,controller=rd.OpenCaptureFile(),None
    try:
        status=cap.OpenFile(path,'',None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status,controller=cap.OpenCapture(rd.ReplayOptions(),None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        actions=[]
        def visit(nodes):
            for action in nodes:
                selected = (action.eventId in requested if requested is not None else
                            action.eventId<=21516 and action.numIndices in (1722,8352))
                if selected and action.flags & rd.ActionFlags.Drawcall:
                    actions.append(action)
                visit(action.children)
        visit(controller.GetRootActions())
        if requested is not None:
            if len(actions) != len(requested): raise ValueError('Missing requested draw event')
        elif not 4 <= len(actions) <= 40: raise ValueError('Unexpected candidate count')
        for action in actions:
            controller.SetFrameEvent(action.eventId,True)
            pipe=controller.GetPipelineState()
            # ActionDescription links to other actions; avoid recursively
            # dumping this graph through RenderDoc's native object serializer.
            row={'event':action.eventId,'action':{
                    'numIndices':action.numIndices,'indexOffset':action.indexOffset,
                    'baseVertex':action.baseVertex,'numInstances':action.numInstances,
                    'outputs':[str(r) for r in action.outputs],'depthOut':str(action.depthOut)},
                 'rasterizer':rd.DumpObject(controller.GetD3D12PipelineState().rasterizer),
                 'depth':rd.DumpObject(controller.GetD3D12PipelineState().outputMerger.depthStencilState),
                 'blend':rd.DumpObject(controller.GetD3D12PipelineState().outputMerger.blendState),
                 'shaders':{},'vertex_constants':[]}
            report['draws'].append(row)
            for stage in (rd.ShaderStage.Vertex,rd.ShaderStage.Pixel):
                reflection=pipe.GetShaderReflection(stage)
                if reflection is None: continue
                raw=bytes(reflection.rawBytes)
                dis=controller.DisassembleShader(pipe.GetGraphicsPipelineObject(),reflection,'')
                name=hashlib.sha256(raw).hexdigest()
                with open(os.path.join(output,name+'.dxbc'),'wb') as target: target.write(raw)
                with open(os.path.join(output,name+'.txt'),'w') as target: target.write(dis)
                row['shaders'][stage.name]={'sha256':name,'header':dis.splitlines()[0],
                                          'resource':str(pipe.GetShader(stage))}
            vs=pipe.GetShaderReflection(rd.ShaderStage.Vertex)
            for access in controller.GetDescriptorAccess():
                if access.stage!=rd.ShaderStage.Vertex or access.type!=rd.DescriptorType.ConstantBuffer: continue
                region=rd.DescriptorRange()
                region.offset,region.descriptorSize,region.count=access.byteOffset,access.byteSize,1
                region.type=access.type
                block=vs.constantBlocks[access.index]
                if not 0<block.byteSize<=65536: raise ValueError('Unexpected constant size')
                for descriptor in controller.GetDescriptors(access.descriptorStore,[region]):
                    raw=bytes(controller.GetBufferData(descriptor.resource,descriptor.byteOffset,block.byteSize))
                    if len(raw)!=block.byteSize: raise ValueError('Short constant buffer')
                    name='%d-cb%d.bin'%(action.eventId,access.index)
                    with open(os.path.join(output,name),'wb') as target: target.write(raw)
                    row['vertex_constants'].append({'index':access.index,'name':block.name,'file':name,
                                                   'sha256':hashlib.sha256(raw).hexdigest()})
        status=controller.GetFatalErrorStatus()
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        report['complete']=True
    except BaseException:
        report['error']=traceback.format_exc()
    finally:
        if controller: controller.Shutdown()
        cap.Shutdown()
        with open(os.path.join(output,'passes.json'),'w') as target: json.dump(report,target,indent=2)


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'],'passes-launch-error.txt'),'w') as target:
        target.write(traceback.format_exc())
sys.exit(0)
