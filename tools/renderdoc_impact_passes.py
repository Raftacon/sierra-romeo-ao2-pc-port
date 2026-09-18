"""Read matched impact crops at explicit later passes in a closed GPU capture."""
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
    with open(os.environ['AOT_IMPACT_COLOR_QUERY']) as f:
        query = json.load(f)
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', query[k]) for k in ('name', 'baseline')):
        raise ValueError('Require simple local output names')
    with open(os.path.join(root, 'renderdoc-capture.json')) as f:
        path = json.load(f)['captures'][0]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(root):
        raise ValueError('Capture must belong to probe')
    with open(os.path.join(root, query['baseline'], 'color.json')) as f:
        baseline = json.load(f)
    if (not baseline.get('complete') or baseline.get('error') or baseline['capture'] != path or
            baseline['query']['crop'] != query['crop'] or
            not 1 <= len(query['frames']) <= 24):
        raise ValueError('Require complete matching baseline and bounded frame count')
    hashes = {row['event']: row['sha256'] for row in baseline['frames']}
    x, y, w, h = query['crop']
    if any(type(v) is not int for v in query['crop']) or min(x, y) < 0 or min(w, h) < 1 or w*h > 512*512:
        raise ValueError('Require bounded crop')
    output = os.path.join(root, query['name'])
    os.mkdir(output)
    report = {'capture': path, 'query': query, 'complete': False, 'frames': [], 'shaders': {}}
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
            for action in items:
                actions[action.eventId] = action
                visit(action.children)
        visit(controller.GetRootActions())
        sub = rd.Subresource()
        for frame in query['frames']:
            if not 1 <= len(frame['stages']) <= 8 or frame['stages'][0]['stage'] != 'impacts':
                raise ValueError('Require bounded stages starting with verified impacts')
            result = {'frame': frame['frame'], 'stages': []}
            report['frames'].append(result)
            last = 0
            for stage in frame['stages']:
                event = stage['event']
                if (event not in actions or event <= last or
                        not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', stage['stage'])):
                    raise ValueError('Require ordered known events and simple stage names')
                last = event
                tex = textures[stage['resource']]
                fmt = tex.format.Name()
                size = {'R16G16B16A16_FLOAT': 8, 'R8G8B8A8_UNORM': 4}.get(fmt)
                if not size or tex.msSamp != 1 or x+w > tex.width or y+h > tex.height:
                    raise ValueError('Unexpected target format, sampling, or crop')
                controller.SetFrameEvent(event, True)
                pipe = controller.GetPipelineState()
                if pipe.GetOutputTargets()[0].resource != tex.resourceId:
                    raise ValueError('Requested event targets another texture')
                identities = {}
                for shader_stage, key in ((rd.ShaderStage.Vertex, 'vertex_sha256'), (rd.ShaderStage.Pixel, 'pixel_sha256')):
                    rid = str(pipe.GetShader(shader_stage))
                    if rid not in report['shaders']:
                        code = bytes(pipe.GetShaderReflection(shader_stage).rawBytes)
                        report['shaders'][rid] = hashlib.sha256(code).hexdigest()
                    identities[shader_stage.name] = report['shaders'][rid]
                    if stage['stage'] == 'impacts' and report['shaders'][rid] != query[key]:
                        raise ValueError('Unexpected impact shader')
                data = bytes(controller.GetTextureData(tex.resourceId, sub))
                if len(data) != tex.width * tex.height * size:
                    raise ValueError('Unexpected readback layout')
                crop = b''.join(data[((y+row)*tex.width+x)*size:((y+row)*tex.width+x+w)*size] for row in range(h))
                digest = hashlib.sha256(crop).hexdigest()
                if stage['stage'] == 'impacts' and digest != hashes.get(event):
                    raise ValueError('Original impact crop did not reproduce')
                name = '%02d-%s.raw' % (frame['frame'], stage['stage'])
                with open(os.path.join(output, name), 'xb') as f:
                    f.write(crop)
                result['stages'].append(dict(stage, file=name, format=fmt, sha256=digest, shaders=identities))
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
        with open(os.path.join(output, 'passes.json'), 'w') as f:
            json.dump(report, f, indent=2)
    return int('error' in report)


sys.exit(main())
