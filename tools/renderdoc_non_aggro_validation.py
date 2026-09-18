"""Validate non-aggro depth projection in the captured faulty frame.

RenderDoc Python: AOT_RENDERDOC_PROBE and AOT_NON_AGGRO_VARIANT. Checks
original/control/precise/restored, paired geometry, rejected material pixels,
nearer occluders, and an unchanged red-character region. No shader source from
the retail game is included here.
"""
import hashlib
import json
import os
import re
import struct
import sys
import traceback
import renderdoc as rd

PAIRS = ((20439, 20549, 213), (20451, 20579, 1680),
         (20451, 20579, 2384), (20443, 20565, 1975))
PIXELS = ((383, 190, 20549, 213), (405, 294, 20579, 1680),
          (352, 347, 20579, 2384), (440, 537, 20565, 1975))


def main():
    root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    variant_dir = os.environ['AOT_NON_AGGRO_VARIANT']
    with open(os.path.join(root, 'probe.json')) as f: done = json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f:
        path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root): raise ValueError('Foreign capture')
    with open(os.path.join(variant_dir, 'variant.json')) as f: variant = json.load(f)
    if variant['event'] != 20439 or variant['original_sha256'] != 'dbb9a4ba90d049f9349bd7aacb08bd27aa1d0b59371b218b48dabcb24801abed':
        raise ValueError('Unexpected fixture')
    name = os.environ.get('AOT_NON_AGGRO_NAME', 'non-aggro-validation-001')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name): raise ValueError('Invalid name')
    out = os.path.join(root, name); os.mkdir(out)
    report = {'capture': path, 'variant': variant, 'runs': [], 'complete': False}
    cap, ctl, original, replacement = rd.OpenCaptureFile(), None, None, None
    baseline_frame, baseline_mesh = None, {}
    def save():
        with open(os.path.join(out, 'validation.json'), 'w') as f: json.dump(report, f, indent=2)
    try:
        assert cap.OpenFile(path, '', None) == rd.ResultCode.Succeeded
        status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        ctl.SetFrameEvent(20439, True); pipe = ctl.GetPipelineState()
        raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
        original = pipe.GetShader(rd.ShaderStage.Vertex)
        if hashlib.sha256(raw).hexdigest() != variant['original_sha256']: raise ValueError('Wrong shader')
        with open(os.path.join(variant_dir, variant['file']), 'rb') as f: precise = f.read()
        if hashlib.sha256(precise).hexdigest() != variant['sha256']: raise ValueError('Changed patch')
        textures = {str(t.resourceId): t for t in ctl.GetTextures()}
        color, display = textures['ResourceId::6459'], textures['ResourceId::2731']
        if (color.width, color.height, color.msSamp) != (1280, 2048, 1): raise ValueError('Unexpected HDR target')
        if (display.width, display.height, display.msSamp, display.format.Name()) != (1280, 2048, 1, 'R8G8B8A8_UNORM'):
            raise ValueError('Unexpected LDR target')
        def geometry(event, primitive):
            ctl.SetFrameEvent(event, True); mesh = ctl.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2, 4) or not 16 <= mesh.vertexByteStride <= 256: raise ValueError('Invalid mesh')
            ib = bytes(ctl.GetBufferData(mesh.indexResourceId, mesh.indexByteOffset+primitive*3*mesh.indexByteStride, 3*mesh.indexByteStride))
            indices = struct.unpack('<3'+('H' if mesh.indexByteStride == 2 else 'I'), ib)
            positions, other = b'', b''
            for index in indices:
                index += mesh.baseVertex
                if not 0 <= index < 100000: raise ValueError('Invalid index')
                data = bytes(ctl.GetBufferData(mesh.vertexResourceId, mesh.vertexByteOffset+index*mesh.vertexByteStride, mesh.vertexByteStride))
                if len(data) != mesh.vertexByteStride: raise ValueError('Short vertex read')
                positions += data[:16]; other += data[16:]
            return positions, other
        def red_region(frame):
            return b''.join(frame[(y*1280+710)*4:(y*1280+870)*4] for y in range(220, 550))
        for mode in ('original', 'control', 'precise', 'restored'):
            if replacement is not None:
                ctl.RemoveReplacement(original); ctl.FreeTargetResource(replacement); replacement = None
            if mode in ('control', 'precise'):
                replacement, errors = ctl.BuildTargetShader('main', rd.ShaderEncoding.DXBC,
                    raw if mode == 'control' else precise, rd.ShaderCompileFlags(), rd.ShaderStage.Vertex)
                if replacement == rd.ResourceId.Null() or errors: raise RuntimeError('Build failed: '+errors)
                ctl.ReplaceResource(original, replacement)
            run = {'mode': mode, 'geometry': [], 'pixels': []}; report['runs'].append(run)
            for depth, material, primitive in PAIRS:
                for event in (depth, material):
                    pos, other = geometry(event, primitive); key = event, primitive
                    if mode == 'original': baseline_mesh[key] = pos, other
                    elif other != baseline_mesh[key][1] or ((mode != 'precise' or event == material) and pos != baseline_mesh[key][0]):
                        raise ValueError('Changed material outputs or failed control/restoration')
                    run['geometry'].append({'event': event, 'primitive': primitive, 'positions_hex': pos.hex(),
                                            'material_sha256': hashlib.sha256(other).hexdigest()})
                if mode == 'precise' and geometry(depth, primitive)[0] != geometry(material, primitive)[0]:
                    raise ValueError('Corrected depth and material clip positions differ')
            for x, y, _, _ in PIXELS:
                ctl.SetFrameEvent(20626, True)
                history = ctl.PixelHistory(color.resourceId, x, y, rd.Subresource(), rd.CompType.Float)
                run['pixels'].append({'x': x, 'y': y, 'history': [
                    {'event': h.eventId, 'primitive': h.primitiveID, 'passed': h.Passed(), 'details': rd.DumpObject(h)}
                    for h in history if h.eventId <= 20626]})
            ctl.SetFrameEvent(22125, True)
            frame = bytes(ctl.GetTextureData(display.resourceId, rd.Subresource()))
            if len(frame) != 1280*2048*4: raise ValueError('Unexpected frame packing')
            run['frame_sha256'] = hashlib.sha256(frame).hexdigest()
            run['red_region_sha256'] = hashlib.sha256(red_region(frame)).hexdigest()
            if mode == 'original': baseline_frame = frame
            elif mode in ('control', 'restored') and frame != baseline_frame: raise ValueError('Frame control/restoration mismatch')
            elif mode == 'precise' and red_region(frame) != red_region(baseline_frame): raise ValueError('Red region changed')
            tex_save = rd.TextureSave(); tex_save.resourceId = display.resourceId; tex_save.destType = rd.FileType.PNG
            if ctl.SaveTexture(tex_save, os.path.join(out, mode+'.png')) != rd.ResultCode.Succeeded: raise RuntimeError('Save failed')
            save()
        def hit(run, x, y, event, primitive):
            pixel = next(p for p in run['pixels'] if (p['x'], p['y']) == (x, y))
            return next(h for h in pixel['history'] if h['event'] == event and h['primitive'] == primitive)
        for x, y, event, primitive in PIXELS:
            before, after = hit(report['runs'][0], x, y, event, primitive), hit(report['runs'][2], x, y, event, primitive)
            if before['passed'] or before['details']['depthTestFailed'] != 'True' or not after['passed']:
                raise ValueError('Rejected material pixel was not restored')
        for x, y, event, primitive in ((383, 190, 20572, 2920), (352, 347, 20572, 2531), (440, 537, 20579, 681)):
            if any(hit(run, x, y, event, primitive)['passed'] for run in report['runs']):
                raise ValueError('Hidden material leaked through nearer character geometry')
        if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('Replay GPU error')
        report.update(complete=True, twelve_vertices_exact=True, four_rejected_pixels_restored=True,
                      three_nearer_occluders_preserved=True, material_outputs_unchanged=True,
                      red_region_unchanged=True, restoration_exact=True)
    except BaseException: report['error'] = traceback.format_exc()
    finally:
        if ctl is not None:
            if replacement is not None and replacement != rd.ResourceId.Null():
                ctl.RemoveReplacement(original); ctl.FreeTargetResource(replacement)
            ctl.Shutdown()
        cap.Shutdown(); save()


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'non-aggro-validation-error.txt'), 'w') as f: f.write(traceback.format_exc())
sys.exit(0)
