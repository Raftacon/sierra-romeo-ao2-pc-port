"""Attribute existing GPU timing samples without retiming or changing shaders.

RenderDoc Python: AOT_GPU_COST_INPUT points to a completed gpu-cost.json.
Output is a new sibling gpu-cost-groups-001 directory, or AOT_GPU_COST_OUTPUT.
Optional AOT_GPU_GROUP_METADATA reuses validated shader metadata from a prior
grouping of the identical capture/timings, avoiding thousands of replay seeks.
Sums are replay counter costs, not native frame time or shader-only time.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import traceback
import renderdoc as rd


source = Path(os.environ['AOT_GPU_COST_INPUT']).resolve()
out = Path(os.environ.get('AOT_GPU_COST_OUTPUT', str(source.parent.parent / 'gpu-cost-groups-001'))).resolve()
out.mkdir(parents=True, exist_ok=False)
report = {'complete': False, 'limits': __doc__, 'source': str(source), 'events': [], 'shaders': {}}
cap, ctl = rd.OpenCaptureFile(), None
try:
    raw = source.read_bytes()
    prior = json.loads(raw)
    if not prior['complete'] or prior.get('error'):
        raise ValueError('Require completed cost measurement')
    report['source_sha256'] = hashlib.sha256(raw).hexdigest()
    path = Path(prior['capture']).resolve()
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != prior['capture_sha256']:
        raise ValueError('Capture changed since counter measurement')
    report['capture_sha256'] = digest.hexdigest()
    cached = {}
    if os.environ.get('AOT_GPU_GROUP_METADATA'):
        cache_path = Path(os.environ['AOT_GPU_GROUP_METADATA']).resolve()
        cache_bytes = cache_path.read_bytes()
        metadata = json.loads(cache_bytes)
        if (not metadata['complete'] or metadata.get('error') or
                metadata['capture_sha256'] != report['capture_sha256'] or
                metadata['source_sha256'] != report['source_sha256']):
            raise ValueError('Metadata is not a completed grouping of these exact timings/capture')
        cached = {e['event']: e for e in metadata['events'] if 'shaders' in e}
        report['metadata_source'] = {'path': str(cache_path), 'sha256': hashlib.sha256(cache_bytes).hexdigest()}
        report['shaders'] = metadata['shaders']
        for sha in report['shaders']:
            code = (cache_path.parent / (sha + '.dxbc')).read_bytes()
            if hashlib.sha256(code).hexdigest() != sha:
                raise ValueError('Cached shader hash mismatch')
            (out / (sha + '.dxbc')).write_bytes(code)
    if cap.OpenFile(str(path), '', None) != rd.ResultCode.Succeeded:
        raise RuntimeError('Capture open failed')
    status, ctl = cap.OpenCapture(rd.ReplayOptions(), None)
    if status != rd.ResultCode.Succeeded:
        raise RuntimeError(str(status))
    actions = {}
    def visit(nodes):
        for a in nodes:
            actions[a.eventId] = a
            visit(a.children)
    visit(ctl.GetRootActions())
    structured = ctl.GetStructuredFile()
    textures = {str(t.resourceId): t for t in ctl.GetTextures()}
    resources = {str(r.resourceId): r.name for r in ctl.GetResources()}
    shader_ids = {}
    groups = {}

    def parameters(obj, depth=0):
        if depth > 8 or obj.NumChildren() > 256:
            raise ValueError('Unexpectedly large copy parameters')
        result = rd.DumpObject(obj)
        result['children'] = [parameters(obj.GetChild(i), depth + 1) for i in range(obj.NumChildren())]
        return result

    def group(kind, key, row):
        entries = groups.setdefault(kind, {})
        entry = entries.setdefault(key, {'key': key, 'count': 0, 'sum_median_ms': 0.,
                                        'sum_pass_ms': [0., 0., 0.], 'events': []})
        entry['count'] += 1
        entry['sum_median_ms'] += row['median_ms']
        entry['sum_pass_ms'] = [a + b for a, b in zip(entry['sum_pass_ms'], row['samples_ms'])]
        entry['events'].append(row['event'])

    for timing in sorted(prior['ranked_events'], key=lambda r: r['event']):
        action = actions[timing['event']]
        row = dict(timing, name=action.GetName(structured), flags=str(action.flags))
        report['events'].append(row)
        group('operation', row['name'].split('(')[0], row)
        if action.flags & rd.ActionFlags.Copy:
            row['copy_source'] = str(action.copySource)
            row['copy_destination'] = str(action.copyDestination)
            row['source_name'] = resources.get(row['copy_source'], '')
            row['destination_name'] = resources.get(row['copy_destination'], '')
            group('copy_resources', row['copy_source'] + ' -> ' + row['copy_destination'], row)
            # Bound render targets on a copy action are not the copied resource.
            row['copy_parameters'] = [parameters(structured.chunks[e.chunkIndex])
                                      for e in action.events
                                      if 'Copy' in structured.chunks[e.chunkIndex].name]
        if not action.flags & (rd.ActionFlags.Drawcall | rd.ActionFlags.Dispatch):
            continue
        if cached:
            saved = cached[action.eventId]
            if saved['samples_ms'] != row['samples_ms'] or saved['name'] != row['name']:
                raise ValueError('Cached event identity changed')
            for key in ('pipeline_name', 'shaders', 'indices', 'instances', 'outputs'):
                row[key] = saved[key]
            group('shader_set', json.dumps(row['shaders'], sort_keys=True), row)
            group('pipeline_name', row['pipeline_name'], row)
            for stage, sha in row['shaders'].items():
                group(stage, sha, row)
            if 'Vertex' in row['shaders']:
                group('vertex_feature_flags', str(report['shaders'][row['shaders']['Vertex']]['feature_flags']), row)
            continue
        ctl.SetFrameEvent(action.eventId, False)
        pipe = ctl.GetPipelineState()
        compute = bool(action.flags & rd.ActionFlags.Dispatch)
        pipeline = pipe.GetComputePipelineObject() if compute else pipe.GetGraphicsPipelineObject()
        row['pipeline_name'] = resources.get(str(pipeline), '')
        row['shaders'] = {}
        for stage in ((rd.ShaderStage.Compute,) if compute else (rd.ShaderStage.Vertex, rd.ShaderStage.Pixel)):
            sid = str(pipe.GetShader(stage))
            reflection = pipe.GetShaderReflection(stage)
            if reflection is None:
                continue
            if sid not in shader_ids:
                code = bytes(reflection.rawBytes)
                sha = hashlib.sha256(code).hexdigest()
                (out / (sha + '.dxbc')).write_bytes(code)
                features = None
                if code[:4] == b'DXBC':
                    count = struct.unpack_from('<I', code, 28)[0]
                    for offset in struct.unpack_from('<' + 'I' * count, code, 32):
                        if code[offset:offset + 4] == b'SFI0':
                            features = struct.unpack_from('<Q', code, offset + 8)[0]
                shader_ids[sid] = sha
                report['shaders'][sha] = {'resource': sid, 'bytes': len(code), 'feature_flags': features}
            row['shaders'][stage.name] = shader_ids[sid]
        row['indices'] = action.numIndices
        row['instances'] = action.numInstances
        row['outputs'] = []
        if not compute:
            for target in list(action.outputs) + [action.depthOut]:
                t = textures.get(str(target))
                if t is not None:
                    row['outputs'].append({'id': str(target), 'width': t.width, 'height': t.height,
                                           'format': t.format.Name()})
        group('shader_set', json.dumps(row['shaders'], sort_keys=True), row)
        group('pipeline_name', row['pipeline_name'], row)
        for stage, sha in row['shaders'].items():
            group(stage, sha, row)
        if 'Vertex' in row['shaders']:
            flags = report['shaders'][row['shaders']['Vertex']]['feature_flags']
            group('vertex_feature_flags', str(flags), row)
    if len(report['events']) != prior['event_count']:
        raise ValueError('Event coverage changed')
    report['groups'] = {kind: sorted(entries.values(), key=lambda r: r['sum_median_ms'], reverse=True)
                        for kind, entries in groups.items()}
    if ctl.GetFatalErrorStatus() != rd.ResultCode.Succeeded:
        raise RuntimeError('Fatal GPU replay error')
    report['complete'] = True
except BaseException:
    report['error'] = traceback.format_exc()
finally:
    if ctl is not None:
        ctl.Shutdown()
    cap.Shutdown()
    (out / 'gpu-cost-groups.json').write_text(json.dumps(report, indent=2) + '\n')
sys.exit(0 if report['complete'] else 1)
