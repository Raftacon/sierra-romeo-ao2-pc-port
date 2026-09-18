"""Inspect visible remaining static material families in the closed 04_05 fixture.

Records all referenced vertices and bounded triangle-centroid pixel histories.
Index matches and visible coverage alone do not establish correct projection.
RenderDoc Python: AOT_RENDERDOC_PROBE; optionally AOT_STATIC_PAIR_KIND=lighting.
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
    with open(os.path.join(root, 'probe.json')) as f: done = json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0: raise ValueError('Require closed native capture')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f: path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root): raise ValueError('Foreign capture')
    kind = os.environ.get('AOT_STATIC_PAIR_KIND', 'material')
    if kind not in ('material', 'lighting', 'partner', 'neighbor'): raise ValueError('Unknown pair kind')
    events = (5097,8969,4879,8311,16293) if kind == 'material' else (5097,16177,16548,4140,16513)
    expected = ({8969:'4a985b920fa13d0d3b6c6e0b90750e8107b11268dfc4d4a6e12ef689b2d627f5',
                 16293:'8c8c8b9c2e799d11e1bb2dbecb3bcafd3245ff63a376ace03745debeaa527df5'} if kind == 'material' else
                {16548:'fc77ef6b3cabdbf165fbf6798c8b78977656d0eed4191c3d8b3bbfb67912415f',
                 16513:'c1fa2fb250194fd8d206dac24b81466f04746a7c3d67f4a90a9ef08cd99340ec'})
    if kind == 'partner':
        events = (4771,7962,1298,1310,1373,4737,8933,5196,9119)
        expected = {e: '01389226d3e7818ce46af7291ddc362aad8d83513be5e6c7d058f4ffe2eeacb0' for e in (7962,1298,1310,1373)}
        expected.update({8933: 'd4aed78629d55c0c164a1716344d7edcfd530163db9f3d6c9a1f8f7423454963',
                         9119: 'c2ac7862362afe9c8df6e2c2d72201e955d41717f8f22a14a89809c589f08743'})
    if kind == 'neighbor':
        events = (4870,6961)
        expected = {6961:'b81e7f67bf18bdaacfd9927686149a96a2da0493e469d647c23e99d53edc0064'}
    name = os.environ.get('AOT_STATIC_PAIR_NAME', {'material':'static-visible-pairs-001', 'lighting':'static-lighting-pairs-001', 'partner':'static-partner-pairs-001', 'neighbor':'static-neighbor-pairs-001'}[kind])
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name): raise ValueError('Invalid output name')
    out = os.path.join(root, name); os.mkdir(out)
    report = {'capture': path, 'draws': [], 'pixels': [], 'complete': False}
    cap, ctl = rd.OpenCaptureFile(), None
    try:
        assert cap.OpenFile(path, '', None) == rd.ResultCode.Succeeded
        status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        textures = {str(t.resourceId): t for t in ctl.GetTextures()}
        for event in events:
            ctl.SetFrameEvent(event, True); pipe = ctl.GetPipelineState()
            row = {'event': event, 'viewport': rd.DumpObject(pipe.GetViewport(0)),
                   'ib': rd.DumpObject(pipe.GetIBuffer()), 'shaders': {},
                   'state': rd.DumpObject(ctl.GetD3D12PipelineState().outputMerger)}
            report['draws'].append(row)
            for stage in (rd.ShaderStage.Vertex, rd.ShaderStage.Pixel):
                reflection = pipe.GetShaderReflection(stage)
                if reflection is None: continue
                raw = bytes(reflection.rawBytes); sha = hashlib.sha256(raw).hexdigest()
                row['shaders'][stage.name] = sha
                with open(os.path.join(out, sha+'.dxbc'), 'wb') as f: f.write(raw)
                with open(os.path.join(out, sha+'.txt'), 'w') as f: f.write(ctl.DisassembleShader(pipe.GetGraphicsPipelineObject(), reflection, ''))
            if event in expected and row['shaders']['Vertex'] != expected[event]:
                raise ValueError('Unexpected material shader')
            mesh = ctl.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2, 4) or not 0 < mesh.numIndices <= 32768 or not 16 <= mesh.vertexByteStride <= 256:
                raise ValueError('Invalid mesh bounds')
            ib = bytes(ctl.GetBufferData(mesh.indexResourceId, mesh.indexByteOffset, mesh.numIndices*mesh.indexByteStride))
            indices = struct.unpack('<'+str(mesh.numIndices)+('H' if mesh.indexByteStride == 2 else 'I'), ib)
            referenced = sorted({i+mesh.baseVertex for i in indices})
            low, high = referenced[0], referenced[-1]
            if not 0 <= low <= high < 100000 or (high-low+1)*mesh.vertexByteStride > 16*1024*1024: raise ValueError('Invalid vertex range')
            raw = bytes(ctl.GetBufferData(mesh.vertexResourceId, mesh.vertexByteOffset+low*mesh.vertexByteStride, (high-low+1)*mesh.vertexByteStride))
            if len(raw) != (high-low+1)*mesh.vertexByteStride: raise ValueError('Short vertex readback')
            positions = {i: struct.unpack_from('<4f', raw, (i-low)*mesh.vertexByteStride) for i in referenced}
            if not all(math.isfinite(v) for p in positions.values() for v in p): raise ValueError('Nonfinite geometry')
            row.update(indices=list(indices), base_vertex=mesh.baseVertex, stride=mesh.vertexByteStride,
                       positions=positions, referenced=referenced, raw_sha256=hashlib.sha256(raw).hexdigest())
            with open(os.path.join(out, str(event)+'-vertices.bin'), 'wb') as f: f.write(raw)
            if event not in expected: continue
            target = textures[str(pipe.GetOutputTargets()[0].resource)]
            viewport = pipe.GetViewport(0)
            row['color'] = {'resource': str(target.resourceId), 'width': target.width,
                            'height': target.height, 'samples': target.msSamp, 'format': target.format.Name()}
            candidates = []
            for primitive in range(len(indices)//3):
                clip = [positions[i+mesh.baseVertex] for i in indices[primitive*3:primitive*3+3]]
                if any(p[3] <= .001 for p in clip): continue
                screen = [(viewport.x+(p[0]/p[3]+1)*viewport.width/2, viewport.y+(1-p[1]/p[3])*viewport.height/2) for p in clip]
                x, y = round(sum(p[0] for p in screen)/3), round(sum(p[1] for p in screen)/3)
                area = abs((screen[1][0]-screen[0][0])*(screen[2][1]-screen[0][1])-(screen[2][0]-screen[0][0])*(screen[1][1]-screen[0][1]))/2
                if area > 4 and max(0,viewport.x) <= x < min(target.width,viewport.x+viewport.width) and max(0,viewport.y) <= y < min(target.height,viewport.y+viewport.height): candidates.append((area, primitive, x, y))
            for area, primitive, x, y in sorted(candidates, reverse=True)[:16]:
                for sample in range(max(1, target.msSamp)):
                    ctl.SetFrameEvent(event, True); sub = rd.Subresource(); sub.sample = sample
                    history = ctl.PixelHistory(target.resourceId, x, y, sub, rd.CompType.Float)
                    report['pixels'].append({'event': event, 'primitive': primitive, 'area': area, 'x': x, 'y': y,
                        'sample': sample, 'history': [{'event': h.eventId, 'primitive': h.primitiveID,
                        'passed': h.Passed(), 'details': rd.DumpObject(h)} for h in history if h.eventId <= event]})
        if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('Replay GPU error')
        report['complete'] = True
    except BaseException: report['error'] = traceback.format_exc()
    finally:
        if ctl is not None: ctl.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out, 'pairs.json'), 'w') as f: json.dump(report, f, indent=2)


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'static-visible-pairs-error.txt'), 'w') as f: f.write(traceback.format_exc())
sys.exit(0)
