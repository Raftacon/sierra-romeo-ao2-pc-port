"""Find visible uses of the pinned unlit material in a closed 1x capture."""
import hashlib
import json
import os
import sys
import traceback
import renderdoc as rd

root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
out = os.path.join(root, 'visible-unlit-001'); os.mkdir(out)
report = {'complete': False, 'draws': []}
cap, ctl = rd.OpenCaptureFile(), None
try:
    with open(os.path.join(root, 'probe.json')) as f: done = json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0: raise ValueError('Require closed native capture')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f: path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root): raise ValueError('Foreign capture')
    report['capture'] = path
    assert cap.OpenFile(path, '', None) == rd.ResultCode.Succeeded
    status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
    if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
    counters = [rd.GPUCounter.SamplesPassed, rd.GPUCounter.PSInvocations]
    if any(c not in ctl.EnumerateCounters() for c in counters): raise ValueError('Coverage counters unavailable')
    coverage = {}
    for result in ctl.FetchCounters(counters):
        coverage.setdefault(result.eventId, {})['samples_passed' if result.counter == counters[0] else 'pixel_invocations'] = int(result.value.u64)
    textures = {str(t.resourceId): t for t in ctl.GetTextures()}
    depth_ids = {t.resourceId for t in textures.values() if (t.width, t.height, t.msSamp, t.format.Name()) == (1280, 2048, 1, 'D32S8_TYPELESS')}
    actions = []
    def visit(nodes):
        for a in nodes:
            if a.flags & rd.ActionFlags.Drawcall and a.depthOut in depth_ids and a.outputs[0] != rd.ResourceId.Null(): actions.append(a)
            visit(a.children)
    visit(ctl.GetRootActions())
    if not 0 < len(actions) < 2000: raise ValueError('Unexpected candidate scope')
    report['candidate_count'] = len(actions)
    shaders, first_visible = {}, None
    for action in actions:
        ctl.SetFrameEvent(action.eventId, False); pipe = ctl.GetPipelineState()
        shader = pipe.GetShader(rd.ShaderStage.Vertex)
        if shader not in shaders:
            shaders[shader] = hashlib.sha256(bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)).hexdigest()
        if shaders[shader] != '7455815e6e0ca341e456923f9f6f93d375983a412b06260d3b18e631317ccd53': continue
        if action.eventId not in coverage: raise ValueError('Missing draw counters')
        row = {'event': action.eventId, 'indices': action.numIndices, 'shader_sha256': shaders[shader],
               'color': str(action.outputs[0]), 'depth': str(action.depthOut), **coverage[action.eventId]}
        report['draws'].append(row)
        if first_visible is None and row['samples_passed'] > 0:
            first_visible = row.copy()
    report['first_visible'] = first_visible
    if first_visible is not None:
        event = first_visible['event']; texture = textures[first_visible['color']]
        report['color_texture'] = rd.DumpObject(texture)
        for label, at in [('before', event-1), ('after', event)]:
            ctl.SetFrameEvent(at, True)
            raw = bytes(ctl.GetTextureData(texture.resourceId, rd.Subresource()))
            with open(os.path.join(out, label+'-color.bin'), 'wb') as f: f.write(raw)
    if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('GPU replay failure')
    report['complete'] = True
except BaseException: report['error'] = traceback.format_exc()
finally:
    if ctl is not None: ctl.Shutdown()
    cap.Shutdown()
    with open(os.path.join(out, 'visibility.json'), 'w') as f: json.dump(report, f, indent=2)
sys.exit(0)
