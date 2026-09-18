"""Validate native paired wall projection with actual colors and occlusion.

Uses the closed aligned impact capture, AOT_WALL_VARIANTS and AOT_WALL_NAME.
Original replacement and restoration are checked without altering materials.
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
    name = os.environ['AOT_WALL_NAME']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name): raise ValueError('Invalid output name')
    with open(os.path.join(root, 'probe.json')) as f: native = json.load(f)
    with open(os.path.join(root, 'aligned-impact-births.json')) as f: births = json.load(f)
    with open(os.path.join(root, 'aligned-impact-color', 'color.json')) as f: colors = json.load(f)
    variant_dir = os.environ['AOT_WALL_VARIANTS']
    with open(os.path.join(variant_dir, 'variants.json')) as f: manifest = json.load(f)
    if native.get('timed_out') or native.get('exit_code_before_cleanup') != 0: raise ValueError('Require closed native probe')
    path = births['capture']
    if (os.path.normcase(os.path.dirname(path)) != os.path.normcase(root) or not colors.get('complete') or
            colors['capture'] != path or colors['query']['crop'] != [550,180,170,170]):
        raise ValueError('Require matching completed baseline')
    variants = manifest['variants']
    if len(variants) != 2 or [v['event'] for v in variants] != [157448,158223]:
        raise ValueError('Require paired wall fixtures')
    out = os.path.join(root, name); os.mkdir(out)
    report = {'capture': path, 'variants': manifest, 'complete': False, 'runs': []}
    cap, controller, owned = rd.OpenCaptureFile(), None, []
    baseline, vertex_baseline, histories = {}, {}, {}
    def save():
        with open(os.path.join(out, 'validation.json'), 'w') as f: json.dump(report, f, indent=2)
    try:
        status = cap.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        textures = {str(t.resourceId): t for t in controller.GetTextures()}
        tex = textures['ResourceId::6248']
        if (tex.width,tex.height,tex.msSamp,tex.format.Name()) != (1280,2048,1,'R16G16B16A16_FLOAT'):
            raise ValueError('Unexpected HDR target')
        for variant in variants:
            controller.SetFrameEvent(variant['event'], True)
            pipe = controller.GetPipelineState()
            raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
            if hashlib.sha256(raw).hexdigest() != variant['original_sha256']: raise ValueError('Wrong original shader')
            with open(os.path.join(variant_dir,variant['file']), 'rb') as f: code = f.read()
            if hashlib.sha256(code).hexdigest() != variant['sha256']: raise ValueError('Changed variant')
            owned.append({'original': pipe.GetShader(rd.ShaderStage.Vertex), 'raw': raw, 'code': code, 'target': None})
        events = [(e['epoch'],e['events'][-1]) for e in births['epochs'] if e['events']]
        if len(events) != 51: raise ValueError('Require 51 material frames')
        expected = {f['event']: f['sha256'] for f in colors['frames']}
        def crop(event):
            controller.SetFrameEvent(event,True)
            raw = bytes(controller.GetTextureData(tex.resourceId,rd.Subresource()))
            if len(raw) != 1280*2048*8: raise ValueError('Unexpected HDR packing')
            return b''.join(raw[((180+y)*1280+550)*8:((180+y)*1280+720)*8] for y in range(170))
        def geometry(event,primitive):
            controller.SetFrameEvent(event,True)
            mesh = controller.GetPostVSData(0,0,rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2,4) or not 16 <= mesh.vertexByteStride <= 256:
                raise ValueError('Unexpected geometry layout')
            ib = bytes(controller.GetBufferData(mesh.indexResourceId,mesh.indexByteOffset+primitive*3*mesh.indexByteStride,3*mesh.indexByteStride))
            indices = struct.unpack('<3'+('H' if mesh.indexByteStride==2 else 'I'),ib)
            pos, other = b'', b''
            for index in indices:
                index += mesh.baseVertex
                if not 0 <= index < 100000: raise ValueError('Invalid vertex index')
                raw = bytes(controller.GetBufferData(mesh.vertexResourceId,mesh.vertexByteOffset+index*mesh.vertexByteStride,mesh.vertexByteStride))
                if len(raw) != mesh.vertexByteStride: raise ValueError('Short vertex readback')
                pos += raw[:16]; other += raw[16:]
            return pos, other
        for mode in ('original','control','precise','restored'):
            for item in owned:
                if item['target'] is not None:
                    controller.RemoveReplacement(item['original']);controller.FreeTargetResource(item['target']);item['target']=None
                if mode in ('control','precise'):
                    target, errors = controller.BuildTargetShader('main',rd.ShaderEncoding.DXBC,
                        item['raw'] if mode=='control' else item['code'],rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
                    if target != rd.ResourceId.Null(): item['target']=target
                    if target == rd.ResourceId.Null() or errors: raise RuntimeError('Shader build failed: '+errors)
                    controller.ReplaceResource(item['original'],target)
            run = {'mode': mode, 'frames': [], 'geometry': [], 'pixels': []};report['runs'].append(run)
            for event,primitive in ((157448,269),(158223,269),(159821,0)):
                vertices = geometry(event,primitive)
                if mode=='original': vertex_baseline[event]=vertices
                elif vertices[1]!=vertex_baseline[event][1] or ((mode!='precise' or event==159821) and vertices[0]!=vertex_baseline[event][0]):
                    raise ValueError('Changed interpolators or failed vertex control')
                run['geometry'].append({'event':event,'positions_hex':vertices[0].hex(),
                                        'other_sha256':hashlib.sha256(vertices[1]).hexdigest()})
            for epoch,event in events:
                if mode=='control' and epoch not in (10,20,30,59): continue
                raw = crop(event);sha=hashlib.sha256(raw).hexdigest()
                if mode=='original':
                    if sha != expected[event]: raise ValueError('Original material crop failed to reproduce')
                    baseline[event]=raw
                elif mode!='precise' and raw!=baseline[event]: raise ValueError('Color control/restoration mismatch')
                filename='%s-%d.rgba16f'%(mode,epoch)
                with open(os.path.join(out,filename),'wb') as f:f.write(raw)
                run['frames'].append({'epoch':epoch,'event':event,'file':filename,'sha256':sha})
                save()
            if mode!='control':
                for event,first,x,y in ((159821,155230,620,260),(438896,434208,642,273)):
                    controller.SetFrameEvent(event,True)
                    history = controller.PixelHistory(tex.resourceId,x,y,rd.Subresource(),rd.CompType.Float)
                    rows=[{'event':h.eventId,'primitive':h.primitiveID,'passed':h.Passed(),'details':rd.DumpObject(h)}
                          for h in history if first<=h.eventId<=event]
                    if not rows: raise ValueError('Empty pixel history')
                    key=(event,x,y)
                    if mode=='original': histories[key]=rows
                    elif mode=='restored' and rows!=histories[key]: raise ValueError('Pixel history restoration mismatch')
                    run['pixels'].append({'event':event,'pixel':[x,y],'history':rows})
            save()
        if controller.GetFatalErrorStatus()!=rd.ResultCode.Succeeded: raise RuntimeError('Fatal replay error')
        report.update(complete=True,restoration_exact=True,original_replacement_exact=True)
    except BaseException: report['error']=traceback.format_exc()
    finally:
        if controller:
            for item in owned:
                if item['target'] is not None:
                    controller.RemoveReplacement(item['original']);controller.FreeTargetResource(item['target'])
            controller.Shutdown()
        cap.Shutdown();save()
    return int(not report['complete'])


sys.exit(main())
