"""Run with qrenderdoc --python; set AOT_RENDERDOC_ROOT and AOT_RENDERDOC_PROBE.

Requires checkpoint verification in runtime.log before requesting GPU frames.
Optional AOT_RENDERDOC_FRAMES selects one to four consecutive frames (default one).
Uses RenderDoc's embedded Python, not the workspace Python interpreter.
"""
import json
import os
import sys
import time
import traceback

import renderdoc as rd


def main():
    directory = os.environ['AOT_RENDERDOC_PROBE']
    frame_count = int(os.environ.get('AOT_RENDERDOC_FRAMES', '1'))
    passive = os.environ.get('AOT_RENDERDOC_PASSIVE') == '1'
    if not 1 <= frame_count <= 4:
        raise RuntimeError('Capture between one and four consecutive frames')
    def read(name):
        with open(os.path.join(directory, name), encoding='utf-8') as source:
            return json.load(source)
    running, injected = read('running.json'), read('renderdoc.json')
    expected = os.path.normcase(os.path.abspath(os.path.join(
        os.environ['AOT_RENDERDOC_ROOT'], 'out/build/RelWithDebInfo/army_of_two.exe')))
    if os.path.normcase(os.path.abspath(running['command'][0])) != expected:
        raise RuntimeError('Probe executable does not match this workspace')
    report = {'pid': running['pid'], 'ident': injected['ident'], 'events': [], 'captures': []}
    connection = None
    try:
        connection = rd.CreateTargetControl('', injected['ident'], 'AO2 owned checkpoint probe', False)
        if connection is None or connection.GetPID() != running['pid']:
            raise RuntimeError('RenderDoc target PID does not match the owned probe')
        report['api'] = connection.GetAPI()
        deadline = time.monotonic() + 180
        triggered = False
        while time.monotonic() < deadline and connection.Connected():
            message = connection.ReceiveMessage(None)
            if message.type != rd.TargetControlMessageType.Noop:
                report['events'].append(str(message.type))
            if message.type == rd.TargetControlMessageType.NewCapture:
                report['api'] = connection.GetAPI()
                capture = message.newCapture
                report['captures'].append({'path': capture.path, 'frame': capture.frameNumber,
                                           'capture_id': capture.captureId})
                if len(report['captures']) >= frame_count:
                    break
            if not triggered and not passive:
                with open(os.path.join(directory, 'runtime.log'), encoding='utf-8', errors='replace') as log:
                    ready = "CurrCheckpoint = AO2Checkpoint'01_map_back_alley_shell.TheWorld.PersistentLevel.AO2Checkpoint_2'" in log.read()
                if ready:
                    connection.TriggerCapture(frame_count)
                    report['trigger_unix_seconds'] = time.time()
                    triggered = True
            time.sleep(.02)
        if len(report['captures']) != frame_count:
            raise RuntimeError('Not all requested GPU captures arrived before the deadline or disconnect')
    except BaseException:
        report['error'] = traceback.format_exc()
    finally:
        if connection is not None:
            connection.Shutdown()
        with open(os.path.join(directory, 'renderdoc-capture.json'), 'w', encoding='utf-8') as out:
            json.dump(report, out, indent=2)


try:
    main()
except BaseException:
    with open(os.path.join(os.environ['AOT_RENDERDOC_PROBE'], 'renderdoc-script-error.txt'), 'w') as out:
        out.write(traceback.format_exc())
sys.exit(0)
