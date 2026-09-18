"""Read-only offline frame inventory with RenderDoc's embedded Python.

Set AOT_RENDERDOC_PROBE to a completed probe with renderdoc-capture.json.
Run qrenderdoc --python tools/renderdoc_inspect.py after the game exits.
"""
import json
import os
import sys
import traceback

import renderdoc as rd


def main():
    directory = os.environ['AOT_RENDERDOC_PROBE']
    with open(os.path.join(directory, 'probe.json')) as source:
        completed = json.load(source)
    if completed.get('timed_out') or completed.get('exit_code_before_cleanup') != 0:
        raise RuntimeError('Require a completed, normally closed native probe before GPU replay')
    with open(os.path.join(directory, 'renderdoc-capture.json')) as source:
        path = json.load(source)['captures'][int(os.environ.get('AOT_RENDERDOC_CAPTURE_INDEX', '0'))]['path']
    if os.path.normcase(os.path.dirname(path)) != os.path.normcase(os.path.abspath(directory)):
        raise RuntimeError('Capture must belong to the probe directory')
    if os.path.exists(path + '-inventory.json'):
        raise RuntimeError('Require a new inventory output')
    report = {'capture': path, 'actions': [], 'textures': [], 'saved': [], 'complete': False}
    capture, controller = rd.OpenCaptureFile(), None
    try:
        status = capture.OpenFile(path, '', None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        status, controller = capture.OpenCapture(rd.ReplayOptions(), None)
        if status != rd.ResultCode.Succeeded:
            raise RuntimeError(str(status))
        structured = controller.GetStructuredFile()
        def visit(actions, parent=0):
            for action in actions:
                report['actions'].append({'event': action.eventId, 'parent': parent,
                    'name': action.GetName(structured), 'flags': str(action.flags),
                    'outputs': [str(r) for r in action.outputs],
                    'depth': str(action.depthOut), 'indices': action.numIndices})
                visit(action.children, action.eventId)
        visit(controller.GetRootActions())
        last = max(a['event'] for a in report['actions'])
        controller.SetFrameEvent(last, True)
        for texture in controller.GetTextures():
            report['textures'].append({'id': str(texture.resourceId), 'width': texture.width,
                'height': texture.height, 'format': texture.format.Name(),
                'samples': texture.msSamp, 'array_size': texture.arraysize,
                'flags': str(texture.creationFlags)})
            if texture.creationFlags & rd.TextureCategory.SwapBuffer:
                save = rd.TextureSave()
                save.resourceId = texture.resourceId
                save.destType = rd.FileType.PNG
                filename = os.path.basename(path) + '-swap-' + str(len(report['saved'])) + '.png'
                status = controller.SaveTexture(save, os.path.join(directory, filename))
                report['saved'].append({'file': filename, 'id': str(texture.resourceId), 'status': str(status)})
        report['debug_messages'] = [str(m.description) for m in controller.GetDebugMessages()]
        if controller.GetFatalErrorStatus() != rd.ResultCode.Succeeded:
            raise RuntimeError('Fatal GPU replay error')
        report['complete'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if controller is not None:
            controller.Shutdown()
        capture.Shutdown()
        with open(path + '-inventory.json', 'w', encoding='utf-8') as out:
            json.dump(report, out, indent=2)


try:
    main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'renderdoc-replay-error.txt'), 'w') as out:
        out.write(traceback.format_exc())
sys.exit(0)
