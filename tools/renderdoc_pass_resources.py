"""Trace resource flow through selected draws/dispatches of a closed GPU capture.

RenderDoc Python: AOT_RENDERDOC_PROBE and AOT_PASS_RESOURCE_QUERY (JSON with
name, 1-64 draw/dispatch events, and optional image_events subset). No shader replacement or
simulation. Images are mip/slice/sample zero illustrations; descriptors retain
the actual views. Raw texture snapshots and usage histories support follow-up.
"""
import hashlib
import json
import os
import re
import sys
import traceback
import renderdoc as rd


def main():
    root = os.path.abspath(os.environ['AOT_RENDERDOC_PROBE'])
    with open(os.path.join(root, 'probe.json')) as f: native = json.load(f)
    if native.get('timed_out') or native.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native capture')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f: path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root): raise ValueError('Foreign capture')
    with open(os.environ['AOT_PASS_RESOURCE_QUERY']) as f: query = json.load(f)
    events = query['events']
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', query['name']): raise ValueError('Invalid name')
    if not 1 <= len(events) <= 64 or len(set(events)) != len(events) or any(type(e) is not int or e < 1 for e in events):
        raise ValueError('Require 1-64 unique positive draw/dispatch events')
    image_events = query.get('image_events', [])
    if len(image_events) > 8 or any(e not in events for e in image_events): raise ValueError('Invalid image events')
    out = os.path.join(root, query['name']); os.mkdir(out)
    report = {'capture': path, 'query': query, 'actions': [], 'events': [], 'textures': {}, 'snapshots': {}, 'complete': False}
    cap, ctl = rd.OpenCaptureFile(), None
    try:
        if cap.OpenFile(path, '', None) != rd.ResultCode.Succeeded: raise RuntimeError('Capture open failed')
        status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded: raise RuntimeError(str(status))
        actions = {}; structured = ctl.GetStructuredFile()
        def visit(nodes):
            for a in nodes:
                if len(report['actions']) >= 20000: raise ValueError('Action inventory limit exceeded')
                report['actions'].append({'event': a.eventId, 'name': a.GetName(structured),
                    'flags': str(a.flags), 'copy_source': str(a.copySource), 'copy_destination': str(a.copyDestination)})
                if a.eventId in events and a.flags & (rd.ActionFlags.Drawcall | rd.ActionFlags.Dispatch): actions[a.eventId] = a
                visit(a.children)
        visit(ctl.GetRootActions())
        if len(actions) != len(events): raise ValueError('Missing requested draw/dispatch')
        textures = {str(t.resourceId): t for t in ctl.GetTextures()}
        names = {str(r.resourceId): r.name for r in ctl.GetResources()}

        def snapshot(resource):
            key = str(resource)
            if key not in textures: return {'skipped': 'Not a texture', 'resource': key}
            t = textures[key]
            if key not in report['textures']:
                report['textures'][key] = {'name': names.get(key, ''), 'width': t.width, 'height': t.height,
                    'depth': t.depth, 'mips': t.mips, 'samples': t.msSamp, 'format': t.format.Name(),
                    'uses': [{'event': u.eventId, 'usage': str(u.usage)} for u in ctl.GetUsage(resource)]}
            if t.width * t.height * max(1, t.depth) * 16 > 64 * 1024 * 1024:
                return {'resource': key, 'skipped': 'Conservative 64 MiB subresource bound'}
            raw = bytes(ctl.GetTextureData(resource, rd.Subresource()))
            sha = hashlib.sha256(raw).hexdigest()
            snapshot_key = key.replace('ResourceId::', '') + '-' + sha
            if snapshot_key not in report['snapshots']:
                if len(report['snapshots']) >= 256: raise ValueError('Snapshot limit exceeded')
                if len(raw) > 64 * 1024 * 1024 or sum(s['bytes'] for s in report['snapshots'].values()) + len(raw) > 1024 ** 3:
                    raise ValueError('Raw snapshot byte budget exceeded')
                with open(os.path.join(out, snapshot_key + '.bin'), 'wb') as f: f.write(raw)
                save = rd.TextureSave(); save.resourceId = resource; save.destType = rd.FileType.PNG
                status = ctl.SaveTexture(save, os.path.join(out, snapshot_key + '.png'))
                report['snapshots'][snapshot_key] = {'resource': key, 'sha256': sha, 'bytes': len(raw),
                    'image_result': str(status), 'illustration': 'Mip/slice/sample zero; see descriptor for actual view'}
                if status != rd.ResultCode.Succeeded: raise RuntimeError('Texture illustration failed')
            return {'resource': key, 'snapshot': snapshot_key}

        for event in sorted(events):
            ctl.SetFrameEvent(event, True); pipe = ctl.GetPipelineState(); a = actions[event]
            state = ctl.GetD3D12PipelineState(); compute = bool(a.flags & rd.ActionFlags.Dispatch)
            pipeline = pipe.GetComputePipelineObject() if compute else pipe.GetGraphicsPipelineObject()
            row = {'event': event, 'compute': compute, 'indices': a.numIndices, 'pipeline': names.get(str(pipeline), ''),
                'viewport': rd.DumpObject(pipe.GetViewport(0)), 'rasterizer': rd.DumpObject(state.rasterizer),
                'depth': rd.DumpObject(state.outputMerger.depthStencilState),
                'blend': rd.DumpObject(state.outputMerger.blendState), 'shaders': {}, 'bindings': []}
            report['events'].append(row)
            for stage in ((rd.ShaderStage.Compute,) if compute else (rd.ShaderStage.Vertex, rd.ShaderStage.Pixel)):
                reflection = pipe.GetShaderReflection(stage)
                if reflection is None: continue
                raw = bytes(reflection.rawBytes); sha = hashlib.sha256(raw).hexdigest()
                row['shaders'][stage.name] = sha
                if not os.path.exists(os.path.join(out, sha + '.dxbc')):
                    with open(os.path.join(out, sha + '.dxbc'), 'wb') as f: f.write(raw)
                    with open(os.path.join(out, sha + '.txt'), 'w') as f:
                        f.write(ctl.DisassembleShader(pipeline, reflection, ''))
            inputs = []
            for access in ctl.GetDescriptorAccess():
                if access.type not in (rd.DescriptorType.Image, rd.DescriptorType.ReadWriteImage,
                        rd.DescriptorType.Buffer, rd.DescriptorType.ReadWriteBuffer, rd.DescriptorType.ConstantBuffer): continue
                region = rd.DescriptorRange()
                region.offset, region.descriptorSize, region.count, region.type = access.byteOffset, access.byteSize, 1, access.type
                for desc in ctl.GetDescriptors(access.descriptorStore, [region]):
                    binding = {'access': rd.DumpObject(access), 'descriptor': rd.DumpObject(desc)}
                    row['bindings'].append(binding)
                    if access.type == rd.DescriptorType.Image: inputs.append((binding, desc.resource))
                    if access.type == rd.DescriptorType.ConstantBuffer:
                        if desc.flags & rd.DescriptorFlags.InlineData:
                            variables = ctl.GetCBufferVariableContents(pipeline, pipe.GetShader(access.stage),
                                access.stage, pipe.GetShaderEntryPoint(access.stage), access.index,
                                desc.resource, desc.byteOffset, desc.byteSize)
                            binding['inline_constants'] = [rd.DumpObject(v) for v in variables]
                            continue
                        block = pipe.GetShaderReflection(access.stage).constantBlocks[access.index]
                        if not 0 < block.byteSize <= 65536: raise ValueError('Unexpected constant size')
                        raw = bytes(ctl.GetBufferData(desc.resource, desc.byteOffset, block.byteSize))
                        if len(raw) != block.byteSize: raise ValueError('Short constant buffer')
                        filename = '%d-%s-cb%d.bin' % (event, access.stage.name, access.index)
                        with open(os.path.join(out, filename), 'wb') as f: f.write(raw)
                        binding['constant_file'] = filename
            ctl.SetFrameEvent(event - 1, True)
            for binding, resource in inputs: binding['before_draw'] = snapshot(resource)
            if event in image_events:
                row['outputs_before'] = [snapshot(r) for r in a.outputs if r != rd.ResourceId.Null()]
                ctl.SetFrameEvent(event, True)
                row['outputs_after'] = [snapshot(r) for r in a.outputs if r != rd.ResourceId.Null()]
        if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded: raise RuntimeError('GPU replay failed')
        report['complete'] = True
    except BaseException: report['error'] = traceback.format_exc()
    finally:
        if ctl is not None: ctl.Shutdown()
        cap.Shutdown()
        with open(os.path.join(out, 'resources.json'), 'w') as f: json.dump(report, f, indent=2)
    return int(not report['complete'])


sys.exit(main())
