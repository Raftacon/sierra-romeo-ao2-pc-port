"""Inspect GPU post-VS triangles behind the captured equipment depth crossover.

Run in RenderDoc Python with AOT_RENDERDOC_PROBE set to the closed sequence
probe. Output is new equipment-geometry/ geometry.json and bounded raw buffers.
Post-VS extraction must be checked against pixel-history depths before treating
it as evidence of the original draw's geometry.
"""
import hashlib
import json
import math
import os
import struct
import sys
import traceback
import renderdoc as rd


def main():
    directory = os.environ['AOT_RENDERDOC_PROBE']
    with open(os.path.join(directory, 'probe.json')) as source:
        done = json.load(source)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    with open(os.path.join(directory, 'renderdoc-capture.json')) as source:
        path = json.load(source)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(os.path.abspath(directory)):
        raise ValueError('Capture must belong to probe')
    variant_path = os.environ.get('AOT_EQUIPMENT_WORLD_VARIANT')
    variant = None
    if variant_path:
        with open(os.path.join(variant_path, 'variant.json')) as source:
            variant = json.load(source)
        with open(os.path.join(variant_path, 'world-output.dxbc'), 'rb') as source:
            variant_data = source.read()
        if hashlib.sha256(variant_data).hexdigest() != variant['sha256']:
            raise ValueError('Variant hash mismatch')
    output = os.path.join(directory, 'equipment-world-geometry' if variant else 'equipment-geometry')
    os.mkdir(output)
    report = {'capture': path, 'draws': [], 'world_variant': variant}
    capture, controller = rd.OpenCaptureFile(), None
    try:
        status = capture.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        status, controller = capture.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        for event, primitive in [(7428,570), (7456,573), (21488,570), (21516,573)]:
            controller.SetFrameEvent(event, True)
            pipe = controller.GetPipelineState()
            reflection = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
            disassembly = controller.DisassembleShader(pipe.GetGraphicsPipelineObject(), reflection, '')
            if not disassembly.startswith('Shader hash aade8145-90a1f3f4-8033efd9-aab718bc'):
                raise ValueError('Unexpected vertex shader')
            if pipe.GetPrimitiveTopology() != rd.Topology.TriangleList:
                raise ValueError('Require triangle-list topology')
            row = {'event': event, 'primitive': primitive,
                   'rasterizer': rd.DumpObject(controller.GetD3D12PipelineState().rasterizer),
                   'constant_buffers': [], 'vertices': []}
            report['draws'].append(row)
            for access in controller.GetDescriptorAccess():
                if access.stage != rd.ShaderStage.Vertex or access.type != rd.DescriptorType.ConstantBuffer:
                    continue
                region = rd.DescriptorRange()
                region.offset, region.descriptorSize, region.count = access.byteOffset, access.byteSize, 1
                region.type = access.type
                block = reflection.constantBlocks[access.index]
                if not 0 < block.byteSize <= 65536:
                    raise ValueError('Unexpected constant-buffer size')
                for descriptor in controller.GetDescriptors(access.descriptorStore, [region]):
                    data = bytes(controller.GetBufferData(descriptor.resource, descriptor.byteOffset, block.byteSize))
                    if len(data) != block.byteSize:
                        raise ValueError('Short constant buffer')
                    name = '%d-cb%d.bin' % (event, access.index)
                    with open(os.path.join(output, name), 'wb') as target:
                        target.write(data)
                    row['constant_buffers'].append({'index': access.index, 'name': block.name,
                        'file': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            def triangle():
                mesh = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
                if (mesh.vertexResourceId == rd.ResourceId.Null() or mesh.indexByteStride not in (2,4)
                        or mesh.numIndices < (primitive+1)*3 or not 16 <= mesh.vertexByteStride <= 1024):
                    raise ValueError('Unexpected indexed post-VS mesh')
                indices = bytes(controller.GetBufferData(mesh.indexResourceId,
                    mesh.indexByteOffset+primitive*3*mesh.indexByteStride, 3*mesh.indexByteStride))
                vertices = []
                for index in struct.unpack('<3'+('H' if mesh.indexByteStride==2 else 'I'), indices):
                    index += mesh.baseVertex
                    if not 0 <= index < 100000:
                        raise ValueError('Unexpected vertex index')
                    data = bytes(controller.GetBufferData(mesh.vertexResourceId,
                        mesh.vertexByteOffset+index*mesh.vertexByteStride, mesh.vertexByteStride))
                    if len(data) != mesh.vertexByteStride:
                        raise ValueError('Short post-VS vertex')
                    position = struct.unpack_from('<4f', data)
                    if not all(math.isfinite(v) for v in position) or position[3] == 0:
                        raise ValueError('Invalid output position')
                    vertices.append({'index': index, 'clip_position': position, 'raw_hex': data.hex()})
                return rd.DumpObject(mesh), vertices
            row['mesh'], row['vertices'] = triangle()
            if variant:
                if hashlib.sha256(bytes(reflection.rawBytes)).hexdigest() != variant['original_sha256']:
                    raise ValueError('Original VS hash mismatch')
                original = pipe.GetShader(rd.ShaderStage.Vertex)
                replacement, errors = controller.BuildTargetShader('main',rd.ShaderEncoding.DXBC,
                    variant_data,rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
                if replacement == rd.ResourceId.Null():
                    raise RuntimeError('Variant build failed: '+errors)
                try:
                    controller.ReplaceResource(original, replacement)
                    controller.SetFrameEvent(event, True)
                    row['world_mesh'], row['world_vertices'] = triangle()
                    row['compiler_messages'] = errors
                    for before, after in zip(row['vertices'],row['world_vertices']):
                        if before['index'] != after['index'] or before['raw_hex'][32:] != after['raw_hex'][32:]:
                            raise ValueError('World variant changed non-position outputs')
                finally:
                    controller.RemoveReplacement(original)
                    controller.FreeTargetResource(replacement)
                controller.SetFrameEvent(event, True)
                _, restored = triangle()
                if restored != row['vertices']:
                    raise ValueError('Restoration changed post-VS output')
                row['restoration_exact'] = True
        status = controller.GetFatalErrorStatus()
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if controller is not None:
            controller.Shutdown()
        capture.Shutdown()
        with open(os.path.join(output, 'geometry.json'), 'w') as target:
            json.dump(report, target, indent=2)


try:
    main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'geometry-launch-error.txt'), 'w') as target:
        target.write(traceback.format_exc())
sys.exit(0)
