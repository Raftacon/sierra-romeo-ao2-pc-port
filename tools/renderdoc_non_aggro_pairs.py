"""Inspect the four failed non-aggro depth/material correspondences.

Run in RenderDoc Python with AOT_RENDERDOC_PROBE pointing at the normally
closed non-aggro-controlled-001 capture. This records geometry and shader
identity even for depth-only or rejected draws.
"""
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
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f:
        path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root):
        raise ValueError('Foreign capture')
    out = os.path.join(root, 'non-aggro-pairs-001'); os.mkdir(out)
    report = {'capture': path, 'complete': False, 'draws': []}
    cap, ctl = rd.OpenCaptureFile(), None
    try:
        assert cap.OpenFile(path, '', None) == rd.ResultCode.Succeeded
        status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        names = {}
        structured = ctl.GetStructuredFile()
        def visit(nodes, ancestors=()):
            for a in nodes:
                chain = ancestors + (a.GetName(structured),)
                names[a.eventId] = chain
                visit(a.children, chain)
        visit(ctl.GetRootActions())
        for event, primitive in ((20439, 213), (20549, 213), (20451, 1680),
                                 (20579, 1680), (20451, 2384), (20579, 2384),
                                 (20443, 1975), (20565, 1975)):
            ctl.SetFrameEvent(event, True); pipe = ctl.GetPipelineState()
            row = {'event': event, 'primitive': primitive, 'names': names[event],
                   'viewport': rd.DumpObject(pipe.GetViewport(0)), 'shaders': {},
                   'ib': rd.DumpObject(pipe.GetIBuffer()),
                   'state': rd.DumpObject(ctl.GetD3D12PipelineState().outputMerger)}
            report['draws'].append(row)
            for stage in (rd.ShaderStage.Vertex, rd.ShaderStage.Pixel):
                reflection = pipe.GetShaderReflection(stage)
                if reflection is None: continue
                raw = bytes(reflection.rawBytes); sha = hashlib.sha256(raw).hexdigest()
                row['shaders'][stage.name] = {'sha256': sha, 'resource': str(pipe.GetShader(stage))}
                with open(os.path.join(out, sha+'.dxbc'), 'wb') as f: f.write(raw)
                with open(os.path.join(out, sha+'.txt'), 'w') as f:
                    f.write(ctl.DisassembleShader(pipe.GetGraphicsPipelineObject(), reflection, ''))
            mesh = ctl.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2, 4) or not 16 <= mesh.vertexByteStride <= 256:
                raise ValueError('Invalid mesh layout')
            ib = bytes(ctl.GetBufferData(mesh.indexResourceId, mesh.indexByteOffset+primitive*3*mesh.indexByteStride, 3*mesh.indexByteStride))
            indices = struct.unpack('<3'+('H' if mesh.indexByteStride == 2 else 'I'), ib)
            row['indices'], row['stride'], row['positions'], row['material'] = indices, mesh.vertexByteStride, [], []
            for index in indices:
                index += mesh.baseVertex
                if not 0 <= index < 100000: raise ValueError('Invalid vertex')
                data = bytes(ctl.GetBufferData(mesh.vertexResourceId, mesh.vertexByteOffset+index*mesh.vertexByteStride, mesh.vertexByteStride))
                if len(data) != mesh.vertexByteStride: raise ValueError('Short vertex read')
                row['positions'].append(struct.unpack('<4f', data[:16]))
                row['material'].append(data[16:].hex())
        if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('Replay GPU error')
        report['complete'] = True
    except BaseException: report['error'] = traceback.format_exc()
    finally:
        if ctl is not None: ctl.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out, 'pairs.json'), 'w') as f: json.dump(report, f, indent=2)


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'non-aggro-pairs-error.txt'), 'w') as f: f.write(traceback.format_exc())
sys.exit(0)
