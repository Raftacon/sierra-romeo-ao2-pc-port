"""Read actual equipment HDR output across a bounded diagnostic GPU sequence.

Run through RenderDoc Python with AOT_RENDERDOC_PROBE set to a closed native
probe. Identifies the known equipment draw by index count AND original shader
hash. This is a temporal material observation, not a visual-parity verdict.
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

EXPECTED = 'adc3a7e10f7260fc3dd98bfe97428dee5d339c7a7df25bfdf53a1374fa4c9b1e'


def main():
    directory = os.environ['AOT_RENDERDOC_PROBE']
    name = os.environ.get('AOT_EQUIPMENT_SEQUENCE_NAME', 'equipment-sequence')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name):
        raise ValueError('Require a simple output directory name')
    output = os.path.join(directory, name)
    with open(os.path.join(directory, 'probe.json')) as source:
        completed = json.load(source)
    if completed.get('timed_out') or completed.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require a normally closed native probe')
    with open(os.path.join(directory, 'renderdoc-capture.json')) as source:
        path = json.load(source)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(os.path.abspath(directory)):
        raise ValueError('Capture must belong to the probe')
    os.mkdir(output)
    report = {'capture': path, 'roi': [260, 360, 100, 120], 'sample': 0,
              'shader_sha256': EXPECTED, 'draws': [], 'rejected': []}
    projection_path = os.environ.get('AOT_PROJECTION_VARIANTS')
    projection = None
    if projection_path:
        with open(os.path.join(projection_path,'replay.json')) as source:
            verified = json.load(source)
        if verified.get('error') or not verified.get('restoration_exact') or verified['capture'] != path:
            raise ValueError('Require the validated paired projection experiment on this capture')
        with open(os.path.join(projection_path,'variants.json')) as source:
            projection = json.load(source)
        if [v['layout']['name'] for v in projection['variants']] != ['color','depth']:
            raise ValueError('Require paired color/depth variants')
        report['projection_variants'] = projection
    replacements = []
    baseline_first = None
    capture, controller = rd.OpenCaptureFile(), None
    try:
        status = capture.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        status, controller = capture.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        textures = {str(t.resourceId): t for t in controller.GetTextures()}
        actions = []
        def visit(nodes):
            for action in nodes:
                target = textures.get(str(action.outputs[0]))
                if (action.flags & rd.ActionFlags.Drawcall and action.numIndices == 8352
                        and target is not None and target.format.Name() == 'R16G16B16A16_FLOAT'
                        and target.msSamp == 2):
                    actions.append(action)
                visit(action.children)
        visit(controller.GetRootActions())
        if not 1 <= len(actions) <= 240:
            raise ValueError('Unexpected equipment candidate count: '+str(len(actions)))
        report['candidate_count'] = len(actions)
        if projection:
            first = actions[0]
            controller.SetFrameEvent(first.eventId,True)
            target = textures[str(first.outputs[0])]
            sub = rd.Subresource(); sub.sample = 0
            baseline_first = bytes(controller.GetTextureData(target.resourceId,sub))
            for variant,event in zip(projection['variants'],[7428,4902]):
                controller.SetFrameEvent(event,True)
                pipe = controller.GetPipelineState()
                original = pipe.GetShader(rd.ShaderStage.Vertex)
                raw = bytes(pipe.GetShaderReflection(rd.ShaderStage.Vertex).rawBytes)
                if hashlib.sha256(raw).hexdigest() != variant['original_sha256']:
                    raise ValueError('Unexpected original projection shader')
                with open(os.path.join(projection_path,variant['file']),'rb') as source:
                    raw = source.read()
                if hashlib.sha256(raw).hexdigest() != variant['sha256']:
                    raise ValueError('Projection variant hash mismatch')
                replacement,errors = controller.BuildTargetShader('main',rd.ShaderEncoding.DXBC,
                    raw,rd.ShaderCompileFlags(),rd.ShaderStage.Vertex)
                if replacement == rd.ResourceId.Null() or errors:
                    if replacement != rd.ResourceId.Null():
                        controller.FreeTargetResource(replacement)
                    raise ValueError('Projection variant build failed: '+errors)
                replacements.append((original,replacement))
                controller.ReplaceResource(original,replacement)
        for action in actions:
            controller.SetFrameEvent(action.eventId, True)
            pipe = controller.GetPipelineState()
            reflection = pipe.GetShaderReflection(rd.ShaderStage.Pixel)
            if reflection is None or hashlib.sha256(bytes(reflection.rawBytes)).hexdigest() != EXPECTED:
                report['rejected'].append(action.eventId)
                continue
            if projection:
                flags_checked = False
                for access in controller.GetDescriptorAccess():
                    if access.stage != rd.ShaderStage.Vertex or access.type != rd.DescriptorType.ConstantBuffer:
                        continue
                    block = pipe.GetShaderReflection(rd.ShaderStage.Vertex).constantBlocks[access.index]
                    if block.name != 'xe_system_cbuffer': continue
                    region = rd.DescriptorRange()
                    region.offset,region.descriptorSize,region.count = access.byteOffset,access.byteSize,1
                    region.type = access.type
                    descriptor = controller.GetDescriptors(access.descriptorStore,[region])[0]
                    raw = bytes(controller.GetBufferData(descriptor.resource,descriptor.byteOffset,4))
                    if len(raw)!=4 or struct.unpack('<I',raw)[0]&14!=8:
                        raise ValueError('Unsupported projection flags during sequence')
                    flags_checked = True
                if not flags_checked: raise ValueError('Missing projection flags')
            target = textures[str(action.outputs[0])]
            if target.format.Name() != 'R16G16B16A16_FLOAT' or target.msSamp != 2:
                raise ValueError('Unexpected identified material target')
            x, y, w, h = report['roi']
            if target.width < x+w or target.height < y+h:
                raise ValueError('ROI exceeds material target')
            sub = rd.Subresource()
            sub.sample = report['sample']
            data = bytes(controller.GetTextureData(target.resourceId, sub))
            if len(data) != target.width*target.height*8:
                raise ValueError('Unexpected sample-zero readback size')
            roi = b''.join(data[((y+j)*target.width+x)*8:((y+j)*target.width+x+w)*8]
                           for j in range(h))
            values = struct.unpack('<'+'e'*(len(roi)//2), roi)
            if not all(math.isfinite(v) for j,v in enumerate(values) if j%4 != 3):
                raise ValueError('Nonfinite material RGB')
            filename = 'event-%d-rgba16f.bin' % action.eventId
            with open(os.path.join(output, filename), 'wb') as target_file:
                target_file.write(roi)
            report['draws'].append({'event': action.eventId, 'resource': str(target.resourceId),
                'file': filename, 'sha256': hashlib.sha256(roi).hexdigest(),
                'width': target.width, 'height': target.height})
        if not report['draws']:
            raise ValueError('No hash-verified equipment draws')
        if replacements:
            for original,replacement in replacements:
                controller.RemoveReplacement(original)
                controller.FreeTargetResource(replacement)
            replacements.clear()
            controller.SetFrameEvent(actions[0].eventId,True)
            sub = rd.Subresource(); sub.sample = 0
            restored = bytes(controller.GetTextureData(actions[0].outputs[0],sub))
            if restored != baseline_first: raise ValueError('Sequence restoration control failed')
            report['restoration_exact'] = True
        status = controller.GetFatalErrorStatus()
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if controller is not None:
            for original,replacement in replacements:
                controller.RemoveReplacement(original)
                controller.FreeTargetResource(replacement)
            controller.Shutdown()
        capture.Shutdown()
        with open(os.path.join(output, 'sequence.json'), 'w') as target:
            json.dump(report, target, indent=2)


try:
    main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'sequence-launch-error.txt'), 'w') as target:
        target.write(traceback.format_exc())
sys.exit(0)
