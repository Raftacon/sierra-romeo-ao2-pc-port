"""Read a bounded HDR impact region at explicitly identified material events."""
import hashlib
import json
import os
import re
import math
import struct
import sys
import traceback
import renderdoc as rd


def main():
    root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    with open(os.path.join(root, 'probe.json')) as f:
        native = json.load(f)
    if native.get('timed_out') or native.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    with open(os.environ['AOT_IMPACT_COLOR_QUERY']) as f:
        query = json.load(f)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', query['name']):
        raise ValueError('Require simple new output name')
    if not 1 <= len(query['events']) <= 120 or len(set(query['events'])) != len(query['events']):
        raise ValueError('Require 1-120 unique events')
    for key in ('vertex_sha256', 'pixel_sha256'):
        if not re.fullmatch(r'[a-f0-9]{64}', query[key]):
            raise ValueError('Require exact shader identities')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f:
        path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root):
        raise ValueError('Capture must belong to probe')
    output = os.path.join(root, query['name'])
    os.mkdir(output)
    report = {'capture': path, 'query': query, 'complete': False, 'frames': []}
    cap, controller, original, replacement = rd.OpenCaptureFile(), None, None, None
    try:
        status = cap.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        status, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        tex = next(t for t in controller.GetTextures() if str(t.resourceId) == query['resource'])
        if tex.format.Name() != 'R16G16B16A16_FLOAT' or tex.msSamp != 1:
            raise ValueError('Require single-sample float16 HDR texture')
        x, y, width, height = query['crop']
        if (any(not isinstance(v, int) for v in query['crop']) or min(x, y) < 0 or
                min(width, height) <= 0 or width * height > 512 * 512 or
                x + width > tex.width or y + height > tex.height):
            raise ValueError('Require bounded crop inside HDR texture')
        sub = rd.Subresource()
        sub.sample = 0
        def read_crop():
            data = bytes(controller.GetTextureData(tex.resourceId, sub))
            if len(data) != tex.width * tex.height * 8:
                raise ValueError('Unexpected HDR readback layout')
            return b''.join(data[((y + row) * tex.width + x) * 8:
                                ((y + row) * tex.width + x + width) * 8] for row in range(height))
        baseline_colors, baseline_vertices, geometry = {}, {}, {}
        def vertices(event):
            controller.SetFrameEvent(event, True)
            mesh = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            indices = sorted(int(i) for i in geometry[event]['positions'])
            if indices != list(range(len(indices))) or not 1 <= len(indices) <= 256 or not 16 <= mesh.vertexByteStride <= 256:
                raise ValueError('Unexpected verification vertex span')
            data = bytes(controller.GetBufferData(mesh.vertexResourceId, mesh.vertexByteOffset,
                                                 len(indices) * mesh.vertexByteStride))
            if len(data) != len(indices) * mesh.vertexByteStride:
                raise ValueError('Short vertex readback')
            position = b''.join(data[i * mesh.vertexByteStride:i * mesh.vertexByteStride + 16] for i in indices)
            other = b''.join(data[i * mesh.vertexByteStride + 16:(i + 1) * mesh.vertexByteStride] for i in indices)
            if not all(math.isfinite(v) for v in struct.unpack('<' + str(len(position) // 4) + 'f', position)):
                raise ValueError('Nonfinite projected position')
            return position, other
        replacement_stage = rd.ShaderStage.Vertex
        if 'variant' in query:
            stage_name = query.get('variant_stage', 'Vertex')
            if stage_name not in ('Vertex', 'Pixel'):
                raise ValueError('Unsupported replacement stage')
            replacement_stage = getattr(rd.ShaderStage, stage_name)
            identity_key = 'vertex_sha256' if stage_name == 'Vertex' else 'pixel_sha256'
            for key in ('variant', 'geometry', 'baseline'):
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', query[key]):
                    raise ValueError('Require local artifact names for projection experiment')
            with open(os.path.join(root, query['geometry'] + '.json')) as f:
                inventory = json.load(f)
            if inventory.get('error') or inventory['capture'] != path:
                raise ValueError('Require geometry from this capture')
            geometry = {d['event']: d for d in inventory['draws']}
            verification = query.get('vertex_events', query['events'])
            if not 1 <= len(verification) <= 96 or len(set(verification)) != len(verification) or any(e not in geometry for e in verification):
                raise ValueError('Require 1-96 known vertex events')
            with open(os.path.join(root, query['variant'], 'variants.json')) as f:
                manifest = json.load(f)
            if len(manifest['variants']) != 1:
                raise ValueError('Require one projection variant')
            variant = manifest['variants'][0]
            if variant['original_sha256'] != query[identity_key]:
                raise ValueError('Wrong original shader')
            for event in sorted(set(verification + query['events'])):
                controller.SetFrameEvent(event, True)
                pipe = controller.GetPipelineState()
                reflection = pipe.GetShaderReflection(replacement_stage)
                if hashlib.sha256(bytes(reflection.rawBytes)).hexdigest() != query[identity_key]:
                    raise ValueError('Unexpected original shader')
                if stage_name == 'Vertex' and any('ClipDistance' in str(s.systemValue) or 'CullDistance' in str(s.systemValue) for s in reflection.outputSignature):
                    raise ValueError('Projection experiment requires no user clip/cull outputs')
                rid = pipe.GetShader(replacement_stage)
                if original is not None and rid != original:
                    raise ValueError('Require one original shader resource')
                original = rid
                checked = stage_name == 'Pixel'
                for access in controller.GetDescriptorAccess() if stage_name == 'Vertex' else []:
                    if access.stage != rd.ShaderStage.Vertex or access.type != rd.DescriptorType.ConstantBuffer:
                        continue
                    if reflection.constantBlocks[access.index].name != 'xe_system_cbuffer':
                        continue
                    region = rd.DescriptorRange()
                    region.offset, region.descriptorSize, region.count = access.byteOffset, access.byteSize, 1
                    region.type = access.type
                    descriptor = controller.GetDescriptors(access.descriptorStore, [region])[0]
                    flags = bytes(controller.GetBufferData(descriptor.resource, descriptor.byteOffset, 4))
                    if len(flags) != 4 or struct.unpack('<I', flags)[0] & 14 != 8:
                        raise ValueError('Unsupported clip-space flags')
                    checked = True
                if not checked:
                    raise ValueError('Missing system constants')
                if event in verification:
                    baseline_vertices[event] = vertices(event)
            with open(os.path.join(root, query['baseline'], 'color.json')) as f:
                baseline = json.load(f)
            if not baseline.get('complete') or baseline['capture'] != path or baseline['query']['crop'] != query['crop']:
                raise ValueError('Require matching original color capture')
            hashes = {f['event']: f['sha256'] for f in baseline['frames']}
            for event in query['events']:
                controller.SetFrameEvent(event, True)
                baseline_colors[event] = read_crop()
                if hashlib.sha256(baseline_colors[event]).hexdigest() != hashes.get(event):
                    raise ValueError('Original color did not reproduce')
            with open(os.path.join(root, query['variant'], variant['file']), 'rb') as f:
                code = f.read()
            if hashlib.sha256(code).hexdigest() != variant['sha256']:
                raise ValueError('Variant hash mismatch')
            replacement, errors = controller.BuildTargetShader('main', rd.ShaderEncoding.DXBC, code,
                                                               rd.ShaderCompileFlags(), replacement_stage)
            if replacement == rd.ResourceId.Null() or errors:
                raise RuntimeError('Replacement shader build failed: ' + errors)
            controller.ReplaceResource(original, replacement)
            report['variant'] = variant
            report['vertices'] = []
            for event, before in baseline_vertices.items():
                after = vertices(event)
                if before[1] != after[1]:
                    raise ValueError('Projection experiment changed material interpolators')
                if stage_name == 'Pixel' and before[0] != after[0]:
                    raise ValueError('Pixel experiment changed projected vertices')
                report['vertices'].append({'event': event, 'interpolators_exact': True,
                                           'positions_changed': before[0] != after[0]})
        seen = {}
        for event in query['events']:
            controller.SetFrameEvent(event, True)
            pipe = controller.GetPipelineState()
            if pipe.GetOutputTargets()[0].resource != tex.resourceId:
                raise ValueError('Event output differs from requested target')
            for stage, key in ((rd.ShaderStage.Vertex, 'vertex_sha256'), (rd.ShaderStage.Pixel, 'pixel_sha256')):
                if replacement is not None and stage == replacement_stage:
                    continue  # Original shader and active replacement were verified above.
                shader = str(pipe.GetShader(stage))
                if shader not in seen:
                    seen[shader] = hashlib.sha256(bytes(pipe.GetShaderReflection(stage).rawBytes)).hexdigest()
                if seen[shader] != query[key]:
                    raise ValueError('Event shader identity mismatch')
            crop = read_crop()
            name = 'event-%d.rgba16f' % event
            with open(os.path.join(output, name), 'wb') as f:
                f.write(crop)
            report['frames'].append({'event': event, 'file': name, 'bytes': len(crop),
                                     'sha256': hashlib.sha256(crop).hexdigest()})
        if replacement is not None:
            controller.RemoveReplacement(original)
            controller.FreeTargetResource(replacement)
            replacement = None
            for event, before in baseline_vertices.items():
                if vertices(event) != before:
                    raise ValueError('Vertex restoration mismatch')
            for event, before in baseline_colors.items():
                controller.SetFrameEvent(event, True)
                if read_crop() != before:
                    raise ValueError('Color restoration mismatch')
            report['restoration_exact'] = True
        status = controller.GetFatalErrorStatus()
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        report['complete'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if controller is not None:
            if replacement is not None and replacement != rd.ResourceId.Null():
                controller.RemoveReplacement(original)
                controller.FreeTargetResource(replacement)
            controller.Shutdown()
        cap.Shutdown()
        with open(os.path.join(output, 'color.json'), 'w') as f:
            json.dump(report, f, indent=2)
    return int('error' in report)


sys.exit(main())
