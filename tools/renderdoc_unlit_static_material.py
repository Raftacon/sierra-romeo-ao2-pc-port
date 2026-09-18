"""Read the remaining unlit static material's captured geometry and bindings."""
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
    out = os.path.join(root, 'unlit-static-material-002'); os.mkdir(out)
    report = {'capture': path, 'complete': False, 'draws': []}
    cap, ctl = rd.OpenCaptureFile(), None
    try:
        assert cap.OpenFile(path, '', None) == rd.ResultCode.Succeeded
        status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        saved = set()
        for event in (11073, 11078, 11083, 11088, 11093, 11098, 11164):
            ctl.SetFrameEvent(event, True); pipe = ctl.GetPipelineState()
            vs = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
            if hashlib.sha256(bytes(vs.rawBytes)).hexdigest() != '7455815e6e0ca341e456923f9f6f93d375983a412b06260d3b18e631317ccd53':
                raise ValueError('Unexpected unlit shader')
            row = {'event': event, 'viewport': [rd.DumpObject(pipe.GetViewport(0))],
                   'state': rd.DumpObject(ctl.GetD3D12PipelineState().outputMerger), 'images': []}
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
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'unlit-material-error.txt'), 'w') as f: f.write(traceback.format_exc())
sys.exit(0)
