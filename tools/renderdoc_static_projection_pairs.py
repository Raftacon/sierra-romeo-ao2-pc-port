"""Inventory users of the shared static depth program in a closed capture.

Run in RenderDoc Python with AOT_RENDERDOC_PROBE. Index-buffer matches are
candidate pairs, not proof of identical world-space geometry or visual parity.
"""
import hashlib
import json
import os
import sys
import traceback
import renderdoc as rd

DEPTH_SHA = '850505c384a4a7d36dff0a49a71a2ed2e7edb37d7aa572fa718a62162c33b926'


def main():
    root = os.environ['AOT_RENDERDOC_PROBE']
    with open(os.path.join(root, 'probe.json')) as f: done = json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require a normally closed native capture')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f:
        path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(os.path.abspath(root)):
        raise ValueError('Capture must belong to probe')
    out = os.path.join(root, 'static-projection-pairs-001')
    os.mkdir(out)
    report = {'capture': path, 'complete': False, 'draws': [], 'shaders': {}}
    cap, ctl = rd.OpenCaptureFile(), None
    try:
        assert cap.OpenFile(path, '', None) == rd.ResultCode.Succeeded
        status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        actions = []
        def visit(nodes):
            for a in nodes:
                if a.flags & rd.ActionFlags.Drawcall and a.depthOut != rd.ResourceId.Null():
                    actions.append(a)
                visit(a.children)
        visit(ctl.GetRootActions())
        if not 1 <= len(actions) <= 4000: raise ValueError('Require bounded single-frame capture')
        for a in actions:
            ctl.SetFrameEvent(a.eventId, False)
            pipe = ctl.GetPipelineState()
            shader = str(pipe.GetShader(rd.ShaderStage.Vertex))
            if shader not in report['shaders']:
                reflection = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
                raw = bytes(reflection.rawBytes)
                sha = hashlib.sha256(raw).hexdigest()
                report['shaders'][shader] = sha
                with open(os.path.join(out, sha+'.dxbc'), 'wb') as f: f.write(raw)
                with open(os.path.join(out, sha+'.txt'), 'w') as f:
                    f.write(ctl.DisassembleShader(pipe.GetGraphicsPipelineObject(), reflection, ''))
            ib = pipe.GetIBuffer()
            report['draws'].append({'event': a.eventId, 'indices': a.numIndices,
                'index_offset': a.indexOffset, 'base_vertex': a.baseVertex, 'instances': a.numInstances,
                'ib_resource': str(ib.resourceId), 'ib_offset': ib.byteOffset, 'ib_stride': ib.byteStride,
                'vs_sha256': report['shaders'][shader], 'depth': str(a.depthOut),
                'outputs': [str(r) for r in a.outputs]})
        def key(r):
            return tuple(r[k] for k in ('indices', 'index_offset', 'base_vertex', 'instances',
                                       'ib_resource', 'ib_offset', 'ib_stride'))
        depth = [r for r in report['draws'] if r['vs_sha256'] == DEPTH_SHA]
        if not depth: raise ValueError('Expected shared static depth program is absent')
        report['pairs'] = [{'depth_event': d['event'], 'candidates': [r['event']
            for r in report['draws'] if r is not d and key(r) == key(d)]} for d in depth]
        if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('Replay GPU error')
        report['complete'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if ctl is not None: ctl.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out, 'pairs.json'), 'w') as f: json.dump(report, f, indent=2)


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'static-pairs-error.txt'), 'w') as f:
        f.write(traceback.format_exc())
sys.exit(0)
