"""Check every referenced vertex in the 40 matched depth/main-color draw pairs.

RenderDoc Python: AOT_RENDERDOC_PROBE is the closed equipment capture;
AOT_PROJECTION_VARIANTS contains the guarded native-patcher variants;
AOT_PROJECTION_VERTICES_NAME names a new output directory within the probe.
"""
import hashlib
import json
import math
import os
import re
import struct
import sys
import traceback
import renderdoc as rd


def main():
    probe = os.environ['AOT_RENDERDOC_PROBE']
    variants = os.environ['AOT_PROJECTION_VARIANTS']
    name = os.environ['AOT_PROJECTION_VERTICES_NAME']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name): raise ValueError('Require simple output name')
    with open(os.path.join(probe, 'probe.json')) as source: done = json.load(source)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0: raise ValueError('Require normally closed probe')
    with open(os.path.join(probe, 'renderdoc-capture.json')) as source: path = json.load(source)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(os.path.abspath(probe)):
        raise ValueError('Capture must belong to probe')
    inventory = os.path.join(probe, 'projection-pairs-001')
    with open(os.path.join(inventory, 'correspondence.json')) as source: pairs = json.load(source)
    if pairs['capture'] != path or pairs.get('unmatched') or len(pairs['pairs']) != 40:
        raise ValueError('Require complete matching correspondence')
    with open(os.path.join(inventory, 'pairs.json'), 'rb') as source:
        if hashlib.sha256(source.read()).hexdigest() != pairs['inventory_sha256']:
            raise ValueError('Changed source inventory')
    with open(os.path.join(variants, 'variants.json')) as source: manifest = json.load(source)
    if [v['layout']['name'] for v in manifest['variants']] != ['color','depth']:
        raise ValueError('Require paired guarded variants')
    out = os.path.join(probe, name)
    os.mkdir(out)
    report = {'capture': path, 'scope': __doc__.strip(), 'runs': []}
    def save():
        with open(os.path.join(out, 'vertices.json'), 'w') as target: json.dump(report, target, indent=2)
    cap, controller, owned = rd.OpenCaptureFile(), None, []
    try:
        status = cap.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        for variant, event in zip(manifest['variants'], [7428,4902]):
            controller.SetFrameEvent(event, True)
            pipe = controller.GetPipelineState()
            raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
            if hashlib.sha256(raw).hexdigest() != variant['original_sha256']:
                raise ValueError('Original vertex shader mismatch')
            owned.append({'original': pipe.GetShader(rd.ShaderStage.Vertex), 'replacement': None, 'variant': variant})
        events = {}
        for pair in pairs['pairs']:
            if len(pair['matching_viewport_color_events']) != 1: raise ValueError('Ambiguous main color draw')
            for event in [pair['depth_event'], pair['matching_viewport_color_events'][0]]:
                if event in events: raise ValueError('Duplicate paired draw')
                events[event] = pair['indices']
        if len(events) != 80: raise ValueError('Expected 80 distinct draws')
        def read_vertices(event, count, mode):
            controller.SetFrameEvent(event, True)
            mesh = controller.GetPostVSData(0,0,rd.MeshDataStage.VSOut)
            if mesh.indexByteStride not in (2,4) or not 16 <= mesh.vertexByteStride <= 128:
                raise ValueError('Unexpected mesh output layout')
            if not 0 < count <= 65536: raise ValueError('Unbounded index count')
            index_data = bytes(controller.GetBufferData(mesh.indexResourceId,mesh.indexByteOffset,count*mesh.indexByteStride))
            if len(index_data) != count*mesh.indexByteStride: raise ValueError('Short index readback')
            indices = struct.unpack('<'+str(count)+('H' if mesh.indexByteStride==2 else 'I'),index_data)
            referenced = sorted({int(i)+mesh.baseVertex for i in indices})
            if referenced[0] < 0 or referenced[-1] >= 100000: raise ValueError('Invalid referenced vertex')
            span = (referenced[-1]-referenced[0]+1)*mesh.vertexByteStride
            if span > 16*1024*1024: raise ValueError('Unbounded vertex readback')
            raw = bytes(controller.GetBufferData(mesh.vertexResourceId,
                mesh.vertexByteOffset+referenced[0]*mesh.vertexByteStride,span))
            if len(raw) != span: raise ValueError('Short vertex readback')
            positions, other = [], hashlib.sha256()
            for index in referenced:
                at = (index-referenced[0])*mesh.vertexByteStride
                position = raw[at:at+16]
                if not all(math.isfinite(v) for v in struct.unpack('<4f', position)):
                    raise ValueError('Nonfinite referenced position at event %d vertex %d' % (event,index))
                positions.append(position)
                other.update(raw[at+16:at+mesh.vertexByteStride])
            blob = b''.join(positions)
            filename = '%s-%d-positions.bin' % (mode,event)
            with open(os.path.join(out,filename),'wb') as target: target.write(blob)
            return {'event':event,'indices':count,'vertex_count':len(referenced),
                'referenced_indices':referenced,'index_sha256':hashlib.sha256(index_data).hexdigest(),
                'position_sha256':hashlib.sha256(blob).hexdigest(),'other_sha256':other.hexdigest(),
                'stride':mesh.vertexByteStride,'positions_file':filename}
        baseline = None
        for mode in ['baseline','precise','fallback','restored']:
            run = {'mode':mode,'builds':[],'draws':[],'pairs':[]}
            report['runs'].append(run)
            save()
            try:
                if mode in ('precise','fallback'):
                    for item in owned:
                        v = item['variant']
                        filename, digest = (v['file'],v['sha256']) if mode=='precise' else (v['fallback_file'],v['fallback_sha256'])
                        with open(os.path.join(variants,filename),'rb') as source: raw = source.read()
                        if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError('Changed replacement shader')
                        replacement, errors = controller.BuildTargetShader('main',rd.ShaderEncoding.DXBC,raw,rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
                        if replacement==rd.ResourceId.Null(): raise RuntimeError('Build failed: '+errors)
                        item['replacement']=replacement
                        if errors: raise RuntimeError('Shader build diagnostics: '+errors)
                        controller.ReplaceResource(item['original'],replacement)
                        run['builds'].append({'name':v['layout']['name'],'sha256':digest,'messages':errors})
                for event,count in sorted(events.items()): run['draws'].append(read_vertices(event,count,mode))
                current = {r['event']:r for r in run['draws']}
                if baseline is None: baseline=current
                for event,row in current.items():
                    before=baseline[event]
                    keys=['referenced_indices','index_sha256','other_sha256','stride']
                    if mode!='precise': keys.append('position_sha256')
                    if any(row[k]!=before[k] for k in keys):
                        raise ValueError('Output/control mismatch at event %d in %s' % (event,mode))
                for pair in pairs['pairs']:
                    d,c=current[pair['depth_event']],current[pair['matching_viewport_color_events'][0]]
                    same_indices=d['referenced_indices']==c['referenced_indices'] and d['index_sha256']==c['index_sha256']
                    same_positions=d['position_sha256']==c['position_sha256']
                    run['pairs'].append({'depth_event':d['event'],'color_event':c['event'],
                        'vertices':d['vertex_count'],'same_indices':same_indices,'same_positions':same_positions})
                run['matching_pairs']=sum(p['same_indices'] and p['same_positions'] for p in run['pairs'])
                run['compared_vertices']=sum(p['vertices'] for p in run['pairs'])
                run['changed_position_draws']=sum(r['position_sha256']!=baseline[r['event']]['position_sha256'] for r in run['draws'])
                save()
            finally:
                for item in owned:
                    if item['replacement'] is not None:
                        controller.RemoveReplacement(item['original'])
                        controller.FreeTargetResource(item['replacement'])
                        item['replacement']=None
        report['restoration_exact']=True
        if any(run['matching_pairs']!=40 for run in report['runs']):
            raise ValueError('Depth/main-color vertex positions disagree; inspect per-pair results')
        status=controller.GetFatalErrorStatus()
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
    except BaseException:
        report['error']=traceback.format_exc()
    finally:
        if controller: controller.Shutdown()
        cap.Shutdown()
        save()


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'],'projection-vertices-launch-error.txt'),'w') as target:
        target.write(traceback.format_exc())
sys.exit(0)
