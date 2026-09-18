"""GPU pixel history and captured shader inputs, without shader simulation.

Run using qrenderdoc --python with AOT_RENDERDOC_PROBE and
AOT_RENDERDOC_PIXEL_QUERY (an absolute JSON query filename). The query names
a resource, last event, pixel coordinates, and a new output name.
"""
import json
import hashlib
import os
import re
import struct
import sys
import traceback

import renderdoc as rd


def main():
    directory = os.environ['AOT_RENDERDOC_PROBE']
    with open(os.path.join(directory, 'probe.json')) as source:
        completed = json.load(source)
    if completed.get('timed_out') or completed.get('exit_code_before_cleanup') != 0:
        raise RuntimeError('Require a normally closed native probe before GPU replay')
    with open(os.environ['AOT_RENDERDOC_PIXEL_QUERY']) as source:
        query = json.load(source)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', query['name']):
        raise RuntimeError('Use a simple output name')
    output = os.path.join(directory, query['name'] + '.json')
    if os.path.exists(output) or os.path.exists(os.path.join(directory, query['name'] + '.png')):
        raise RuntimeError('Use a new output name')
    with open(os.path.join(directory, 'renderdoc-capture.json')) as source:
        path = json.load(source)['captures'][query.get('capture_index', 0)]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(os.path.abspath(directory)):
        raise RuntimeError('Capture must belong to this probe')
    report = {'capture': path, 'query': query, 'pixels': [], 'complete': False}
    capture, controller = rd.OpenCaptureFile(), None
    try:
        status = capture.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        status, controller = capture.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        def last_event(actions):
            return max([0] + [max(a.eventId, last_event(a.children)) for a in actions])
        if not isinstance(query['event'], int) or not 0 < query['event'] <= last_event(controller.GetRootActions()):
            raise RuntimeError('Requested event is outside the capture')
        first_event = query.get('first_event', 0)
        if not isinstance(first_event, int) or not 0 <= first_event <= query['event']:
            raise RuntimeError('Invalid history event interval')
        texture = next(t for t in controller.GetTextures() if str(t.resourceId) == query['resource'])
        sample = query.get('sample', 0)
        if not isinstance(sample, int) or not 0 <= sample < max(1, texture.msSamp):
            raise RuntimeError('Requested sample is outside the texture')
        report['texture'] = {'width': texture.width, 'height': texture.height,
                             'format': texture.format.Name(), 'samples': texture.msSamp}
        report['history_subresource'] = {'mip': 0, 'slice': 0, 'sample': sample}
        history_subresource = rd.Subresource()
        history_subresource.sample = sample
        if not 1 <= len(query['pixels']) <= 8 or not isinstance(query['event'], int) or query['event'] <= 0:
            raise RuntimeError('Require a positive event and one to eight pixels')
        for x, y in query['pixels']:
            if not isinstance(x, int) or not isinstance(y, int) or not (0 <= x < texture.width and 0 <= y < texture.height):
                raise RuntimeError('Pixel is outside the texture')
            controller.SetFrameEvent(query['event'], True)
            history = controller.PixelHistory(texture.resourceId, x, y, history_subresource, rd.CompType.Float)
            pixel = {'x': x, 'y': y, 'history': []}
            report['pixels'].append(pixel)
            for h in history:
                if h.eventId < first_event or h.eventId > query['event']:
                    continue
                item = {'event': h.eventId, 'primitive': h.primitiveID,
                        'passed': h.Passed(), 'details': rd.DumpObject(h)}
                pixel['history'].append(item)
                if not h.Passed() or h.unboundPS:
                    continue
                controller.SetFrameEvent(h.eventId, True)
                pipe = controller.GetPipelineState()
                item['vs'] = str(pipe.GetShader(rd.ShaderStage.Vertex))
                item['ps'] = str(pipe.GetShader(rd.ShaderStage.Pixel))
                if h.eventId not in query.get('input_events', []):
                    continue
                if controller.GetAPIProperties().pipelineType == rd.GraphicsAPI.D3D12:
                    native = controller.GetD3D12PipelineState()
                    item['rasterizer'] = rd.DumpObject(native.rasterizer.state)
                    item['depth_stencil'] = rd.DumpObject(native.outputMerger.depthStencilState)
                    item['blend'] = rd.DumpObject(native.outputMerger.blendState)
                item['shaders'] = {}
                for stage in (rd.ShaderStage.Vertex, rd.ShaderStage.Pixel):
                    reflection = pipe.GetShaderReflection(stage)
                    disassembly = controller.DisassembleShader(pipe.GetGraphicsPipelineObject(), reflection, '')
                    filename = f"{query['name']}-{h.eventId}-{stage.name}.txt"
                    with open(os.path.join(directory, filename), 'w', encoding='utf-8') as out:
                        out.write(disassembly)
                    binary = bytes(reflection.rawBytes)
                    binary_name = f"{query['name']}-{h.eventId}-{stage.name}.dxbc"
                    with open(os.path.join(directory, binary_name), 'wb') as out:
                        out.write(binary)
                    item['shaders'][stage.name] = {'file': filename,
                        'header': disassembly.splitlines()[0] if disassembly else '',
                        'binary_file': binary_name, 'sha256': hashlib.sha256(binary).hexdigest()}
                item['descriptor_access'] = [rd.DumpObject(a) for a in controller.GetDescriptorAccess()]
                item['bindings'] = []
                for access in controller.GetDescriptorAccess():
                    if access.stage != rd.ShaderStage.Pixel or access.type not in (rd.DescriptorType.Image, rd.DescriptorType.Sampler, rd.DescriptorType.ConstantBuffer):
                        continue
                    region = rd.DescriptorRange()
                    region.offset, region.descriptorSize, region.count = access.byteOffset, access.byteSize, 1
                    region.type = access.type
                    if access.type == rd.DescriptorType.Sampler:
                        descriptors = controller.GetSamplerDescriptors(access.descriptorStore, [region])
                    else:
                        descriptors = controller.GetDescriptors(access.descriptorStore, [region])
                    for descriptor in descriptors:
                        binding = {'access': rd.DumpObject(access), 'descriptor': rd.DumpObject(descriptor)}
                        item['bindings'].append(binding)
                        if access.type == rd.DescriptorType.ConstantBuffer:
                            block = pipe.GetShaderReflection(rd.ShaderStage.Pixel).constantBlocks[access.index]
                            raw = controller.GetBufferData(descriptor.resource, descriptor.byteOffset, min(64, block.byteSize))
                            binding['first_u32'] = list(struct.unpack('<' + 'I' * (len(raw) // 4), bytes(raw)))
                        if access.type == rd.DescriptorType.Image and query.get('hash_textures', False):
                            tex = next(t for t in controller.GetTextures() if t.resourceId == descriptor.resource)
                            if tex.width * tex.height * max(1, tex.depth) * 16 > 64 * 1024 * 1024:
                                binding['hash_skipped'] = 'Texture exceeds conservative 64 MiB per-subresource limit'
                                continue
                            binding['mips_slice_zero'] = []
                            preview = rd.TextureSave()
                            preview.resourceId, preview.destType = tex.resourceId, rd.FileType.PNG
                            preview_name = f"{query['name']}-{h.eventId}-texture-{access.arrayElement}.png"
                            binding['preview'] = {'file': preview_name,
                                'result': str(controller.SaveTexture(preview, os.path.join(directory, preview_name)))}
                            for mip in range(min(tex.mips, 16)):
                                subresource = rd.Subresource()
                                subresource.mip = mip
                                data = controller.GetTextureData(tex.resourceId, subresource)
                                binding['mips_slice_zero'].append({'mip': mip, 'bytes': len(data),
                                    'sha256': hashlib.sha256(bytes(data)).hexdigest()})
                inputs = rd.DebugPixelInputs()
                inputs.primitive = h.primitiveID
                inputs.sample = sample
                inputs.view = rd.ReplayController.NoPreference
                trace = controller.DebugPixel(x, y, inputs)
                try:
                    # Input extraction runs on the GPU. Do not ContinueDebug:
                    # the translated switch/loop currently misbehaves in the
                    # RenderDoc 1.46 shader interpreter for the car shader.
                    item['inputs'] = [{'name': v.name, 'bits': list(v.value.u32v)[:4]}
                                      for v in trace.inputs]
                    item['constants'] = [rd.DumpObject(block) for block in trace.constantBlocks]
                finally:
                    controller.FreeTrace(trace)
        controller.SetFrameEvent(query['event'], True)
        save = rd.TextureSave()
        save.resourceId, save.destType = texture.resourceId, rd.FileType.PNG
        report['image_result'] = str(controller.SaveTexture(save, os.path.join(directory, query['name'] + '.png')))
        status = controller.GetFatalErrorStatus()
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        report['complete'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if controller is not None:
            controller.Shutdown()
        capture.Shutdown()
        with open(output, 'w', encoding='utf-8') as out:
            json.dump(report, out, indent=2)


try:
    main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'renderdoc-pixel-error.txt'), 'w') as out:
        out.write(traceback.format_exc())
sys.exit(0)
