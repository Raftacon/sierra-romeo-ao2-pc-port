"""Measure actual sample coverage of the remaining unlit static draws."""
import json
import os
import sys
import traceback
import renderdoc as rd

root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
out = os.path.join(root, 'unlit-coverage-001')
os.mkdir(out)
report = {'complete': False}
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
    available = ctl.EnumerateCounters()
    report['available'] = [rd.DumpObject(ctl.DescribeCounter(c)) for c in available]
    selected = [c for c in available if c in (rd.GPUCounter.SamplesPassed, rd.GPUCounter.PSInvocations)]
    if not selected: raise ValueError('Required coverage counters unavailable')
    results = ctl.FetchCounters(selected)
    events = (11073, 11078, 11083, 11088, 11093, 11098, 11164, 6318)
    report['results'] = [rd.DumpObject(r) for r in results if r.eventId in events]
    if not report['results']: raise ValueError('No counters for selected draws')
    ctl.SetFrameEvent(11070, True)
    texture = next(t for t in ctl.GetTextures() if str(t.resourceId) == 'ResourceId::6367')
    report['depth_texture'] = rd.DumpObject(texture)
    raw = bytes(ctl.GetTextureData(texture.resourceId, rd.Subresource()))
    with open(os.path.join(out, 'before-unlit-depth.bin'), 'wb') as f: f.write(raw)
    if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('GPU replay failure')
    report['complete'] = True
except BaseException: report['error'] = traceback.format_exc()
finally:
    if ctl is not None: ctl.Shutdown()
    cap.Shutdown()
    with open(os.path.join(out, 'coverage.json'), 'w') as f: json.dump(report, f, indent=2)
sys.exit(0)
