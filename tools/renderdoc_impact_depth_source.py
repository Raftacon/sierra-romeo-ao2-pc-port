"""Compare the rejected impact's copied depth with current/adjacent MSAA sources.

Capture-specific fixture, read-only by default. AOT_IMPACT_DEPTH_VARIANT opts
into a pinned replay-only wall shader replacement. No timing or sample-age
conclusion is assumed; record the source texture and each sampled depth value.
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
    root = os.environ['AOT_RENDERDOC_PROBE']
    with open(os.path.join(root, 'probe.json')) as f: native = json.load(f)
    with open(os.path.join(root, 'aligned-impact-births.json')) as f: births = json.load(f)
    if native['timed_out'] or native['exit_code_before_cleanup'] != 0:
        raise ValueError('Require closed native probe')
    path = births['capture']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(os.path.abspath(root)):
        raise ValueError('Foreign capture')
    name = os.environ.get('AOT_IMPACT_DEPTH_SOURCE_NAME', 'aligned-impact-depth-source')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name): raise ValueError('Invalid output name')
    out = os.path.join(root, name)
    os.mkdir(out)
    report = {'capture': path, 'complete': False, 'sources': [], 'bindings': []}
    cap, controller = rd.OpenCaptureFile(), None
    owned = []
    try:
        status = cap.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        textures = {str(t.resourceId): t for t in controller.GetTextures()}
        variant_dir = os.environ.get('AOT_IMPACT_DEPTH_VARIANT')
        if variant_dir:
            with open(os.path.join(variant_dir, 'variants.json')) as f: variants = json.load(f)['variants']
            if not 1 <= len(variants) <= 2: raise ValueError('Require one or two wall variants')
            for variant in variants:
                event = variant.get('event', 157448)
                if event not in (157448, 158223): raise ValueError('Unexpected replacement event')
                controller.SetFrameEvent(event, True)
                pipe = controller.GetPipelineState()
                code = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
                if hashlib.sha256(code).hexdigest() != variant['original_sha256']:
                    raise ValueError('Unexpected wall replacement source')
                original = pipe.GetShader(rd.ShaderStage.Vertex)
                with open(os.path.join(variant_dir, variant['file']), 'rb') as f: code = f.read()
                if hashlib.sha256(code).hexdigest() != variant['sha256']: raise ValueError('Changed variant')
                replacement, errors = controller.BuildTargetShader('main', rd.ShaderEncoding.DXBC, code, rd.ShaderCompileFlags(), rd.ShaderStage.Vertex)
                if replacement != rd.ResourceId.Null(): owned.append((original, replacement))
                if replacement == rd.ResourceId.Null() or errors: raise RuntimeError('Variant build failed: '+errors)
                controller.ReplaceResource(original, replacement)
            report['variants'] = variants
        controller.SetFrameEvent(159734, True)
        pipe = controller.GetPipelineState()
        ps = pipe.GetShaderReflection(rd.ShaderStage.Pixel)
        code = bytes(ps.rawBytes)
        sha = hashlib.sha256(code).hexdigest()
        report['copy_shader_sha256'] = sha
        with open(os.path.join(out, sha+'.dxbc'), 'wb') as f: f.write(code)
        with open(os.path.join(out, sha+'.txt'), 'w') as f:
            f.write(controller.DisassembleShader(pipe.GetGraphicsPipelineObject(), ps, ''))
        candidates = set()
        for access in controller.GetDescriptorAccess():
            if access.stage != rd.ShaderStage.Pixel or access.type not in (rd.DescriptorType.Image, rd.DescriptorType.ConstantBuffer):
                continue
            region = rd.DescriptorRange()
            region.offset, region.descriptorSize, region.count, region.type = access.byteOffset, access.byteSize, 1, access.type
            for desc in controller.GetDescriptors(access.descriptorStore, [region]):
                row = {'access': rd.DumpObject(access), 'descriptor': rd.DumpObject(desc)}
                report['bindings'].append(row)
                if access.type == rd.DescriptorType.ConstantBuffer:
                    block = ps.constantBlocks[access.index]
                    if not 0 < block.byteSize <= 4096: raise ValueError('Unexpected copy constants')
                    raw = bytes(controller.GetBufferData(desc.resource, desc.byteOffset, block.byteSize))
                    row['constant_hex'] = raw.hex()
                else:
                    tex = textures.get(str(desc.resource))
                    if tex and tex.format.Name() == 'R32_FLOAT' and (tex.width, tex.height, tex.msSamp) == (1280, 720, 1):
                        candidates.add(str(desc.resource))
        if len(candidates) != 1: raise ValueError('Require unique resolved R32 depth input')
        source = textures[candidates.pop()]
        def sample(tex, event, sample_index, label):
            controller.SetFrameEvent(event, True)
            sub = rd.Subresource(); sub.sample = sample_index
            raw = bytes(controller.GetTextureData(tex.resourceId, sub))
            stride = 4 if tex.format.Name() == 'R32_FLOAT' else 8
            if len(raw) != tex.width*tex.height*stride:
                raise ValueError('Unexpected texture packing')
            crop = b''.join(raw[((258+j)*tex.width+618)*stride:((258+j)*tex.width+623)*stride] for j in range(5))
            with open(os.path.join(out, label+'.bin'), 'wb') as f: f.write(crop)
            report['sources'].append({'label': label, 'event': event, 'resource': str(tex.resourceId),
                'sample': sample_index, 'format': tex.format.Name(), 'stride': stride,
                'crop_xywh': [618, 258, 5, 5], 'sha256': hashlib.sha256(crop).hexdigest(),
                'center_depth': struct.unpack_from('<f', crop, (2*5+2)*stride)[0]})
        sample(source, 159734, 0, 'resolved')
        dest = textures['ResourceId::6219']
        if dest.format.Name() != 'D32S8_TYPELESS' or dest.msSamp != 1:
            raise ValueError('Unexpected destination depth')
        sample(dest, 159734, 0, 'copied')
        scene = textures['ResourceId::6024']
        if scene.format.Name() != 'D32S8_TYPELESS' or scene.msSamp != 2:
            raise ValueError('Unexpected scene depth')
        sample(scene, 157979, 1, 'after-scene-restore')
        sample(scene, 158223, 1, 'after-wall-color')
        with open(os.path.join(root, 'gpu-frame_capture.rdc-inventory.json')) as f: inventory = json.load(f)
        if not inventory.get('complete') or inventory['capture'] != path:
            raise ValueError('Require matching completed action inventory')
        for epoch in (18, 19, 20, 21):
            begin, end = births['epochs'][epoch]['start_event'], births['epochs'][epoch+1]['start_event']
            clears = [a['event'] for a in inventory['actions'] if begin <= a['event'] < end and
                      a['depth'] == str(scene.resourceId) and 'ClearDepthStencil' in a['flags']]
            if len(clears) != 4: raise ValueError('Unexpected scene tile clear sequence')
            # Record every completed interval before surface reuse. Do not
            # infer the screen tile from the final draw of the whole epoch.
            for interval, (left, right) in enumerate(zip(clears, clears[1:])):
                events = [a['event'] for a in inventory['actions'] if left < a['event'] < right and
                          a['depth'] == str(scene.resourceId) and 'Drawcall' in a['flags']]
                if not events: raise ValueError('Missing completed scene pass')
                for index in (0, 1):
                    sample(scene, max(events), index, 'epoch-%d-interval-%d-sample-%d' % (epoch, interval, index))
        report['geometry'] = []
        for event, primitive in ((157448, 269), (159821, 0)):
            controller.SetFrameEvent(event, True)
            pipe = controller.GetPipelineState()
            reflection = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
            code = bytes(reflection.rawBytes)
            sha = hashlib.sha256(code).hexdigest()
            with open(os.path.join(out, sha+'.dxbc'), 'wb') as f: f.write(code)
            with open(os.path.join(out, sha+'.txt'), 'w') as f:
                f.write(controller.DisassembleShader(pipe.GetGraphicsPipelineObject(), reflection, ''))
            mesh = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2, 4) or not 16 <= mesh.vertexByteStride <= 256:
                raise ValueError('Unexpected triangle output layout')
            raw = bytes(controller.GetBufferData(mesh.indexResourceId,
                mesh.indexByteOffset+primitive*3*mesh.indexByteStride, 3*mesh.indexByteStride))
            indices = struct.unpack('<3'+('H' if mesh.indexByteStride == 2 else 'I'), raw)
            positions = []
            for index in indices:
                raw = bytes(controller.GetBufferData(mesh.vertexResourceId,
                    mesh.vertexByteOffset+(index+mesh.baseVertex)*mesh.vertexByteStride, 16))
                positions.append(list(struct.unpack('<4f', raw)))
            native = controller.GetD3D12PipelineState()
            row = {'event': event, 'primitive': primitive, 'shader_sha256': sha,
                   'positions': positions, 'viewports': rd.DumpObject(native.rasterizer.viewports),
                   'rasterizer': rd.DumpObject(native.rasterizer.state), 'constants': []}
            report['geometry'].append(row)
            for access in controller.GetDescriptorAccess():
                if access.stage != rd.ShaderStage.Vertex or access.type != rd.DescriptorType.ConstantBuffer:
                    continue
                region = rd.DescriptorRange()
                region.offset, region.descriptorSize, region.count, region.type = access.byteOffset, access.byteSize, 1, access.type
                for desc in controller.GetDescriptors(access.descriptorStore, [region]):
                    block = reflection.constantBlocks[access.index]
                    if not 0 < block.byteSize <= 8192: raise ValueError('Unexpected vertex constants')
                    raw = bytes(controller.GetBufferData(desc.resource, desc.byteOffset, block.byteSize))
                    row['constants'].append({'index': access.index, 'hex': raw.hex()})
        if controller.GetFatalErrorStatus() != rd.ResultCode.Succeeded:
            raise RuntimeError('Fatal replay error')
        report['complete'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if controller:
            for original, replacement in owned:
                controller.RemoveReplacement(original)
                controller.FreeTargetResource(replacement)
            controller.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out, 'source.json'), 'w') as f: json.dump(report, f, indent=2)


main()
sys.exit(0)
