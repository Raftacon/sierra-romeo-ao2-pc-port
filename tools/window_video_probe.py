"""Record only this game's window, with actual capture timestamps beside the video.

The lossless video stores samples at a nominal 60 FPS for frame stepping. It is
not wall-clock playback; use frames.jsonl for timing. Capture affects game speed.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time

from probe import game_windows as windows, user, gdi
from pc_input_probe import k


def record(args, output):
    handle = k.OpenProcess(0x1000, False, args.pid)
    if not handle:
        raise OSError(c.get_last_error(), 'Could not open target process')
    try:
        name = c.create_unicode_buffer(32768)
        count = w.DWORD(len(name))
        if not k.QueryFullProcessImageNameW(handle, 0, name, c.byref(count)):
            raise OSError(c.get_last_error(), 'Could not identify target')
        expected = Path(__file__).resolve().parents[1] / 'out/build/RelWithDebInfo/army_of_two.exe'
        if Path(name.value).resolve() != expected.resolve():
            raise RuntimeError('Target is not this project game executable')
    finally:
        k.CloseHandle(handle)
    targets = windows(args.pid)
    if len(targets) != 1:
        raise RuntimeError('Expected exactly one game window')
    hwnd, title, width, height = targets[0]
    if not 0 < width <= 8192 or not 0 < height <= 8192:
        raise RuntimeError('Invalid capture dimensions')
    if args.crop:
        x, y, crop_width, crop_height = args.crop
        if x < 0 or y < 0 or min(crop_width, crop_height) <= 0 or x + crop_width > width or y + crop_height > height:
            raise ValueError('Crop must lie inside the captured window')
    command = [args.ffmpeg, '-hide_banner', '-loglevel', 'warning',
               '-f', 'rawvideo', '-pixel_format', 'bgr0', '-video_size', f'{width}x{height}',
               '-framerate', '60', '-i', 'pipe:0', '-an',
               *(['-vf', f'crop={crop_width}:{crop_height}:{x}:{y}'] if args.crop else []),
               '-c:v', 'libx264rgb',
               '-crf', '0', '-preset', 'ultrafast', '-threads', '2', '-pix_fmt', 'bgr24',
               str(output / 'game-window.mkv')]
    (output / 'video.json').write_text(json.dumps({
        'pid': args.pid, 'window_title': title, 'width': width, 'height': height,
        'executable_sha256': hashlib.sha256(expected.read_bytes()).hexdigest(),
        'command': command, 'requested_seconds': args.seconds,
        'crop_xywh': args.crop,
        'frame_hash_scope': 'Full captured window in raw BGR0; video may contain only the specified crop.',
        'ffmpeg_version': subprocess.check_output([args.ffmpeg, '-version'], text=True).splitlines()[0],
        'interpretation': __doc__.strip()
    }, indent=2) + '\n')
    dc = user.GetDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc) if dc else None
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height) if memory else None
    previous = gdi.SelectObject(memory, bitmap) if bitmap else None
    encoder = None
    frames = 0
    begin = time.monotonic()
    try:
        if not all((dc, memory, bitmap, previous)):
            raise RuntimeError('Could not allocate window capture resources')
        header = c.create_string_buffer(struct.pack('<IiiHHIIiiII', 40, width, -height,
            1, 32, 0, width * height * 4, 0, 0, 0, 0))
        pixels = c.create_string_buffer(width * height * 4)
        with (output / 'encoder.log').open('w') as log, (output / 'frames.jsonl').open('w', buffering=1) as stamps:
            encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log,
                                       stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            begin = time.monotonic()
            while time.monotonic() - begin < args.seconds:
                current = windows(args.pid)
                if len(current) != 1 or current[0][0] != hwnd or current[0][2:] != (width, height):
                    raise RuntimeError('Game window disappeared or changed size')
                call = time.monotonic()
                if not user.PrintWindow(hwnd, memory, 2):
                    raise RuntimeError('PrintWindow failed')
                gdi.SelectObject(memory, previous)
                if gdi.GetDIBits(memory, bitmap, 0, height, pixels, header, 0) != height:
                    raise RuntimeError('GetDIBits failed')
                gdi.SelectObject(memory, bitmap)
                stamp = time.monotonic()
                data = pixels.raw
                encoder.stdin.write(data)
                stamps.write(json.dumps({'index': frames, 'seconds': stamp - begin,
                    'capture_ms': (stamp - call) * 1000, 'sha256': hashlib.sha256(data).hexdigest()}) + '\n')
                frames += 1
                time.sleep(max(0, 1 / 60 - (time.monotonic() - call)))
    finally:
        if encoder:
            try:
                encoder.stdin.close()
            except BrokenPipeError:
                pass
            try:
                encoder.wait(timeout=10)
            except subprocess.TimeoutExpired:
                encoder.kill()
                encoder.wait()
        if previous: gdi.SelectObject(memory, previous)
        if bitmap: gdi.DeleteObject(bitmap)
        if memory: gdi.DeleteDC(memory)
        if dc: user.ReleaseDC(hwnd, dc)
    if not encoder or encoder.returncode != 0 or not frames:
        raise RuntimeError('No completed video; inspect encoder.log')
    print(f'Captured {frames} samples; use frames.jsonl for actual timing', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('--crop', nargs=4, type=int, metavar=('X', 'Y', 'WIDTH', 'HEIGHT'),
                        help='Lossless video region in full-window pixels; capture timestamps/hashes still describe the full window')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 30:
        parser.error('--seconds must be between 1 and 30')
    output = args.output.resolve()
    if args.worker:
        record(args, output)
        return
    if output.exists(): parser.error('Use a new output directory')
    executable = shutil.which(args.ffmpeg)
    if not executable: parser.error('FFmpeg is required')
    output.mkdir(parents=True)
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--pid', str(args.pid),
               '--output', str(output), '--seconds', str(args.seconds), '--ffmpeg', executable,
               *(['--crop', *map(str, args.crop)] if args.crop else [])]
    # PrintWindow and encoder pipe writes can block. Keep them in a worker with
    # an external deadline. Killing it closes the encoder's sole input pipe.
    with (output / 'capture.log').open('w') as log:
        worker = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        timed_out = False
        try:
            worker.wait(timeout=args.seconds + 25)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            if worker.poll() is None:
                worker.kill()
                worker.wait()
            (output / 'result.json').write_text(json.dumps({
                'exit_code': worker.returncode, 'timed_out': timed_out,
                'interpretation': 'Capture completion is not a visual-parity pass.'
            }, indent=2) + '\n')
    if timed_out or worker.returncode != 0:
        raise RuntimeError('Capture did not complete; inspect capture.log and result.json')
    print(output)


if __name__ == '__main__': main()
