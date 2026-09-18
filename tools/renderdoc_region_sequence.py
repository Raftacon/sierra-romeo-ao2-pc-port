"""Read bounded color regions at explicit draw events in a closed GPU capture.

Run in RenderDoc Python with AOT_RENDERDOC_PROBE and AOT_REGION_QUERY. This
records actual GPU outputs and shader identities; it does not infer frame
correspondence, diagnose flicker or modify shaders.
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
    with open(os.path.join(root, 'probe.json')) as f:
        native = json.load(f)
    if native.get('timed_out') or native.get('exit_code_before_cleanup') != 0:
        raise ValueError('Require normally closed native probe')
    with open(os.environ['AOT_REGION_QUERY']) as f:
        query = json.load(f)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', query['name']):
        raise ValueError('Require simple new output name')
    if not 1 <= len(query['samples']) <= 120:
        raise ValueError('Require 1-120 samples')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f:
        path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root):
        raise ValueError('Capture must belong to probe')
    output = os.path.join(root, query['name'])
    os.mkdir(output)
    report = {'capture': path, 'query': query, 'complete': False, 'samples': [], 'shaders': {}}
    cap, controller = rd.OpenCaptureFile(), None
    try:
        status = cap.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        status, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        textures = {str(t.resourceId): t for t in controller.GetTextures()}
        actions = {}
        def visit(items):
            for a in items:
                actions[a.eventId] = a
                visit(a.children)
        visit(controller.GetRootActions())
        for index, sample in enumerate(query['samples']):
            event = sample['event']
            if type(event) is not int or event not in actions or not actions[event].flags & rd.ActionFlags.Drawcall:
                raise ValueError('Require known draw event')
            tex = textures[sample['resource']]
            fmt = tex.format.Name()
            size = {'R16G16B16A16_FLOAT': 8, 'R8G8B8A8_UNORM': 4}.get(fmt)
            x, y, w, h = sample['crop']
            if (not size or tex.msSamp != 1 or tex.depth != 1 or tex.arraysize != 1 or
                    any(type(v) is not int for v in sample['crop']) or
                    min(x, y) < 0 or min(w, h) < 1 or w*h > 512*512 or
                    x+w > tex.width or y+h > tex.height):
                raise ValueError('Require bounded single-sample 2D color crop')
            controller.SetFrameEvent(event, True)
            pipe = controller.GetPipelineState()
            if pipe.GetOutputTargets()[0].resource != tex.resourceId:
                raise ValueError('Requested draw targets another texture')
            identities = {}
            for stage in (rd.ShaderStage.Vertex, rd.ShaderStage.Pixel):
                rid = str(pipe.GetShader(stage))
                if rid not in report['shaders']:
                    raw = bytes(pipe.GetShaderReflection(stage).rawBytes)
                    digest = hashlib.sha256(raw).hexdigest()
                    with open(os.path.join(output, digest + '.dxbc'), 'wb') as f:
                        f.write(raw)
                    report['shaders'][rid] = digest
                identities[stage.name] = report['shaders'][rid]
            data = bytes(controller.GetTextureData(tex.resourceId, rd.Subresource()))
            if len(data) != tex.width*tex.height*size:
                raise ValueError('Unexpected readback layout')
            crop = b''.join(data[((y+r)*tex.width+x)*size:((y+r)*tex.width+x+w)*size] for r in range(h))
            name = '%03d.raw' % index
            with open(os.path.join(output, name), 'xb') as f:
                f.write(crop)
            report['samples'].append(dict(sample, file=name, format=fmt,
                sha256=hashlib.sha256(crop).hexdigest(), shaders=identities))
        status = controller.GetFatalErrorStatus()
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        report['complete'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if controller is not None:
            controller.Shutdown()
        cap.Shutdown()
        with open(os.path.join(output, 'regions.json'), 'w') as f:
            json.dump(report, f, indent=2)
    return int('error' in report)


sys.exit(main())
