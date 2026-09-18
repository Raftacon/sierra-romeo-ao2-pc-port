"""Validate projection variants against captured color/depth target bytes.

Use RenderDoc Python after the native capture process has closed. Consecutive
corrected draws are grouped only when both vertex shader and targets match;
copies, clears, other draws and shader changes end a group. Check every MSAA
sample plus final output. Replay counter sums are not native frame durations.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import traceback
import renderdoc as rd

root = Path(os.environ['AOT_RENDERDOC_PROBE']).resolve()
manifest = Path(os.environ['AOT_PROJECTION_FMA_VARIANTS']).resolve()
out = Path(os.environ['AOT_PROJECTION_FMA_OUTPUT']).resolve()
out.mkdir(parents=True, exist_ok=False)
report = dict(complete=False, limits=__doc__, runs=[])
cap, ctl = rd.OpenCaptureFile(), None
replacements = []


def release():
    for original, replacement in replacements:
        ctl.RemoveReplacement(original)
        ctl.FreeTargetResource(replacement)
    replacements.clear()


def progress(mode, step, total):
    (out / 'progress.json').write_text(json.dumps(dict(mode=mode, step=step, total=total))+'\n')


try:
    native = json.loads((root/'probe.json').read_text())
    patches = json.loads(manifest.read_text())
    group_bytes = Path(patches['groups']).read_bytes()
    if hashlib.sha256(group_bytes).hexdigest() != patches['groups_sha256']:
        raise ValueError('Shader inventory changed')
    groups = json.loads(group_bytes)
    if (native['timed_out'] or native['exit_code_before_cleanup'] != 0 or
            not groups['complete'] or not patches['complete']):
        raise ValueError('Require closed native capture and complete shader variants')
    path = Path(json.loads((root/'renderdoc-capture.json').read_text())['captures'][0]['path']).resolve()
    if path.parent != root:
        raise ValueError('Foreign capture')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    if digest.hexdigest() != patches['capture_sha256']:
        raise ValueError('Capture changed')
    report['capture_sha256'] = digest.hexdigest()
    if cap.OpenFile(str(path), '', None) != rd.ResultCode.Succeeded:
        raise RuntimeError('Capture open failed')
    status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
    if status != rd.ResultCode.Succeeded:
        raise RuntimeError(str(status))
    textures = {str(texture.resourceId): texture for texture in ctl.GetTextures()}
    patch_map = {patch['original_sha256']: patch for patch in patches['variants']}
    events = groups['events']
    targeted = [row for row in events if row.get('shaders', {}).get('Vertex') in patch_map]
    if not targeted:
        raise ValueError('No projection shader uses')
    target_events = {row['event'] for row in targeted}
    originals = {}
    for index, row in enumerate(targeted):
        ctl.SetFrameEvent(row['event'], False)
        pipe = ctl.GetPipelineState()
        sid = pipe.GetShader(rd.ShaderStage.Vertex)
        sha = row['shaders']['Vertex']
        raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError('Captured shader mismatch')
        if str(sid) not in originals:
            patch = patch_map[sha]
            code = (manifest.parent/patch['file']).read_bytes()
            if hashlib.sha256(code).hexdigest() != patch['sha256']:
                raise ValueError('Replacement changed')
            originals[str(sid)] = (sid, raw, code)
        if index % 32 == 0:
            progress('shader-identity', index, len(targeted))
    checkpoints = []
    for index, row in enumerate(events):
        if row['event'] not in target_events:
            continue
        following = events[index+1] if index+1 < len(events) else None
        if (following and following['event'] in target_events and following['outputs'] == row['outputs']
                and following['shaders']['Vertex'] == row['shaders']['Vertex']):
            continue
        checkpoints.append(dict(event=row['event'], targets=[target['id'] for target in row['outputs']]))
    final_targets = [str(texture.resourceId) for texture in textures.values()
                     if texture.width == 1920 and texture.height == 1080 and
                     texture.format.Name() == 'R10G10B10A2_UNORM']
    if not final_targets:
        raise ValueError('Missing final output textures')
    checkpoints.append(dict(event=max(row['event'] for row in events), targets=final_targets))
    report.update(shader_resources=len(originals), targeted_draws=len(targeted), checkpoints=checkpoints)
    baseline = {}
    counter = rd.GPUCounter.EventGPUDuration
    if counter not in ctl.EnumerateCounters():
        raise ValueError('GPU duration counter unavailable')
    description = ctl.DescribeCounter(counter)
    if description.unit != rd.CounterUnit.Seconds or description.resultByteWidth != 8:
        raise ValueError('Unexpected GPU counter format')
    for mode in ('original', 'control', 'candidate', 'restored'):
        release()
        if mode in ('control', 'candidate'):
            for sid, raw, code in originals.values():
                replacement, errors = ctl.BuildTargetShader('main', rd.ShaderEncoding.DXBC,
                    raw if mode == 'control' else code, rd.ShaderCompileFlags(), rd.ShaderStage.Vertex)
                if replacement == rd.ResourceId.Null() or errors:
                    raise RuntimeError('Shader build: '+errors)
                replacements.append((sid, replacement))
                ctl.ReplaceResource(sid, replacement)
        run = dict(mode=mode, targets=[], counter_passes=[])
        report['runs'].append(run)
        for index, checkpoint in enumerate(checkpoints):
            event = checkpoint['event']
            ctl.SetFrameEvent(event, True)
            for resource in checkpoint['targets']:
                texture = textures[resource]
                if texture.depth != 1 or texture.arraysize != 1 or texture.width*texture.height*16 > 256*1024*1024:
                    raise ValueError('Unexpected target shape/size')
                for sample in range(texture.msSamp):
                    sub = rd.Subresource(); sub.sample = sample
                    data = bytes(ctl.GetTextureData(texture.resourceId, sub))
                    if not data:
                        raise ValueError('Empty texture read')
                    key = (event, resource, sample)
                    fingerprint = (len(data), hashlib.sha256(data).hexdigest())
                    if mode == 'original':
                        baseline[key] = fingerprint
                    row = dict(event=event, resource=resource, sample=sample, bytes=len(data),
                               sha256=fingerprint[1], matches_original=fingerprint == baseline[key])
                    run['targets'].append(row)
                    if not row['matches_original']:
                        raise ValueError('Target mismatch: '+repr(key)+' in '+mode)
            progress(mode, index+1, len(checkpoints))
        for repeat in range(3):
            values = {row.eventId: float(row.value.d)*1000 for row in ctl.FetchCounters([counter])}
            if (len(values) != len(events) or not target_events.issubset(values) or
                    any(not math.isfinite(value) or value < 0 for value in values.values())):
                raise ValueError('Counter scope or values changed')
            (out/('%s-counter-%d.json' % (mode, repeat))).write_text(json.dumps(values)+'\n')
            run['counter_passes'].append(dict(sum_all_ms=sum(values.values()),
                sum_corrected_draw_ms=sum(values[event] for event in target_events)))
        run['median_corrected_draw_ms'] = statistics.median(row['sum_corrected_draw_ms'] for row in run['counter_passes'])
        run['bytes_checked'] = sum(target['bytes'] for target in run['targets'])
        (out/'validation.json').write_text(json.dumps(report, indent=2)+'\n')
    if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded:
        raise RuntimeError('GPU replay error')
    report['complete'] = True
except BaseException:
    report['error'] = traceback.format_exc()
finally:
    if ctl is not None:
        release(); ctl.Shutdown()
    cap.Shutdown()
    (out/'validation.json').write_text(json.dumps(report, indent=2)+'\n')
sys.exit(0 if report['complete'] else 1)
