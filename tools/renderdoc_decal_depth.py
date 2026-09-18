"""Sample moving impact triangle interiors using actual GPU pixel history.

Set AOT_RENDERDOC_PROBE and AOT_DECAL_DEPTH_QUERY. Query fields: geometry (the
inventory JSON basename), name (new output basename), events (up to 48 known
draws). One pixel per triangle is a sparse depth observation, not a coverage test.
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
    root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    with open(os.path.join(root, 'probe.json')) as source: done = json.load(source)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    with open(os.environ['AOT_DECAL_DEPTH_QUERY']) as source: query = json.load(source)
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', query[k]) for k in ('geometry', 'name')):
        raise ValueError('Require simple basenames')
    raw = open(os.path.join(root, query['geometry'] + '.json'), 'rb').read()
    geometry = json.loads(raw)
    with open(os.path.join(root, 'renderdoc-capture.json')) as source:
        path = json.load(source)['captures'][0]['path']
    if (geometry.get('error') or geometry['capture'] != path or
            os.path.normcase(os.path.dirname(path)) != os.path.normcase(root)):
        raise ValueError('Require successful geometry inventory from this capture')
    events = query['events']
    if not 1 <= len(events) <= 48 or len(set(events)) != len(events):
        raise ValueError('Require one to 48 unique draw events')
    draws = {d['event']: d for d in geometry['draws']}
    if any(e not in draws for e in events): raise ValueError('Unknown draw event')
    out = os.path.join(root, query['name'] + '.json')
    if os.path.exists(out): raise ValueError('Require new output name')
    report = {'capture': path, 'query': query, 'geometry_sha256': hashlib.sha256(raw).hexdigest(),
              'complete': False, 'samples': []}
    def save():
        with open(out, 'w') as target: json.dump(report, target, indent=2)
    cap, controller, replacement, original = rd.OpenCaptureFile(), None, None, None
    baseline_vertices = {}
    try:
        status = cap.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        tex = next(t for t in controller.GetTextures() if str(t.resourceId) == geometry['query']['resource'])
        sub = rd.Subresource(); sub.sample = 0
        def vertices(event):
            controller.SetFrameEvent(event, True)
            mesh = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            referenced = sorted(int(i) for i in draws[event]['positions'])
            if referenced != list(range(4)) or mesh.vertexByteStride != 112:
                raise ValueError('Require captured four-vertex, seven-float4 layout')
            data = bytes(controller.GetBufferData(mesh.vertexResourceId, mesh.vertexByteOffset, 448))
            if len(data) != 448: raise ValueError('Short post-VS readback')
            position = b''.join(data[i*112:i*112+16] for i in range(4))
            other = b''.join(data[i*112+16:(i+1)*112] for i in range(4))
            if not all(math.isfinite(v) for v in struct.unpack('<16f', position)):
                raise ValueError('Nonfinite position')
            return position, other
        if 'precise_variant' in query:
            variant_root = os.path.abspath(query['precise_variant'])
            with open(os.path.join(variant_root, 'variants.json')) as source:
                manifest = json.load(source)
            if len(manifest['variants']) != 1: raise ValueError('Require one decal variant')
            variant = manifest['variants'][0]
            if variant['original_sha256'] != 'cb0554cf65e315b18b5e419f5ebc07f0100505631a03a81889516670f76b632d':
                raise ValueError('Wrong original decal shader')
            report['variant'] = variant
            for event in events:
                baseline_vertices[event] = vertices(event)
                pipe = controller.GetPipelineState()
                reflection = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
                if hashlib.sha256(bytes(reflection.rawBytes)).hexdigest() != variant['original_sha256']:
                    raise ValueError('Unexpected original shader')
                if any('ClipDistance' in str(s.systemValue) or 'CullDistance' in str(s.systemValue)
                       for s in reflection.outputSignature):
                    raise ValueError('Experiment requires no user clip/cull outputs')
                rid = pipe.GetShader(rd.ShaderStage.Vertex)
                if original is not None and rid != original: raise ValueError('Multiple original resources')
                original = rid
                checked = False
                for access in controller.GetDescriptorAccess():
                    if access.stage != rd.ShaderStage.Vertex or access.type != rd.DescriptorType.ConstantBuffer:
                        continue
                    if reflection.constantBlocks[access.index].name != 'xe_system_cbuffer': continue
                    region = rd.DescriptorRange()
                    region.offset, region.descriptorSize, region.count = access.byteOffset, access.byteSize, 1
                    region.type = access.type
                    descriptor = controller.GetDescriptors(access.descriptorStore, [region])[0]
                    flags = bytes(controller.GetBufferData(descriptor.resource, descriptor.byteOffset, 4))
                    if len(flags) != 4 or struct.unpack('<I', flags)[0] & 14 != 8:
                        raise ValueError('Unsupported clip-space flags')
                    checked = True
                if not checked: raise ValueError('Missing system constants')
            controller.SetFrameEvent(events[0], True)
            baseline_texture = bytes(controller.GetTextureData(tex.resourceId, sub))
            with open(os.path.join(variant_root, variant['file']), 'rb') as source: code = source.read()
            if hashlib.sha256(code).hexdigest() != variant['sha256']: raise ValueError('Variant hash mismatch')
            replacement, errors = controller.BuildTargetShader('main', rd.ShaderEncoding.DXBC,
                code, rd.ShaderCompileFlags(), rd.ShaderStage.Vertex)
            if replacement == rd.ResourceId.Null() or errors: raise RuntimeError('Shader build failed: ' + errors)
            controller.ReplaceResource(original, replacement)
            report['vertices'] = []
        for event in events:
            d = draws[event]
            if replacement is not None:
                precise = vertices(event)
                if precise[1] != baseline_vertices[event][1]: raise ValueError('Changed material interpolators')
                report['vertices'].append({'event': event, 'interpolators_exact': True,
                    'original_positions': list(struct.unpack('<16f', baseline_vertices[event][0])),
                    'precise_positions': list(struct.unpack('<16f', precise[0]))})
            # The inventory records post-VS adjusted indices as position keys.
            indices = d.get('adjusted_indices', d['indices'])
            for primitive in range(len(indices)//3):
                p = [d['positions'][str(i)] for i in indices[primitive*3:primitive*3+3]]
                x, y = [math.floor(sum(v[k] for v in p)/3) for k in (0, 1)]
                if not (0 <= x < tex.width and 0 <= y < tex.height):
                    raise ValueError('Centroid outside target')
                controller.SetFrameEvent(event, True)
                history = controller.PixelHistory(tex.resourceId, x, y, sub, rd.CompType.Float)
                # Failed draws may carry an unknown primitive ID. Preserve all
                # modifications for this event rather than hiding those failures.
                matches = [h for h in history if h.eventId == event]
                sample = {'event': event, 'primitive': primitive, 'pixel': [x, y],
                          'matches': [{'passed': h.Passed(), 'details': rd.DumpObject(h)} for h in matches]}
                report['samples'].append(sample)
                save()
        if replacement is not None:
            controller.RemoveReplacement(original)
            controller.FreeTargetResource(replacement)
            replacement = None
            for event in events:
                if vertices(event) != baseline_vertices[event]: raise ValueError('Vertex restoration mismatch')
            controller.SetFrameEvent(events[0], True)
            if bytes(controller.GetTextureData(tex.resourceId, sub)) != baseline_texture:
                raise ValueError('HDR restoration mismatch')
            report['restoration_exact'] = True
        status = controller.GetFatalErrorStatus()
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
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
        save()
    return int('error' in report)


sys.exit(main())
