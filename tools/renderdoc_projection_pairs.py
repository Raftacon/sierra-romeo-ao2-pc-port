"""Audit main-scene uses of the corrected depth shader against color geometry.

Read-only first-frame inventory for the closed equipment sequence capture.
Run with AOT_RENDERDOC_PROBE and a new AOT_PROJECTION_PAIRS_NAME in RenderDoc Python.
"""
import hashlib
import json
import os
import re
import sys
import traceback
import renderdoc as rd

DEPTH = '3097f03d3ce3ebd7ea1b700f450daf6f1414c03d0b188e278604ffa172360112'


def main():
    directory = os.environ['AOT_RENDERDOC_PROBE']
    name = os.environ['AOT_PROJECTION_PAIRS_NAME']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name): raise ValueError('Require simple output name')
    with open(os.path.join(directory, 'probe.json')) as source: done = json.load(source)
    if done.get('timed_out') or done.get('exit_code_before_cleanup') != 0: raise ValueError('Require closed native probe')
    with open(os.path.join(directory, 'renderdoc-capture.json')) as source: path = json.load(source)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(os.path.abspath(directory)):
        raise ValueError('Capture must belong to probe')
    out = os.path.join(directory, name)
    os.mkdir(out)
    report = {'capture': path, 'scope': __doc__.strip(), 'draws': [], 'shaders': {}}
    cap, controller = rd.OpenCaptureFile(), None
    try:
        status = cap.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        status, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        actions = []
        def visit(nodes):
            for a in nodes:
                if a.eventId <= 14000 and a.flags & rd.ActionFlags.Drawcall and str(a.depthOut) == 'ResourceId::10694':
                    actions.append(a)
                visit(a.children)
        visit(controller.GetRootActions())
        if not 4 <= len(actions) <= 2000: raise ValueError('Unexpected main-scene candidate count')
        for a in actions:
            controller.SetFrameEvent(a.eventId, False)
            pipe = controller.GetPipelineState()
            shader = str(pipe.GetShader(rd.ShaderStage.Vertex))
            if shader not in report['shaders']:
                reflection = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
                raw = bytes(reflection.rawBytes)
                digest = hashlib.sha256(raw).hexdigest()
                report['shaders'][shader] = digest
                with open(os.path.join(out, digest+'.dxbc'), 'wb') as target: target.write(raw)
            ib = pipe.GetIBuffer()
            report['draws'].append({'event': a.eventId, 'indices': a.numIndices,
                'index_offset': a.indexOffset, 'base_vertex': a.baseVertex, 'instances': a.numInstances,
                'ib_resource': str(ib.resourceId), 'ib_offset': ib.byteOffset, 'ib_stride': ib.byteStride,
                'vs_sha256': report['shaders'][shader], 'outputs': [str(r) for r in a.outputs]})
        def key(row):
            return tuple(row[k] for k in ('indices','index_offset','base_vertex','instances','ib_resource','ib_offset','ib_stride'))
        depth_rows = [r for r in report['draws'] if r['vs_sha256'] == DEPTH]
        if not depth_rows: raise ValueError('No targeted depth draws')
        keys = {key(r) for r in depth_rows}
        candidates = [r for r in report['draws'] if key(r) in keys]
        if len(candidates) > 400: raise ValueError('Unexpected matching geometry count')
        for row in candidates:
            controller.SetFrameEvent(row['event'], False)
            pipe = controller.GetPipelineState()
            reflection = pipe.GetShaderReflection(rd.ShaderStage.Vertex)
            row['constants'] = []
            for access in controller.GetDescriptorAccess():
                if access.stage != rd.ShaderStage.Vertex or access.type != rd.DescriptorType.ConstantBuffer: continue
                block = reflection.constantBlocks[access.index]
                if not 0 < block.byteSize <= 16384: raise ValueError('Unbounded constant buffer')
                region = rd.DescriptorRange()
                region.offset, region.descriptorSize, region.count, region.type = access.byteOffset, access.byteSize, 1, access.type
                for descriptor in controller.GetDescriptors(access.descriptorStore, [region]):
                    raw = bytes(controller.GetBufferData(descriptor.resource, descriptor.byteOffset, block.byteSize))
                    if len(raw) != block.byteSize: raise ValueError('Short constant readback')
                    filename = '%d-cb%d.bin' % (row['event'], access.index)
                    with open(os.path.join(out, filename), 'wb') as target: target.write(raw)
                    row['constants'].append({'name': block.name, 'file': filename,
                        'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)})
        report['depth_candidates'] = [{'depth_event': d['event'],
            'matching_index_draws': [r['event'] for r in candidates if r is not d and key(r) == key(d)]}
            for d in depth_rows]
        report['candidate_count'] = len(candidates)
        status = controller.GetFatalErrorStatus()
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if controller: controller.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out, 'pairs.json'), 'w') as target: json.dump(report, target, indent=2)


try: main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'projection-pairs-launch-error.txt'), 'w') as target:
        target.write(traceback.format_exc())
sys.exit(0)
