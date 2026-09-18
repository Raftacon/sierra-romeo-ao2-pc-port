"""Inspect additional facade material candidates against their shared depth draws."""
import hashlib
import json
import os
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
    out = os.path.join(root, 'facade-static-candidates-001'); os.mkdir(out)
    report = {'capture': path, 'complete': False, 'draws': []}
    cap, ctl = rd.OpenCaptureFile(), None
    try:
        assert cap.OpenFile(path, '', None) == rd.ResultCode.Succeeded
        status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        structured = ctl.GetStructuredFile()
        names = {}
        def visit(nodes, ancestors=()):
            for a in nodes:
                chain = ancestors + (a.GetName(structured),)
                names[a.eventId] = chain
                visit(a.children, chain)
        visit(ctl.GetRootActions())
        counters = ctl.FetchCounters([rd.GPUCounter.SamplesPassed, rd.GPUCounter.PSInvocations])
        saved = set()
        for event in (4497, 6086, 7629, 4503, 6092, 7598, 4553, 6142, 7591, 4546, 6135, 7606, 4587, 6172, 7621, 4579, 6164, 7613):
            ctl.SetFrameEvent(event, True); pipe = ctl.GetPipelineState()
            vs = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
            if hashlib.sha256(bytes(vs.rawBytes)).hexdigest() not in ('4a985b920fa13d0d3b6c6e0b90750e8107b11268dfc4d4a6e12ef689b2d627f5', '8c8c8b9c2e799d11e1bb2dbecb3bcafd3245ff63a376ace03745debeaa527df5'):
                raise ValueError('Unexpected facade candidate shader')
            row = {'event': event, 'viewport': [rd.DumpObject(pipe.GetViewport(0))],
                   'state': rd.DumpObject(ctl.GetD3D12PipelineState().outputMerger), 'images': []}
            row['vs_sha256'] = hashlib.sha256(bytes(vs.rawBytes)).hexdigest()
            row['names'] = names[event]
            row['coverage'] = [rd.DumpObject(r) for r in counters if r.eventId == event]
            report['draws'].append(row)
            ps = pipe.GetShaderReflection(rd.ShaderStage.Pixel)
            sha = hashlib.sha256(bytes(ps.rawBytes)).hexdigest(); row['pixel_sha256'] = sha
            with open(os.path.join(out, sha+'.dxbc'), 'wb') as f: f.write(bytes(ps.rawBytes))
            with open(os.path.join(out, sha+'.txt'), 'w') as f: f.write(ctl.DisassembleShader(pipe.GetGraphicsPipelineObject(), ps, ''))
            mesh = ctl.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2, 4) or not 0 < mesh.numIndices < 4096 or not 16 <= mesh.vertexByteStride <= 256: raise ValueError('Invalid mesh bounds')
            ib = bytes(ctl.GetBufferData(mesh.indexResourceId, mesh.indexByteOffset, mesh.numIndices*mesh.indexByteStride))
            indices = struct.unpack('<'+str(mesh.numIndices)+('H' if mesh.indexByteStride == 2 else 'I'), ib)
            low, high = min(indices)+mesh.baseVertex, max(indices)+mesh.baseVertex
            if not 0 <= low <= high < 100000: raise ValueError('Invalid vertex range')
            raw = bytes(ctl.GetBufferData(mesh.vertexResourceId, mesh.vertexByteOffset+low*mesh.vertexByteStride, (high-low+1)*mesh.vertexByteStride))
            if len(raw) != (high-low+1)*mesh.vertexByteStride: raise ValueError('Short readback')
            row['positions'] = [struct.unpack_from('<4f', raw, (i+mesh.baseVertex-low)*mesh.vertexByteStride) for i in sorted(set(indices))]
            row['indices'] = list(indices); row['stride'] = mesh.vertexByteStride
            with open(os.path.join(out, str(event)+'-vertices.bin'), 'wb') as f: f.write(raw)
            for access in ctl.GetDescriptorAccess():
                if access.stage != rd.ShaderStage.Pixel or access.type != rd.DescriptorType.Image: continue
                region = rd.DescriptorRange(); region.offset = access.byteOffset; region.descriptorSize = access.byteSize; region.count = 1; region.type = access.type
                for desc in ctl.GetDescriptors(access.descriptorStore, [region]):
                    row['images'].append(rd.DumpObject(desc)); rid = str(desc.resource)
                    if rid in saved: continue
                    saved.add(rid); save = rd.TextureSave(); save.resourceId = desc.resource; save.destType = rd.FileType.PNG
                    ctl.SaveTexture(save, os.path.join(out, rid.replace('::', '-')+'.png'))
        report['complete'] = True
    except BaseException: report['error'] = traceback.format_exc()
    finally:
        if ctl is not None: ctl.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out, 'material.json'), 'w') as f: json.dump(report, f, indent=2)


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'facade-candidate-error.txt'), 'w') as f: f.write(traceback.format_exc())
sys.exit(0)
