"""Replay the captured palm material against its corrected shared depth pass.

RenderDoc Python: AOT_RENDERDOC_PROBE, AOT_PALM_VARIANT. Native capture must
already be closed. Checks original replacement and exact restoration; changes
only the palm family's vertex projection, never depth state or pixel shading.
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
    variant_dir = os.environ['AOT_PALM_VARIANT']
    with open(os.path.join(root, 'probe.json')) as f: done = json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f:
        path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root):
        raise ValueError('Capture must belong to probe')
    with open(os.path.join(variant_dir, 'variant.json')) as f: variant = json.load(f)
    if variant['event'] != 6318 or variant['original_sha256'] != 'bc4bfdc82c08dce699628636a714c778ad4e086c61c0d8a5c3880dafeb17d85d':
        raise ValueError('Unexpected palm fixture')
    out = os.path.join(root, 'palm-material-validation-001'); os.mkdir(out)
    report = {'capture': path, 'variant': variant, 'runs': [], 'complete': False}
    cap, ctl, original, replacement = rd.OpenCaptureFile(), None, None, None
    baseline_frame, baseline_mesh = None, {}
    def save():
        with open(os.path.join(out, 'validation.json'), 'w') as f: json.dump(report, f, indent=2)
    try:
        assert cap.OpenFile(path, '', None) == rd.ResultCode.Succeeded
        status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        ctl.SetFrameEvent(6318, True); pipe = ctl.GetPipelineState()
        raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
        original = pipe.GetShader(rd.ShaderStage.Vertex)
        if hashlib.sha256(raw).hexdigest() != variant['original_sha256']: raise ValueError('Wrong captured shader')
        with open(os.path.join(variant_dir, variant['file']), 'rb') as f: precise = f.read()
        if hashlib.sha256(precise).hexdigest() != variant['sha256']: raise ValueError('Changed patch')
        textures = {str(t.resourceId): t for t in ctl.GetTextures()}
        color, display = textures['ResourceId::6262'], textures['ResourceId::2669']
        if (color.width, color.height, color.msSamp) != (1280, 1024, 2): raise ValueError('Unexpected MSAA target')
        def geometry(event):
            ctl.SetFrameEvent(event, True); mesh = ctl.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2, 4) or not 16 <= mesh.vertexByteStride <= 256: raise ValueError('Invalid mesh layout')
            ib = bytes(ctl.GetBufferData(mesh.indexResourceId, mesh.indexByteOffset+93*3*mesh.indexByteStride, 3*mesh.indexByteStride))
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
            for event in (4359, 4362, 6318):
                pos, other = geometry(event)
                if mode == 'original': baseline_mesh[event] = pos, other
                elif other != baseline_mesh[event][1] or ((mode != 'precise' or event != 6318) and pos != baseline_mesh[event][0]):
                    raise ValueError('Changed material outputs, unchanged prepass, or failed control/restoration')
                run['geometry'].append({'event': event, 'positions_hex': pos.hex(), 'material_sha256': hashlib.sha256(other).hexdigest()})
            for sample in (0, 1):
                sub = rd.Subresource(); sub.sample = sample
                for x, y in ((1053, 73), (1050, 50), (1065, 70)):
                    ctl.SetFrameEvent(7550, True)
                    history = ctl.PixelHistory(color.resourceId, x, y, sub, rd.CompType.Float)
                    run['pixels'].append({'x': x, 'y': y, 'sample': sample, 'history': [
                        {'event': h.eventId, 'primitive': h.primitiveID, 'passed': h.Passed(), 'details': rd.DumpObject(h)}
                        for h in history if h.eventId <= 7550]})
            ctl.SetFrameEvent(13981, True)
            frame = bytes(ctl.GetTextureData(display.resourceId, rd.Subresource()))
            if len(frame) != 1280*2048*4: raise ValueError('Unexpected display packing')
            run['frame_sha256'] = hashlib.sha256(frame).hexdigest()
            if mode == 'original': baseline_frame = frame
            elif mode in ('control', 'restored') and frame != baseline_frame: raise ValueError('Frame control/restoration mismatch')
            tex_save = rd.TextureSave(); tex_save.resourceId = display.resourceId; tex_save.destType = rd.FileType.PNG
            status = ctl.SaveTexture(tex_save, os.path.join(out, mode+'.png'))
            if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
            save()
        precise_run = report['runs'][2]
        report['palm_pixels_pass'] = all(any(h['event'] == 6318 and h['passed'] for h in p['history']) for p in precise_run['pixels'])
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
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'palm-validation-error.txt'), 'w') as f: f.write(traceback.format_exc())
sys.exit(0)
