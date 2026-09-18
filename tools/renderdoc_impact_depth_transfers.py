"""Trace the captured wall-depth restore operations and their bound resources.

Read-only fixture for impact-creation-001; requires a normally closed probe.
"""
import hashlib
import json
import os
import sys
import traceback
import renderdoc as rd


def main():
    root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    with open(os.path.join(root, 'probe.json')) as f: native = json.load(f)
    with open(os.path.join(root, 'aligned-impact-births.json')) as f: births = json.load(f)
    if native.get('timed_out') or native.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require a normally closed native probe')
    path = births['capture']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root): raise ValueError('Foreign capture')
    out = os.path.join(root, 'aligned-depth-transfers')
    os.mkdir(out)
    report = {'capture': path, 'complete': False, 'events': [], 'uses': {}}
    cap, controller = rd.OpenCaptureFile(), None
    try:
        status = cap.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        textures = {str(t.resourceId): t for t in controller.GetTextures()}
        for resource in ('ResourceId::6024', 'ResourceId::6058', 'ResourceId::6247'):
            report['uses'][resource] = [{'event': u.eventId, 'usage': str(u.usage)}
                for u in controller.GetUsage(textures[resource].resourceId) if 157270 <= u.eventId <= 159734]
        for event in (157850, 157929, 157937, 157950, 157979, 158223):
            controller.SetFrameEvent(event, True)
            pipe = controller.GetPipelineState()
            row = {'event': event, 'shaders': {}, 'bindings': []}
            report['events'].append(row)
            for stage in (rd.ShaderStage.Vertex, rd.ShaderStage.Pixel, rd.ShaderStage.Compute):
                if (event == 157937) != (stage == rd.ShaderStage.Compute): continue
                reflection = pipe.GetShaderReflection(stage)
                if reflection is None: continue
                raw = bytes(reflection.rawBytes); sha = hashlib.sha256(raw).hexdigest()
                row['shaders'][stage.name] = sha
                with open(os.path.join(out, sha+'.dxbc'), 'wb') as f: f.write(raw)
                with open(os.path.join(out, sha+'.txt'), 'w') as f:
                    f.write(controller.DisassembleShader(pipe.GetComputePipelineObject() if stage == rd.ShaderStage.Compute
                                                        else pipe.GetGraphicsPipelineObject(), reflection, ''))
            row['depth_target'] = str(pipe.GetDepthTarget().resource)
            for access in controller.GetDescriptorAccess():
                if access.type not in (rd.DescriptorType.Image, rd.DescriptorType.ReadWriteImage,
                        rd.DescriptorType.Buffer, rd.DescriptorType.ReadWriteBuffer, rd.DescriptorType.ConstantBuffer): continue
                region = rd.DescriptorRange()
                region.offset, region.descriptorSize, region.count, region.type = access.byteOffset, access.byteSize, 1, access.type
                for desc in controller.GetDescriptors(access.descriptorStore, [region]):
                    binding = {'access': rd.DumpObject(access), 'descriptor': rd.DumpObject(desc)}
                    row['bindings'].append(binding)
                    if access.type == rd.DescriptorType.ConstantBuffer:
                        block = pipe.GetShaderReflection(access.stage).constantBlocks[access.index]
                        if not 0 < block.byteSize <= 16384: raise ValueError('Unexpected constant size')
                        raw = bytes(controller.GetBufferData(desc.resource, desc.byteOffset, block.byteSize))
                        binding['constant_hex'] = raw.hex()
            row['depth_state'] = rd.DumpObject(controller.GetD3D12PipelineState().outputMerger.depthStencilState)
        if controller.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('Fatal replay error')
        report['complete'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if controller: controller.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out, 'transfers.json'), 'w') as f: json.dump(report, f, indent=2)
    return int(not report['complete'])


sys.exit(main())
