"""Isolate each remaining static projection replacement in the closed 04_05 frame.

RenderDoc Python: AOT_RENDERDOC_PROBE, AOT_STATIC_MATERIAL_VARIANTS and an
optional new AOT_STATIC_MATERIAL_NAME. Keeps exact controls/restoration, all
referenced paired vertices, unchanged material outputs, and occlusion samples.
Set AOT_STATIC_PAIR_KIND=lighting for later lighting passes; that mode requires
AOT_STATIC_BASE_VARIANTS containing the previously validated material pair.
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
    variant_dir = os.environ['AOT_STATIC_MATERIAL_VARIANTS']
    kind = os.environ.get('AOT_STATIC_PAIR_KIND', 'material')
    if kind not in ('material', 'lighting'): raise ValueError('Unknown pair kind')
    lighting = kind == 'lighting'
    expected = [('807B2A09A19C3B15',16548), ('494DCD69B7BA177C',16513)] if lighting else [('3306D6C23B238BE6',8969), ('AC2A17351535ED19',16293)]
    first_mode, second_mode = ('lighting-807b', 'lighting-494d') if lighting else ('material-3306', 'material-ac2a')
    exact_pairs = ((16177,16548),) if lighting else ((5093,8962), (5097,8969), (5105,8982))
    phase_pair = (4140,16513) if lighting else (4879,16293)
    events = (5097,16177,16548,4140,16513) if lighting else (5093,8962,5097,8969,5105,8982,4879,8311,16293)
    pixel_queries = (
        (16548, 'ResourceId::6398', ((771,143),(601,143),(546,255),(713,255),(921,141)), (0,)),
        (16513, 'ResourceId::6398', ((1198,559),(1201,477),(1190,645),(992,524),(1260,411),(954,502)), (0,))) if lighting else (
        (8969, 'ResourceId::6255', ((770,142),(601,143),(545,254),(713,254),(921,141)), (0,1)),
        (16293, 'ResourceId::6398', ((1269,143),(1127,46),(278,175),(1242,352)), (0,)))
    recovery_pixels = () if lighting else ((906,310,8982,2), (906,287,8982,2), (459,378,8962,192))
    with open(os.path.join(root, 'probe.json')) as f: done = json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0: raise ValueError('Require closed native probe')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f: path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root): raise ValueError('Foreign capture')
    with open(os.path.join(variant_dir, 'variants.json')) as f: variants = json.load(f)['variants']
    if [(v['guest'], v['event']) for v in variants] != expected:
        raise ValueError('Unexpected variants')
    name = os.environ.get('AOT_STATIC_MATERIAL_NAME', 'static-material-validation-001')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name): raise ValueError('Invalid output name')
    out = os.path.join(root, name); os.mkdir(out)
    report = {'capture': path, 'kind': kind, 'variants': variants, 'runs': [], 'complete': False}
    cap, ctl, owned, fixed = rd.OpenCaptureFile(), None, [], []
    baseline_frame, baseline_mesh = None, {}
    def save():
        with open(os.path.join(out, 'validation.json'), 'w') as f: json.dump(report, f, indent=2)
    def release():
        for item in owned:
            if item['replacement'] is not None:
                ctl.RemoveReplacement(item['original']); ctl.FreeTargetResource(item['replacement']); item['replacement'] = None
    try:
        assert cap.OpenFile(path, '', None) == rd.ResultCode.Succeeded
        status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        if lighting:
            base_dir = os.environ['AOT_STATIC_BASE_VARIANTS']
            with open(os.path.join(base_dir, 'variants.json')) as f: base = json.load(f)['variants']
            if [(v['guest'],v['event'],v['sha256']) for v in base] != [
                ('3306D6C23B238BE6',8969,'f348a828675d9b0ef98fc3e8a7a9fc2620014f5409df1090cfd04b5032ff5644'),
                ('AC2A17351535ED19',16293,'f96c864fcdda78946182db9a29360464dc45e6e659fa706f98649a3ba7adfd41')]:
                raise ValueError('Unexpected prior material corrections')
            report['fixed_baseline_variants'] = base
            for v in base:
                ctl.SetFrameEvent(v['event'], True); pipe = ctl.GetPipelineState()
                raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
                if hashlib.sha256(raw).hexdigest() != v['original_sha256']: raise ValueError('Wrong base shader')
                original = pipe.GetShader(rd.ShaderStage.Vertex)
                with open(os.path.join(base_dir, v['file']), 'rb') as f: precise = f.read()
                if hashlib.sha256(precise).hexdigest() != v['sha256']: raise ValueError('Changed base correction')
                replacement, errors = ctl.BuildTargetShader('main', rd.ShaderEncoding.DXBC, precise,
                    rd.ShaderCompileFlags(), rd.ShaderStage.Vertex)
                if replacement == rd.ResourceId.Null() or errors: raise RuntimeError('Base shader build failed: '+errors)
                fixed.append((original,replacement)); ctl.ReplaceResource(original,replacement)
        for v in variants:
            ctl.SetFrameEvent(v['event'], True); pipe = ctl.GetPipelineState()
            raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
            if hashlib.sha256(raw).hexdigest() != v['original_sha256']: raise ValueError('Wrong captured shader')
            with open(os.path.join(variant_dir, v['file']), 'rb') as f: precise = f.read()
            if hashlib.sha256(precise).hexdigest() != v['sha256']: raise ValueError('Changed replacement')
            owned.append({'original': pipe.GetShader(rd.ShaderStage.Vertex), 'replacement': None, 'raw': raw, 'precise': precise})
        textures = {str(t.resourceId): t for t in ctl.GetTextures()}
        displays = [t for t in textures.values() if (t.width, t.height, t.msSamp, t.format.Name()) == (1280, 2048, 1, 'R8G8B8A8_UNORM') and t.creationFlags & rd.TextureCategory.ColorTarget]
        if len(displays) != 1: raise ValueError('Ambiguous display target')
        display = displays[0]; report['display'] = str(display.resourceId)
        def last(nodes): return max([0]+[max(a.eventId, last(a.children)) for a in nodes])
        end = last(ctl.GetRootActions())
        def geometry(event):
            ctl.SetFrameEvent(event, True); mesh = ctl.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2, 4) or not 0 < mesh.numIndices < 4096 or not 16 <= mesh.vertexByteStride <= 256: raise ValueError('Invalid mesh')
            ib = bytes(ctl.GetBufferData(mesh.indexResourceId, mesh.indexByteOffset, mesh.numIndices*mesh.indexByteStride))
            indices = struct.unpack('<'+str(mesh.numIndices)+('H' if mesh.indexByteStride == 2 else 'I'), ib)
            refs = sorted({i+mesh.baseVertex for i in indices}); low, high = refs[0], refs[-1]
            if not 0 <= low <= high < 100000: raise ValueError('Invalid indices')
            raw = bytes(ctl.GetBufferData(mesh.vertexResourceId, mesh.vertexByteOffset+low*mesh.vertexByteStride, (high-low+1)*mesh.vertexByteStride))
            if len(raw) != (high-low+1)*mesh.vertexByteStride: raise ValueError('Short vertex read')
            pos, other = b'', b''
            for i in refs:
                at = (i-low)*mesh.vertexByteStride; pos += raw[at:at+16]; other += raw[at+16:at+mesh.vertexByteStride]
            return ib, refs, pos, other
        for mode in ('original', 'control', first_mode, second_mode, 'both', 'restored'):
            release()
            for i, item in enumerate(owned):
                if mode not in ('control', 'both', first_mode if i == 0 else second_mode): continue
                replacement, errors = ctl.BuildTargetShader('main', rd.ShaderEncoding.DXBC,
                    item['raw'] if mode == 'control' else item['precise'], rd.ShaderCompileFlags(), rd.ShaderStage.Vertex)
                if replacement == rd.ResourceId.Null() or errors: raise RuntimeError('Shader build failed: '+errors)
                item['replacement'] = replacement; ctl.ReplaceResource(item['original'], replacement)
            run = {'mode': mode, 'geometry': [], 'pixels': []}; report['runs'].append(run)
            current = {}
            for event in events:
                ib, refs, pos, other = geometry(event); current[event] = ib, refs, pos, other
                if mode == 'original': baseline_mesh[event] = current[event]
                before = baseline_mesh[event]
                changed_target = event in tuple(p[1] for p in exact_pairs) and mode in (first_mode, 'both') or event == phase_pair[1] and mode in (second_mode, 'both')
                if (ib, refs, other) != (before[0], before[1], before[3]) or not changed_target and pos != before[2]:
                    raise ValueError('Material output or control/restoration mismatch')
                filename = mode+'-'+str(event)+'-positions.bin'
                with open(os.path.join(out, filename), 'wb') as f: f.write(pos)
                run['geometry'].append({'event': event, 'vertices': len(refs), 'position_file': filename,
                    'position_sha256': hashlib.sha256(pos).hexdigest(), 'material_sha256': hashlib.sha256(other).hexdigest()})
            if mode in (first_mode, 'both'):
                for depth, material in exact_pairs:
                    if current[depth][:3] != current[material][:3]: raise ValueError('Material/prepass indices or positions differ')
            if mode in (second_mode, 'both'):
                if current[phase_pair[0]][:2] != current[phase_pair[1]][:2]: raise ValueError('Phased material/prepass indices differ')
                a, b = current[phase_pair[0]][2], current[phase_pair[1]][2]; phases = []
                for off in range(0, len(a), 16):
                    pa, pb = struct.unpack_from('<4f', a, off), struct.unpack_from('<4f', b, off)
                    if a[off+8:off+16] != b[off+8:off+16]: raise ValueError('Phased material/prepass depth or W differs')
                    if pa[3] > 10 and abs(pa[0]/pa[3]) < 2 and abs(pa[1]/pa[3]) < 2:
                        phase = ((pb[0]/pb[3]-pa[0]/pa[3])*640, (pb[1]/pb[3]-pa[1]/pa[3])*-360)
                        if any(abs(v-.25) > .0002 for v in phase): raise ValueError('Material raster phase changed')
                        phases.append(phase)
                if not phases: raise ValueError('No visible raster-phase vertices')
                run['raster_phase_vertices'] = len(phases)
                run['max_phase_error_pixels'] = max(abs(v-.25) for p in phases for v in p)
            # Passing material and nearer-occluder controls in both target paths.
            for event, resource, points, samples in pixel_queries:
                for x, y in points:
                    for sample in samples:
                        ctl.SetFrameEvent(event, True); sub = rd.Subresource(); sub.sample = sample
                        history = ctl.PixelHistory(textures[resource].resourceId, x, y, sub, rd.CompType.Float)
                        run['pixels'].append({'event': event, 'x': x, 'y': y, 'sample': sample, 'history': [
                            {'event': h.eventId, 'primitive': h.primitiveID, 'passed': h.Passed(), 'details': rd.DumpObject(h)}
                            for h in history if h.eventId <= event]})
            for x, y, event, primitive in recovery_pixels:
                for sample in (0,1):
                    ctl.SetFrameEvent(9300, True); sub = rd.Subresource(); sub.sample = sample
                    history = ctl.PixelHistory(textures['ResourceId::6255'].resourceId, x, y, sub, rd.CompType.Float)
                    run['pixels'].append({'event': event, 'primitive': primitive, 'x': x, 'y': y, 'sample': sample,
                        'restoration': True, 'history': [{'event': h.eventId, 'primitive': h.primitiveID,
                        'passed': h.Passed(), 'details': rd.DumpObject(h)} for h in history if h.eventId <= 9300]})
            ctl.SetFrameEvent(end, True); frame = bytes(ctl.GetTextureData(display.resourceId, rd.Subresource()))
            if len(frame) != 1280*2048*4: raise ValueError('Unexpected display packing')
            run['frame_sha256'] = hashlib.sha256(frame).hexdigest()
            if mode == 'original': baseline_frame = frame
            elif mode in ('control', 'restored') and frame != baseline_frame: raise ValueError('Frame control/restoration mismatch')
            with open(os.path.join(out, mode+'.rgba'), 'wb') as f: f.write(frame)
            tex_save = rd.TextureSave(); tex_save.resourceId = display.resourceId; tex_save.destType = rd.FileType.PNG
            if ctl.SaveTexture(tex_save, os.path.join(out, mode+'.png')) != rd.ResultCode.Succeeded: raise RuntimeError('Save failed')
            save()
        # These controls already passed or were genuinely hidden before the patch.
        for run in report['runs']:
            for before, after in zip(report['runs'][0]['pixels'], run['pixels']):
                if before.get('restoration'):
                    def hit(pixel, primitive):
                        return next(h for h in pixel['history'] if h['event'] == pixel['event'] and h['primitive'] == primitive)
                    b, a = hit(before, before['primitive']), hit(after, after['primitive'])
                    if run['mode'] in (first_mode, 'both'):
                        if not a['passed']: raise ValueError('Rejected visible material was not restored')
                    elif b['passed'] != a['passed']: raise ValueError('Unexpected material control change')
                    if before['sample'] == 0 and (b['passed'] or b['details']['depthTestFailed'] != 'True'):
                        raise ValueError('Original fixture did not reproduce rejection')
                    if before['event'] == 8962 and before['sample'] == 0 and hit(after, 303)['passed']:
                        raise ValueError('Hidden door-trim primitive leaked through')
                    continue
                expected = [(h['primitive'], h['passed']) for h in before['history'] if h['event'] == before['event']]
                actual = [(h['primitive'], h['passed']) for h in after['history'] if h['event'] == after['event']]
                if expected != actual: raise ValueError('Passing material or nearer-occluder control changed')
        if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('Replay GPU error')
        report.update(complete=True, paired_vertices=sum(len(current[p[0]][1]) for p in exact_pairs+(phase_pair,)),
                      restored_pixel_checks=len(recovery_pixels), material_outputs_unchanged=True, controls_preserved=True, restoration_exact=True)
    except BaseException: report['error'] = traceback.format_exc()
    finally:
        if ctl is not None:
            release()
            for original, replacement in fixed:
                ctl.RemoveReplacement(original); ctl.FreeTargetResource(replacement)
            ctl.Shutdown()
        cap.Shutdown(); save()


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'static-material-validation-error.txt'), 'w') as f: f.write(traceback.format_exc())
sys.exit(0)
