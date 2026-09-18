"""GPU clipping controls on four captured equipment draws, with nonzero planes.

RenderDoc Python: AOT_RENDERDOC_PROBE, AOT_PROJECTION_CLIP_VARIANTS,
AOT_PROJECTION_CLIP_NAME. Native game must already have exited normally.
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
    probe=os.environ['AOT_RENDERDOC_PROBE']
    variants=os.environ['AOT_PROJECTION_CLIP_VARIANTS']
    name=os.environ['AOT_PROJECTION_CLIP_NAME']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name): raise ValueError('Require simple output name')
    with open(os.path.join(probe,'probe.json')) as f: done=json.load(f)
    if done.get('timed_out') or done.get('exit_code_before_cleanup')!=0: raise ValueError('Require normally closed capture')
    with open(os.path.join(probe,'renderdoc-capture.json')) as f: capture=json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(capture))!=os.path.normcase(os.path.abspath(probe)):
        raise ValueError('Capture does not belong to probe')
    decal=os.environ.get('AOT_PROJECTION_CLIP_KIND')=='decal'
    courtyard=os.environ.get('AOT_PROJECTION_CLIP_KIND')=='courtyard'
    reference_path=os.path.join(probe,'courtyard-clip-reference' if courtyard else
                                'decal-clip-reference' if decal else 'projection-vertices-v2-001','vertices.json')
    with open(reference_path,'rb') as f: reference_raw=f.read()
    reference=json.loads(reference_raw)
    if reference.get('error') or not reference.get('restoration_exact') or reference['capture']!=capture:
        raise ValueError('Invalid reference replay')
    expected={run['mode']:{r['event']:r for r in run['draws']} for run in reference['runs']}
    manifest_path=os.path.join(variants,'clip-variants.json')
    with open(manifest_path,'rb') as f: manifest_raw=f.read()
    manifest=json.loads(manifest_raw)
    if [case['kind'] for case in manifest['cases']]!=['clip','cull']:
        raise ValueError('Require both clipping and culling cases')
    if manifest['planes'][:4]!=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]] or len(manifest['planes'])!=6:
        raise ValueError('Require four coordinate planes and two mixed planes')
    for case in manifest['cases']:
        required=([('courtyard',[4881,4888])] if courtyard else
                  [('decal',[11781,11796])] if decal else [('color',[7428,7456]),('depth',[4902,4918])])
        if [(p['name'],p['events']) for p in case['programs']]!=required:
            raise ValueError('Require all selected reference draws')
    out=os.path.join(probe,name);os.mkdir(out)
    report={'capture':capture,'reference_sha256':hashlib.sha256(reference_raw).hexdigest(),
            'manifest_sha256':hashlib.sha256(manifest_raw).hexdigest(),'cases':[],'passed':False}
    def save():
        with open(os.path.join(out,'clip-results.json'),'w') as f: json.dump(report,f,indent=2)
    cap,controller=rd.OpenCaptureFile(),None
    try:
        status=cap.OpenFile(capture,'',None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status,controller=cap.OpenCapture(rd.ReplayOptions(),None)
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        for case in manifest['cases']:
            result={'kind':case['kind'],'runs':[]};report['cases'].append(result)
            owned=[]
            for program in case['programs']:
                controller.SetFrameEvent(program['events'][0],True)
                pipe=controller.GetPipelineState()
                owned.append({'id':pipe.GetShader(rd.ShaderStage.Vertex),'replacement':None,'program':program})
            base_planes={}
            for mode in ['original','precise','faulty','restored']:
                run={'mode':mode,'draws':[],'builds':[]};result['runs'].append(run);save()
                try:
                    if mode!='restored':
                        for item in owned:
                            variant=item['program'][mode]
                            with open(os.path.join(variants,variant['file']),'rb') as f: raw=f.read()
                            if hashlib.sha256(raw).hexdigest()!=variant['sha256']: raise ValueError('Replacement hash mismatch')
                            replacement,errors=controller.BuildTargetShader('main',rd.ShaderEncoding.DXBC,raw,rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
                            if replacement==rd.ResourceId.Null(): raise RuntimeError('Shader build failed: '+errors)
                            item['replacement']=replacement
                            if errors: raise RuntimeError(errors)
                            controller.ReplaceResource(item['id'],replacement)
                            run['builds'].append({'file':variant['file'],'sha256':variant['sha256'],'messages':errors})
                    for item in owned:
                        for event in item['program']['events']:
                            prior=expected['baseline'][event]
                            controller.SetFrameEvent(event,True)
                            mesh=controller.GetPostVSData(0,0,rd.MeshDataStage.VSOut)
                            base_stride=prior['stride'];stride=mesh.vertexByteStride
                            if stride!=base_stride+(0 if mode=='restored' else 24):
                                raise ValueError('Unexpected output stride at %d: %d, base %d' % (event,stride,base_stride))
                            if mesh.indexByteStride not in (2,4): raise ValueError('Unexpected index type')
                            indices_raw=bytes(controller.GetBufferData(mesh.indexResourceId,mesh.indexByteOffset,prior['indices']*mesh.indexByteStride))
                            if hashlib.sha256(indices_raw).hexdigest()!=prior['index_sha256']: raise ValueError('Changed index bytes')
                            indices=sorted({i+mesh.baseVertex for i in struct.unpack('<'+str(prior['indices'])+('H' if mesh.indexByteStride==2 else 'I'),indices_raw)})
                            if indices!=prior['referenced_indices']: raise ValueError('Changed vertex references')
                            size=(indices[-1]-indices[0]+1)*stride
                            if size>16*1024*1024: raise ValueError('Unbounded vertex output')
                            raw=bytes(controller.GetBufferData(mesh.vertexResourceId,mesh.vertexByteOffset+indices[0]*stride,size))
                            if len(raw)!=size: raise ValueError('Short vertex output')
                            reflection=controller.GetPipelineState().GetShaderReflection(rd.ShaderStage.Vertex)
                            run['layout']=[{'semantic':s.semanticName,'index':s.semanticIndex,'register':s.regIndex,
                                            'components':s.compCount,'mask':s.regChannelMask,'system':str(s.systemValue)}
                                           for s in reflection.outputSignature]
                            run['first_vertex_raw']=list(struct.unpack('<'+'f'*(stride//4),raw[:stride]))
                            save()
                            positions,others,planes,records=[],[],[],[]
                            for index in indices:
                                at=(index-indices[0])*stride;record=raw[at:at+stride]
                                positions.append(record[:16]);others.append(record[16:base_stride]);records.append(record)
                                if mode!='restored':
                                    values=struct.unpack('<6f',record[base_stride:base_stride+24])
                                    if not all(math.isfinite(v) for v in values):
                                        raise ValueError('Invalid clip distance')
                                    # Unit planes expose the original guest clip coordinates.
                                    for n in (4,5):
                                        calculated=sum(a*b for a,b in zip(values[:4],manifest['planes'][n]))
                                        if abs(values[n]-calculated)>max(.0002,abs(calculated)*2e-6):
                                            raise ValueError('Mixed plane mismatch: event=%d vertex=%d mode=%s values=%s expected=%s base=%d stride=%d' % (event,index,mode,values,calculated,base_stride,stride))
                                    planes.append(record[base_stride:base_stride+24])
                            sha=lambda b:hashlib.sha256(b).hexdigest()
                            position_sha=sha(b''.join(positions));other_sha=sha(b''.join(others))
                            wanted=expected['precise' if mode in ('precise','faulty') else 'baseline'][event]
                            if position_sha!=wanted['position_sha256'] or other_sha!=prior['other_sha256']:
                                raise ValueError('Position or original interpolator output changed unexpectedly')
                            blob=b''.join(records);filename='%s-%s-%d.bin' % (case['kind'],mode,event)
                            with open(os.path.join(out,filename),'wb') as f: f.write(blob)
                            row={'event':event,'vertices':len(indices),'stride':stride,'base_stride':base_stride,
                                 'output_file':filename,'output_sha256':sha(blob),'position_sha256':position_sha,
                                 'other_sha256':other_sha}
                            if planes:
                                plane_blob=b''.join(planes)
                                row['plane_sha256']=sha(plane_blob)
                                row['first_vertex_planes']=list(struct.unpack('<6f',planes[0]))
                                if mode=='original':
                                    base_planes[event]=plane_blob
                                    if any(max(abs(struct.unpack('<6f',p)[i]) for p in planes)<.001 for i in range(6)):
                                        raise ValueError('A control plane is effectively zero')
                                elif mode=='precise' and plane_blob!=base_planes[event]:
                                    raise ValueError('Correction changed user clip distances')
                                elif mode=='faulty' and plane_blob==base_planes[event]:
                                    raise ValueError('Wrong-space positive control was not detected')
                            run['draws'].append(row);save()
                finally:
                    for item in owned:
                        if item['replacement'] is not None:
                            controller.RemoveReplacement(item['id'])
                            controller.FreeTargetResource(item['replacement'])
                            item['replacement']=None
        status=controller.GetFatalErrorStatus()
        if status!=rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        report['passed']=True
    except BaseException:
        report['error']=traceback.format_exc()
    finally:
        if controller:controller.Shutdown()
        cap.Shutdown();save()


try:main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'],'clip-launch-error.txt'),'w') as f:f.write(traceback.format_exc())
sys.exit(0)
