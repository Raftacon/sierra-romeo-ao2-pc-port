"""Inventory depth-bound guest shaders and actual coverage in a closed capture.

Set AOT_RENDERDOC_PROBE and AOT_SHADER_REGISTRY (SHA-256 keyed local manifest).
Unknown shaders remain explicitly unknown; index-buffer matches are candidates.
Run with RenderDoc's embedded Python after the owned native process has exited.
"""
import hashlib
import json
import os
import re
import sys
import traceback
import renderdoc as rd


def main():
    root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    with open(os.path.join(root, 'probe.json')) as f: done = json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require a normally closed native capture')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f:
        path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root):
        raise ValueError('Foreign capture')
    with open(os.environ['AOT_SHADER_REGISTRY']) as f: registry = json.load(f)
    # Verify source bytes rather than trusting an old filename-to-hash mapping.
    for sha, source in registry.items():
        with open(source['file'], 'rb') as f: actual = hashlib.sha256(f.read()).hexdigest()
        if actual != sha: raise ValueError('Changed registry source: '+source['file'])
    name = os.environ.get('AOT_STATIC_COVERAGE_NAME', 'static-family-coverage-001')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name): raise ValueError('Invalid output name')
    out = os.path.join(root, name); os.mkdir(out)
    report = {'capture': path, 'draws': [], 'complete': False}
    cap, ctl = rd.OpenCaptureFile(), None
    try:
        if cap.OpenFile(path, '', None) != rd.ResultCode.Succeeded:
            raise RuntimeError('Capture open failed')
        status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        counters = [rd.GPUCounter.SamplesPassed, rd.GPUCounter.PSInvocations]
        if any(c not in ctl.EnumerateCounters() for c in counters):
            raise ValueError('Required counters unavailable')
        coverage = {}
        for r in ctl.FetchCounters(counters):
            coverage.setdefault(r.eventId, {})[
                'samples_passed' if r.counter == counters[0] else 'pixel_invocations'] = int(r.value.u64)
        actions = []
        def visit(nodes):
            for a in nodes:
                if a.flags & rd.ActionFlags.Drawcall and a.depthOut != rd.ResourceId.Null(): actions.append(a)
                visit(a.children)
        visit(ctl.GetRootActions())
        if not 0 < len(actions) <= 4000: raise ValueError('Require bounded single-frame capture')
        shaders = {}
        for a in actions:
            ctl.SetFrameEvent(a.eventId, False); pipe = ctl.GetPipelineState()
            sid = str(pipe.GetShader(rd.ShaderStage.Vertex))
            if sid not in shaders:
                refl = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
                raw = bytes(refl.rawBytes); sha = hashlib.sha256(raw).hexdigest()
                shaders[sid] = sha
                with open(os.path.join(out, sha+'.dxbc'), 'wb') as f: f.write(raw)
                with open(os.path.join(out, sha+'.txt'), 'w') as f:
                    f.write(ctl.DisassembleShader(pipe.GetGraphicsPipelineObject(), refl, ''))
            sha = shaders[sid]; ib = pipe.GetIBuffer()
            report['draws'].append({'event': a.eventId, 'vs_sha256': sha,
                'source': registry.get(sha), 'coverage': coverage.get(a.eventId, {}),
                'indices': a.numIndices, 'index_offset': a.indexOffset,
                'base_vertex': a.baseVertex, 'instances': a.numInstances,
                'ib_resource': str(ib.resourceId), 'ib_offset': ib.byteOffset, 'ib_stride': ib.byteStride,
                'depth': str(a.depthOut), 'outputs': [str(r) for r in a.outputs],
                'viewport': rd.DumpObject(pipe.GetViewport(0)),
                'depth_state': rd.DumpObject(ctl.GetD3D12PipelineState().outputMerger.depthStencilState)})
        if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('Replay GPU error')
        report['complete'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if ctl is not None: ctl.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out, 'coverage.json'), 'w') as f: json.dump(report, f, indent=2)


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'static-family-error.txt'), 'w') as f:
        f.write(traceback.format_exc())
sys.exit(0)
