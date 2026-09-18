"""Capture a short README GIF from an owned probe's game window only.

Capture timestamps determine playback duration. GIF sampling is not an FPS test.
Optional events control only that probe's scripted controller/command files.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
from pathlib import Path
import time
import threading

from PIL import Image
from probe import game_windows, capture
from pc_input_probe import k


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=12)
    parser.add_argument('--fps', type=float, default=8)
    parser.add_argument('--width', type=int, default=960)
    parser.add_argument('--events', type=Path)
    parser.add_argument('--state-file', default='pad.state')
    args = parser.parse_args()
    if not (0 < args.seconds <= 45 and 1 <= args.fps <= 12 and 320 <= args.width <= 1280):
        parser.error('Use 0..45 seconds, 1..12 FPS and 320..1280 pixel width')
    if args.output.exists():
        parser.error('Choose a new output filename')
    if Path(args.state_file).name != args.state_file:
        parser.error('State filename must be a basename within the probe')
    pid = json.loads((args.probe / 'running.json').read_text())['pid']
    expected = Path(__file__).resolve().parents[1] / 'out/build/RelWithDebInfo/army_of_two.exe'
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        raise RuntimeError('Probe process is not live')
    try:
        name = c.create_unicode_buffer(32768); count = w.DWORD(len(name))
        if not k.QueryFullProcessImageNameW(handle, 0, name, c.byref(count)) or Path(name.value).resolve() != expected.resolve():
            raise RuntimeError('Probe PID is not this project game executable')
    finally:
        k.CloseHandle(handle)
    events = json.loads(args.events.read_text()) if args.events else []
    events = sorted(events, key=lambda event: event['at'])
    frames, stamps = [], []
    scratch = args.probe / 'gif-current-frame.png'
    state = '0000 0 0 0 0 0 0\n'
    begin = time.monotonic()
    stop = threading.Event()
    event_errors = []
    def play_events():
        current = state
        next_event = 0
        written = None
        last_write = -10.0
        try:
            while not stop.is_set():
                elapsed = time.monotonic() - begin
                while next_event < len(events) and events[next_event]['at'] <= elapsed:
                    event = events[next_event]; next_event += 1
                    current = event.get('state', current).strip() + '\n'
                    if 'command' in event:
                        with (args.probe / 'game.commands').open('a') as output:
                            output.write(event['command'] + '\n')
                # Refresh before the 1.5s watchdog expires, without repeatedly
                # truncating the file while the native input thread reads it.
                if current != written or elapsed - last_write > 1.3:
                    (args.probe / args.state_file).write_text(current)
                    written, last_write = current, elapsed
                stop.wait(.04)
        except Exception as error:
            event_errors.append(error)
    worker = threading.Thread(target=play_events) if args.events else None
    if worker:
        worker.start()
    try:
        while time.monotonic() - begin < args.seconds:
            windows = game_windows(pid)
            if len(windows) != 1:
                raise RuntimeError('Expected exactly one live game window')
            hwnd, title, width, height = windows[0]
            rect = w.RECT(); client = w.RECT(); origin = w.POINT()
            user = c.windll.user32
            user.GetWindowRect(w.HWND(hwnd), c.byref(rect))
            user.GetClientRect(w.HWND(hwnd), c.byref(client))
            user.ClientToScreen(w.HWND(hwnd), c.byref(origin))
            if not capture(hwnd, width, height, scratch):
                raise RuntimeError('Game window capture failed')
            with Image.open(scratch) as image:
                image = image.crop((origin.x - rect.left, origin.y - rect.top,
                                    origin.x - rect.left + client.right,
                                    origin.y - rect.top + client.bottom)).convert('RGB')
                image = image.resize((args.width, round(image.height * args.width / image.width)), Image.Resampling.LANCZOS)
                frames.append(image)
            stamps.append(time.monotonic() - begin)
            time.sleep(max(0, len(frames) / args.fps - (time.monotonic() - begin)))
    finally:
        stop.set()
        if worker:
            worker.join()
        if args.events:
            (args.probe / args.state_file).write_text('0000 0 0 0 0 0 0\n')
    if event_errors:
        raise event_errors[0]
    # A shared palette avoids independent color quantization changing each frame.
    sheet = Image.new('RGB', (640, 360))
    for index in range(12):
        frame = frames[min(len(frames)-1, index * len(frames)//12)]
        sheet.paste(frame.resize((160, 120)), ((index % 4)*160, (index//4)*120))
    palette = sheet.quantize(colors=256)
    quantized = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    duration = [max(20, round(((stamps[i+1] if i+1 < len(stamps) else args.seconds) - stamps[i])*100)*10)
                for i in range(len(stamps))]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frames[-1].save(args.output.with_suffix('.last.png'))
    quantized[0].save(args.output, save_all=True, append_images=quantized[1:],
                      duration=duration, loop=0, optimize=False, palette=palette.getpalette())
    args.output.with_suffix('.json').write_text(json.dumps({
        'frames': len(frames), 'sample_seconds': stamps, 'duration_ms': duration,
        'events': events, 'exe_sha256': hashlib.sha256(expected.read_bytes()).hexdigest(),
        'scope': 'Owned game client area; demonstration, not an FPS measurement'}, indent=2))
    print(f'Saved {len(frames)} frames: {args.output}')


if __name__ == '__main__':
    main()
