"""Replay the captured unlit material against its corrected shared depth pass.

RenderDoc Python: AOT_RENDERDOC_PROBE, AOT_UNLIT_VARIANT. Native capture must
already be closed. Checks original replacement and exact restoration; changes
only the unlit family's vertex projection, never depth state or pixel shading.
"""
import hashlib
import json
import os
import re
import struct
import sys
import traceback
import renderdoc as rd


def main():
    root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    variant_dir = os.environ['AOT_UNLIT_VARIANT']
    with open(os.path.join(root, 'probe.json')) as f: done = json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f:
        path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root):
        raise ValueError('Capture must belong to probe')
    with open(os.path.join(variant_dir, 'variant.json')) as f: variant = json.load(f)
    if variant['event'] != 10072 or variant['original_sha256'] != '7455815e6e0ca341e456923f9f6f93d375983a412b06260d3b18e631317ccd53':
        raise ValueError('Unexpected unlit fixture')
    name = os.environ.get('AOT_UNLIT_NAME', 'unlit-material-validation-001')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name): raise ValueError('Invalid output name')
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
        ctl.SetFrameEvent(10072, True); pipe = ctl.GetPipelineState()
        raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
        original = pipe.GetShader(rd.ShaderStage.Vertex)
        if hashlib.sha256(raw).hexdigest() != variant['original_sha256']: raise ValueError('Wrong captured shader')
        with open(os.path.join(variant_dir, variant['file']), 'rb') as f: precise = f.read()
        if hashlib.sha256(precise).hexdigest() != variant['sha256']: raise ValueError('Changed patch')
        textures = {str(t.resourceId): t for t in ctl.GetTextures()}
        color = textures['ResourceId::5796']
        displays = [t for t in textures.values() if (t.width, t.height, t.msSamp, t.format.Name()) == (1280, 2048, 1, 'R8G8B8A8_UNORM') and t.creationFlags & rd.TextureCategory.ColorTarget]
        if len(displays) != 1: raise ValueError('Ambiguous display target')
        display = displays[0]
        def last(nodes): return max([0]+[max(a.eventId, last(a.children)) for a in nodes])
        display_event = last(ctl.GetRootActions())
        if (color.width, color.height, color.msSamp) != (1280, 2048, 1): raise ValueError('Unexpected material target')
        def geometry(event, primitive):
            ctl.SetFrameEvent(event, True); mesh = ctl.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2, 4) or not 16 <= mesh.vertexByteStride <= 256: raise ValueError('Invalid mesh layout')
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
        for mode in ('original', 'control', 'precise', 'restored'):
            if replacement is not None:
                ctl.RemoveReplacement(original); ctl.FreeTargetResource(replacement); replacement = None
            if mode in ('control', 'precise'):
                replacement, errors = ctl.BuildTargetShader('main', rd.ShaderEncoding.DXBC,
                    raw if mode == 'control' else precise, rd.ShaderCompileFlags(), rd.ShaderStage.Vertex)
                if replacement == rd.ResourceId.Null() or errors: raise RuntimeError('Shader build failed: '+errors)
                ctl.ReplaceResource(original, replacement)
            run = {'mode': mode, 'geometry': [], 'pixels': []}; report['runs'].append(run)
            for event, primitive in ((3775, 293), (10072, 293), (3781, 256), (10097, 256)):
                pos, other = geometry(event, primitive)
                if mode == 'original': baseline_mesh[event] = pos, other
                elif other != baseline_mesh[event][1] or ((mode != 'precise' or event not in (10072, 10097)) and pos != baseline_mesh[event][0]):
                    raise ValueError('Changed material outputs, unchanged prepass, or failed control/restoration')
                run['geometry'].append({'event': event, 'positions_hex': pos.hex(), 'viewport': rd.DumpObject(ctl.GetPipelineState().GetViewport(0)), 'material_sha256': hashlib.sha256(other).hexdigest()})
            for sample in (0,):
                sub = rd.Subresource(); sub.sample = sample
                for x, y in ((875, 166), (874, 166), (875, 167), (800, 200),
                             (1020, 97), (917, 149), (964, 102)):
                    ctl.SetFrameEvent(10120, True)
                    history = ctl.PixelHistory(color.resourceId, x, y, sub, rd.CompType.Float)
                    run['pixels'].append({'x': x, 'y': y, 'sample': sample, 'history': [
                        {'event': h.eventId, 'primitive': h.primitiveID, 'passed': h.Passed(), 'details': rd.DumpObject(h)}
                        for h in history if h.eventId <= 10120]})
            ctl.SetFrameEvent(display_event, True)
            frame = bytes(ctl.GetTextureData(display.resourceId, rd.Subresource()))
            if len(frame) != 1280*2048*4: raise ValueError('Unexpected display packing')
            run['frame_sha256'] = hashlib.sha256(frame).hexdigest()
            if mode == 'original': baseline_frame = frame
            elif mode in ('control', 'restored') and frame != baseline_frame: raise ValueError('Frame control/restoration mismatch')
            tex_save = rd.TextureSave(); tex_save.resourceId = display.resourceId; tex_save.destType = rd.FileType.PNG
            status = ctl.SaveTexture(tex_save, os.path.join(out, mode+'.png'))
            if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
            run['coverage'] = [rd.DumpObject(r) for r in ctl.FetchCounters([rd.GPUCounter.SamplesPassed]) if r.eventId in (10072, 10077, 10082, 10087, 10092, 10097, 10103, 10117)]
            save()
        precise_run = report['runs'][2]
        report['visible_material_passes'] = any(h['event'] == 10072 and h['passed'] for h in precise_run['pixels'][0]['history'])
        report['nearer_occluder_preserved'] = not any(h['passed'] and 10072 <= h['event'] <= 10117 for h in precise_run['pixels'][2]['history'])
        if not report['nearer_occluder_preserved']: raise ValueError('Unlit material leaked through nearer geometry')
        # Match clip depth/W exactly; X/Y retain the game's quarter-pixel phase.
        geometry_by_event = {g['event']: g for g in precise_run['geometry']}
        for depth, color_event in ((3775, 10072), (3781, 10097)):
            a = struct.unpack('<12f', bytes.fromhex(geometry_by_event[depth]['positions_hex']))
            b = struct.unpack('<12f', bytes.fromhex(geometry_by_event[color_event]['positions_hex']))
            for i in range(0, 12, 4):
                if a[i+2:i+4] != b[i+2:i+4]: raise ValueError('Depth/W correspondence failed')
                # Float32 clip-coordinate quantization contributes up to 3e-5 px here.
                phase = ((b[i]/b[i+3]-a[i]/a[i+3])*640,
                         (b[i+1]/b[i+3]-a[i+1]/a[i+3])*-360)
                if any(abs(v-.25) > 3e-5 for v in phase): raise ValueError('Changed raster phase')
        for x, y, event, primitive in ((1020, 97, 10072, 290),
                                      (917, 149, 10117, 28), (964, 102, 10072, 289)):
            def hit(run):
                pixel = next(p for p in run['pixels'] if (p['x'], p['y']) == (x, y))
                return next(h for h in pixel['history'] if h['event'] == event and h['primitive'] == primitive)
            before, after = hit(report['runs'][0]), hit(precise_run)
            if before['passed'] or before['details']['depthTestFailed'] != 'True' or not after['passed']:
                raise ValueError('Expected rejected material pixel was not restored')
        report['depth_and_w_exact'] = True
        report['quarter_pixel_phase_preserved'] = True
        report['three_rejected_pixels_restored'] = True
        report['restoration_exact'] = True
        if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('Replay GPU error')
        report['complete'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if ctl is not None:
            if replacement is not None and replacement != rd.ResourceId.Null():
                ctl.RemoveReplacement(original); ctl.FreeTargetResource(replacement)
            ctl.Shutdown()
        cap.Shutdown(); save()


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'unlit-validation-error.txt'), 'w') as f: f.write(traceback.format_exc())
sys.exit(0)
