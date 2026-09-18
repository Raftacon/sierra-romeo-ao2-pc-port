"""Bounded replay-only material experiment for the identified equipment draw.

Run in RenderDoc's Python with AOT_RENDERDOC_PROBE and AOT_EQUIPMENT_PHASE
pointing to a completed capture and a new variants directory respectively.
Optional AOT_EQUIPMENT_REPLAY_QUERY selects draw_event, readback_event and roi.
Uses actual GPU texture readback, not RenderDoc's shader interpreter.
"""
import hashlib
import json
import math
import os
import struct
import sys
import traceback
import renderdoc as rd


def main():
    probe = os.environ['AOT_RENDERDOC_PROBE']
    output = os.environ['AOT_EQUIPMENT_PHASE']
    report_path = os.path.join(output, 'replay.json')
    if os.path.exists(report_path):
        raise ValueError('Require new replay output')
    with open(os.path.join(probe, 'probe.json')) as source:
        completed = json.load(source)
    if completed.get('timed_out') or completed.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require a normally closed native probe')
    with open(os.path.join(probe, 'renderdoc-capture.json')) as source:
        capture_path = json.load(source)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(capture_path)) != os.path.normcase(os.path.abspath(probe)):
        raise ValueError('Capture does not belong to probe')
    with open(os.path.join(output, 'variants.json')) as source:
        manifest = json.load(source)
    report = {'capture': capture_path, 'draw_event': 7464, 'readback_event': 7613,
              'roi': [285, 410, 48, 40], 'sample': 0, 'variants': [],
              'experiment': manifest.get('experiment', 'uv-phase')}
    query_path = os.environ.get('AOT_EQUIPMENT_REPLAY_QUERY')
    if query_path:
        with open(query_path) as source:
            query = json.load(source)
        for key in ('draw_event', 'readback_event', 'roi'):
            report[key] = query[key]
        if (not isinstance(report['draw_event'], int) or report['draw_event'] <= 0
                or not isinstance(report['readback_event'], int)
                or report['readback_event'] < report['draw_event']):
            raise ValueError('Require ordered positive draw/readback events')
    if (len(report['roi']) != 4 or not all(isinstance(v, int) for v in report['roi'])
            or min(report['roi'][:2]) < 0 or min(report['roi'][2:]) <= 0
            or report['roi'][2]*report['roi'][3] > 65536):
        raise ValueError('Require a bounded integer ROI')
    capture, controller, replacement, original = rd.OpenCaptureFile(), None, None, None
    try:
        status = capture.OpenFile(capture_path, '', None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        status, controller = capture.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        def visit(actions):
            for action in actions:
                yield action
                yield from visit(action.children)
        actions = {a.eventId: a for a in visit(controller.GetRootActions())}
        if report['draw_event'] not in actions or report['readback_event'] > max(actions):
            raise ValueError('Requested events are outside the capture')
        controller.SetFrameEvent(report['draw_event'], True)
        pipe = controller.GetPipelineState()
        original = pipe.GetShader(rd.ShaderStage.Pixel)
        reflection = pipe.GetShaderReflection(rd.ShaderStage.Pixel)
        raw = bytes(reflection.rawBytes)
        if hashlib.sha256(raw).hexdigest() != manifest['original_sha256']:
            raise ValueError('Captured draw does not use the identified shader')
        target = actions[report['draw_event']].outputs[0]
        texture = next(t for t in controller.GetTextures() if t.resourceId == target)
        report['resource'] = str(target)
        if texture.format.Name() != 'R16G16B16A16_FLOAT' or texture.msSamp != 2:
            raise ValueError('Unexpected captured HDR format or sample count')
        x, y, w, h = report['roi']
        if x+w > texture.width or y+h > texture.height:
            raise ValueError('ROI exceeds target bounds')
        sub = rd.Subresource()
        sub.sample = 0

        def read_roi():
            controller.SetFrameEvent(report['readback_event'], True)
            data = bytes(controller.GetTextureData(texture.resourceId, sub))
            if len(data) != texture.width * texture.height * 8:
                raise ValueError('Unexpected sample-zero readback size')
            x, y, w, h = report['roi']
            return b''.join(data[((y+row)*texture.width+x)*8:((y+row)*texture.width+x+w)*8]
                            for row in range(h))

        baseline = read_roi()
        report['baseline_sha256'] = hashlib.sha256(baseline).hexdigest()
        with open(os.path.join(output, 'baseline-rgba16f.bin'), 'wb') as target:
            target.write(baseline)
        baseline_values = struct.unpack('<'+'e'*(len(baseline)//2), baseline)
        if not all(math.isfinite(v) for j,v in enumerate(baseline_values) if j%4!=3):
            raise ValueError('Baseline contains nonfinite RGB values')
        variants = [{'file': manifest['original'], 'control': 'unchanged original',
                     'sha256': manifest['original_sha256']}] + manifest['variants']
        if len(variants) > 16:
            raise ValueError('Variant count exceeds replay budget')
        for i, variant in enumerate(variants):
            filename = variant['file']
            if i and (os.path.basename(filename) != filename or not filename.endswith('.dxbc')):
                raise ValueError('Variant must be local DXBC file')
            with open(filename if not i else os.path.join(output, filename), 'rb') as source:
                bytecode = source.read()
            if hashlib.sha256(bytecode).hexdigest() != variant['sha256']:
                raise ValueError('Variant checksum mismatch')
            replacement, errors = controller.BuildTargetShader(
                'main', rd.ShaderEncoding.DXBC, bytecode, rd.ShaderCompileFlags(), rd.ShaderStage.Pixel)
            if replacement == rd.ResourceId.Null():
                raise RuntimeError('Replacement creation failed: '+errors)
            controller.ReplaceResource(original, replacement)
            changed = read_roi()
            values = struct.unpack('<'+'e'*(len(changed)//2), changed)
            if not all(math.isfinite(v) for j,v in enumerate(values) if j%4!=3):
                raise ValueError('Variant contains nonfinite RGB values')
            row = dict(variant, output_sha256=hashlib.sha256(changed).hexdigest(),
                       changed_rgb_pixels=sum(any(values[p+c] != baseline_values[p+c] for c in range(3))
                                              for p in range(0,len(values),4)),
                       max_abs_rgb=max(abs(v-baseline_values[j]) for j,v in enumerate(values) if j%4!=3),
                       compiler_messages=errors)
            report['variants'].append(row)
            with open(os.path.join(output, 'result-%02d-rgba16f.bin'%i), 'wb') as target:
                target.write(changed)
            if i <= 1 and changed != baseline:
                raise RuntimeError('Original/literal baseline control changed GPU output')
            if variant.get('control') == 'zero output' and row['changed_rgb_pixels'] == 0:
                raise RuntimeError('Positive control did not change GPU output')
            controller.RemoveReplacement(original)
            controller.FreeTargetResource(replacement)
            replacement = None
        report['restored_sha256'] = hashlib.sha256(read_roi()).hexdigest()
        if report['restored_sha256'] != report['baseline_sha256']:
            raise RuntimeError('Restored replay differs from original')
        status = controller.GetFatalErrorStatus()
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if controller is not None:
            if replacement is not None:
                controller.RemoveReplacement(original)
                controller.FreeTargetResource(replacement)
            controller.Shutdown()
        capture.Shutdown()
        with open(report_path, 'w') as target:
            json.dump(report, target, indent=2)


try:
    main()
except BaseException:
    with open(os.path.join(os.environ['AOT_EQUIPMENT_PHASE'], 'launch-error.txt'), 'w') as target:
        target.write(traceback.format_exc())
sys.exit(0)
