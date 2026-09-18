"""Inventory known impact triangles during a closed native GPU capture.

RenderDoc Python: AOT_RENDERDOC_PROBE names the probe; AOT_DECAL_SEQUENCE_QUERY
names a JSON file with a target resource and a new simple output name. Shader
identity is checked before extracting triangles. This inventories geometry, not
texture coverage or temporal visual parity.
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
    directory = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    with open(os.path.join(directory, 'probe.json')) as source:
        done = json.load(source)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    with open(os.environ['AOT_DECAL_SEQUENCE_QUERY']) as source:
        query = json.load(source)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', query['name']):
        raise ValueError('Require simple output name')
    vertex_sha256 = query.get('vertex_sha256')
    pixel_sha256 = query.get('pixel_sha256')
    if vertex_sha256 is not None and not re.fullmatch(r'[a-f0-9]{64}', vertex_sha256):
        raise ValueError('Require an exact lowercase vertex shader SHA-256')
    if pixel_sha256 is not None and not re.fullmatch(r'[a-f0-9]{64}', pixel_sha256):
        raise ValueError('Require an exact lowercase pixel shader SHA-256')
    index_counts = query.get('index_counts', [6, 12])
    if (not 1 <= len(index_counts) <= 4 or
            any(type(n) is not int or not 3 <= n <= 4096 or n % 3 for n in index_counts) or
            ('index_counts' in query and (not vertex_sha256 or not pixel_sha256))):
        raise ValueError('Require bounded triangle counts and exact shader identities for custom geometry')
    with open(os.path.join(directory, 'renderdoc-capture.json')) as source:
        path = json.load(source)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(directory):
        raise ValueError('Capture must belong to probe')
    output = os.path.join(directory, query['name'] + '.json')
    if os.path.exists(output):
        raise ValueError('Require new output name')
    report = {'capture': path, 'query': query, 'draws': []}
    cap, controller = rd.OpenCaptureFile(), None
    try:
        status = cap.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        texture = next(t for t in controller.GetTextures() if str(t.resourceId) == query['resource'])
        if texture.format.Name() != 'R16G16B16A16_FLOAT' or texture.msSamp != 1:
            raise ValueError('Require single-sample HDR target')
        candidates = []
        def visit(actions):
            for a in actions:
                if (a.flags & rd.ActionFlags.Indexed and a.flags & rd.ActionFlags.Drawcall
                        and a.numIndices in index_counts and a.outputs[0] == texture.resourceId):
                    candidates.append(a)
                visit(a.children)
        visit(controller.GetRootActions())
        if not 1 <= len(candidates) <= 1000: raise ValueError('Unbounded candidate count')
        report['candidate_count'] = len(candidates)
        if 'events' in query:
            events = query['events']
            if not 1 <= len(events) <= 96 or len(set(events)) != len(events):
                raise ValueError('Require one to 96 unique requested events')
            candidates = [a for a in candidates if a.eventId in events]
            if len(candidates) != len(events): raise ValueError('Unknown requested candidate')
        shaders = {}
        for a in candidates:
            controller.SetFrameEvent(a.eventId, True)
            pipe = controller.GetPipelineState()
            matched = True
            identity = {}
            for stage, expected in ((rd.ShaderStage.Vertex, 'ebf82099-420f4ecc-70cc92f2-f82ad5a8'),
                                    (rd.ShaderStage.Pixel, '2f7dba27-a6fef522-2f67df5c-10fe35f1')):
                rid = str(pipe.GetShader(stage))
                if rid not in shaders:
                    reflection = pipe.GetShaderReflection(stage)
                    dis = controller.DisassembleShader(pipe.GetGraphicsPipelineObject(), reflection, '')
                    shaders[rid] = {'header': dis.splitlines()[0],
                                    'sha256': hashlib.sha256(bytes(reflection.rawBytes)).hexdigest()}
                identity[stage.name] = shaders[rid]
                expected_sha256 = vertex_sha256 if stage == rd.ShaderStage.Vertex else pixel_sha256
                expected_match = (shaders[rid]['sha256'] == expected_sha256
                                  if expected_sha256 is not None
                                  else shaders[rid]['header'] == 'Shader hash ' + expected)
                if not expected_match:
                    matched = False
                    break
            if not matched: continue
            if query.get('export_vertex_shader', False):
                raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
                filename = query['name'] + '-' + identity['Vertex']['sha256'] + '.dxbc'
                with open(os.path.join(directory, filename), 'wb') as target: target.write(raw)
                identity['Vertex']['file'] = filename
            if pipe.GetPrimitiveTopology() != rd.Topology.TriangleList:
                raise ValueError('Require indexed triangle list')
            mesh = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2, 4) or not 16 <= mesh.vertexByteStride <= 256:
                raise ValueError('Unexpected post-VS layout')
            ib = bytes(controller.GetBufferData(mesh.indexResourceId, mesh.indexByteOffset,
                                               a.numIndices * mesh.indexByteStride))
            indices = struct.unpack('<' + str(a.numIndices) + ('H' if mesh.indexByteStride == 2 else 'I'), ib)
            referenced = sorted(set(i + mesh.baseVertex for i in indices))
            if referenced[0] < 0 or referenced[-1] > 65535: raise ValueError('Unexpected mesh span')
            vb = bytes(controller.GetBufferData(mesh.vertexResourceId,
                mesh.vertexByteOffset + referenced[0] * mesh.vertexByteStride,
                (referenced[-1] - referenced[0] + 1) * mesh.vertexByteStride))
            if len(vb) != (referenced[-1] - referenced[0] + 1) * mesh.vertexByteStride:
                raise ValueError('Short post-VS vertex readback')
            vp = pipe.GetViewport(0)
            positions = {}
            for index in referenced:
                p = struct.unpack_from('<4f', vb, (index - referenced[0]) * mesh.vertexByteStride)
                if not all(math.isfinite(v) for v in p) or p[3] <= 0:
                    raise ValueError('Require finite unclipped front-facing positions')
                positions[index] = [vp.x + (p[0]/p[3] + 1)*vp.width/2,
                                    vp.y + (1-p[1]/p[3])*vp.height/2,
                                    vp.minDepth + p[2]/p[3]*(vp.maxDepth-vp.minDepth)]
            native = controller.GetD3D12PipelineState()
            item = {'event': a.eventId, 'indices': list(indices), 'shaders': identity,
                    'adjusted_indices': [i + mesh.baseVertex for i in indices],
                    'viewport': rd.DumpObject(vp), 'positions': positions,
                    'rasterizer': rd.DumpObject(native.rasterizer.state),
                    'depth_stencil': rd.DumpObject(native.outputMerger.depthStencilState)}
            report['draws'].append(item)
        if not report['draws']: raise ValueError('No matching impact shader draws')
        if 'events' in query and len(report['draws']) != len(query['events']):
            raise ValueError('Requested event has a different shader')
        status = controller.GetFatalErrorStatus()
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if controller is not None: controller.Shutdown()
        cap.Shutdown()
        with open(output, 'w') as target: json.dump(report, target, indent=2)
    return int('error' in report)


sys.exit(main())
