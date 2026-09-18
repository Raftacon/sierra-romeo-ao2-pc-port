"""Bounded Windows startup probe. Saves logs and captures only the game's window.

Run with .tools/py/Scripts/python.exe (Pillow required). A live process at the
deadline is reported as a timeout, never as proof of successful gameplay.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import subprocess
import time
import os
import hashlib
import re
import traceback

from PIL import Image

user = c.WinDLL('user32', use_last_error=True)
gdi = c.WinDLL('gdi32', use_last_error=True)
user.GetDC.argtypes = [w.HWND]
user.GetDC.restype = w.HDC
user.ReleaseDC.argtypes = [w.HWND, w.HDC]
gdi.CreateCompatibleDC.argtypes = [w.HDC]
gdi.CreateCompatibleDC.restype = w.HDC
gdi.CreateCompatibleBitmap.argtypes = [w.HDC, c.c_int, c.c_int]
gdi.CreateCompatibleBitmap.restype = w.HBITMAP
gdi.SelectObject.argtypes = [w.HDC, w.HANDLE]
gdi.SelectObject.restype = w.HANDLE
gdi.DeleteObject.argtypes = [w.HANDLE]
gdi.DeleteDC.argtypes = [w.HDC]
user.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]
user.GetWindowRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
user.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, c.c_int]
gdi.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, c.c_void_p, c.c_void_p, w.UINT]


def windows(pid):
    found = []
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    @callback_type
    def visit(hwnd, _):
        process = w.DWORD()
        user.GetWindowThreadProcessId(hwnd, c.byref(process))
        if process.value == pid:
            title = c.create_unicode_buffer(1024)
            user.GetWindowTextW(hwnd, title, len(title))
            rect = w.RECT()
            user.GetWindowRect(hwnd, c.byref(rect))
            if rect.right > rect.left and rect.bottom > rect.top:
                found.append((hwnd, title.value, rect.right - rect.left, rect.bottom - rect.top))
        return True

    user.EnumWindows(visit, 0)
    return found


def game_windows(pid):
    """Select the main game surface, excluding presenter's 1x1 Temp Window."""
    return [entry for entry in windows(pid) if entry[1].startswith(('Sierra Romeo', 'Army of Two'))
            and entry[2] >= 320 and entry[3] >= 320]


def capture(hwnd, width, height, path):
    if not 0 < width <= 8192 or not 0 < height <= 8192:
        return False
    dc = user.GetDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        ok = user.PrintWindow(hwnd, memory, 2)
        # BITMAPINFOHEADER (top-down 32-bit RGB).
        import struct
        header = c.create_string_buffer(struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0))
        pixels = c.create_string_buffer(width * height * 4)
        gdi.SelectObject(memory, previous)
        if ok and gdi.GetDIBits(memory, bitmap, 0, height, pixels, header, 0):
            Image.frombytes('RGB', (width, height), pixels.raw, 'raw', 'BGRX').save(path)
            return True
        return False
    finally:
        gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, dc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=['native', 'xenia', 'xenia-scripted'], default='native')
    parser.add_argument('--gpu-plugin', default='xenos', help='Native GPU plugin staged beside the executable')
    parser.add_argument('--seconds', type=float, default=25)
    parser.add_argument('--script-seconds', type=float,
                        help='Also bound observation from the recorded first scripted controller poll; --seconds remains the hard launch deadline')
    parser.add_argument('--capture-interval', type=float, default=5, help='Periodic screenshots in seconds; 0 disables them')
    parser.add_argument('--log-level', choices=('info','debug'), default='debug', help='Native runtime logging level')
    parser.add_argument('--network-trace', action='store_true', help='Bounded native network/session import trace in network.csv')
    parser.add_argument('--fullscreen', action='store_true', help='Test the native fullscreen window')
    parser.add_argument('--use-saved-settings', action='store_true', help='Load PC preferences without overriding display mode or size')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--user-data', type=Path, help='Reuse a specific test save/profile directory')
    parser.add_argument('--cache-root', type=Path, help='Explicit shader cache for controlled cold/warm comparisons')
    parser.add_argument('--renderdoc', type=Path, help='Opt-in renderdoccmd.exe injection before the owned native process starts')
    parser.add_argument('extra', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.network_trace and args.backend != 'native':
        parser.error('--network-trace requires native backend')
    if args.renderdoc and (args.backend != 'native' or not args.renderdoc.is_file()):
        parser.error('--renderdoc requires native backend and an existing renderdoccmd.exe')
    if args.script_seconds is not None and (args.backend != 'native' or not os.environ.get('AOT_INPUT_SCRIPT') or
                                           not 0 < args.script_seconds <= args.seconds):
        parser.error('--script-seconds requires native scripted input and a positive duration within --seconds')
    if args.capture_interval != 0 and args.capture_interval < .25:
        parser.error('Capture interval must be 0 (disabled) or at least .25 seconds')
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if args.network_trace:
        os.environ['AOT_NETWORK_LOG'] = str(output / 'network.csv')
    user_data = args.user_data.resolve() if args.user_data else output / 'user'
    cache_root = args.cache_root.resolve() if args.cache_root else root / 'artifacts/native-cache'
    if args.backend == 'native':
        command = [str(root / 'out/build/RelWithDebInfo/army_of_two.exe'),
                   '--game_data_root=' + str(root / 'assets'),
                   '--user_data_root=' + str(user_data),
                   '--cache_root=' + str(cache_root),
                   '--gpu_plugin=' + args.gpu_plugin, '--mnk_mode=true',
                   '--log_file=' + str(output / 'runtime.log'), '--log_level='+args.log_level,
                   '--log_flush_interval=1']
        if not args.use_saved_settings:
            command.extend(['--fullscreen=' + str(args.fullscreen).lower(), '--window_width=1280', '--window_height=720'])
        elif args.fullscreen:
            command.append('--fullscreen=true')
    else:
        baseline_dir = '.tools/xenia-scripted' if args.backend == 'xenia-scripted' else '.tools/xenia'
        command = [str(root / baseline_dir / 'xenia_canary.exe'), str(root / 'assets/default.xex'),
                   '--storage_root=' + str(user_data),
                   '--log_file=' + str(output / 'runtime.log')]
        if args.backend == 'xenia-scripted': command.append('--hid=xinput')
    command.extend(args.extra[1:] if args.extra[:1] == ['--'] else args.extra)
    report = {'backend': args.backend, 'command': command, 'captures': [], 'windows': []}
    if os.environ.get('AOT_NETWORK_LOG'):
        report['network_trace'] = os.environ['AOT_NETWORK_LOG']
    if os.environ.get('AOT_PC_COOP_DIAGNOSTIC'):
        report['pc_coop_menu_diagnostic'] = os.environ['AOT_PC_COOP_DIAGNOSTIC']
    if os.environ.get('AOT_PC_COOP_UI_DIAGNOSTIC'):
        report['pc_coop_ui_diagnostic'] = os.environ['AOT_PC_COOP_UI_DIAGNOSTIC']
    report['executable_sha256'] = hashlib.sha256(Path(command[0]).read_bytes()).hexdigest()
    if args.backend == 'native':
        plugin = Path(command[0]).parent / ('rexgpu-' + args.gpu_plugin + 'rd.dll')
        report['gpu_plugin'] = {'path': str(plugin), 'sha256': hashlib.sha256(plugin.read_bytes()).hexdigest()}
        report['shader_cache_before'] = {'path': str(cache_root), 'files': {
            str(path.relative_to(cache_root)): {'size': path.stat().st_size,
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted((cache_root / 'shaders').rglob('*'))
            if path.is_file() and path.suffix in ('.xsh', '.xpso')}}
    report['xex_sha256'] = hashlib.sha256((root / 'assets/default.xex').read_bytes()).hexdigest()
    report['input_environment'] = {name: os.environ[name] for name in ('AOT_INPUT_SCRIPT', 'AOT_INPUT_STATE', 'AOT_TEST_KBM', 'AOT_TRACE_MOUSE', 'AOT_TRACE_FONT', 'AOT_TRACE_TEXT', 'AOT_TRACE_READBACK_RETIREMENT', 'AOT_TIMER_RESOLUTION_MS', 'AOT_TRACE_HUD', 'AOT_TRACE_COOP_TEXTURE', 'AOT_REPLACE_COOP_TEXTURE', 'AOT_PROFILE', 'AOT_FRAME_PHASE_LOG', 'AOT_WAIT_LOG', 'AOT_RENDER_WAIT_LOG', 'AOT_GPU_WAIT_LOG', 'AOT_GPU_COPY_LOG', 'AOT_GPU_VBLANK_LOG', 'AOT_GPU_VBLANK_WATCH', 'AOT_RECT_LOG') if name in os.environ}
    if 'AOT_FRAME_COLOR_PROBE' in os.environ:
        report['input_environment']['AOT_FRAME_COLOR_PROBE'] = os.environ['AOT_FRAME_COLOR_PROBE']
    if 'AOT_INPUT_SCRIPT' in os.environ:
        (output / 'input.script').write_bytes(Path(os.environ['AOT_INPUT_SCRIPT']).read_bytes())
    start = time.monotonic()
    start_unix = time.time()
    script_start = None
    # Suppress Windows crash popups so an unattended crash has an exit code.
    c.windll.kernel32.SetErrorMode(0x0002 | 0x8000)
    with (output / 'console.log').open('w') as log:
        obs_capture_guard = None
        process = subprocess.Popen(command, cwd=output, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=0x00000004 if args.renderdoc else 0)
        if args.renderdoc:
            try:
                # OBS's duplicate-hook guard is per PID. Keep its D3D11On12
                # hook out of this diagnostic child while RenderDoc wraps D3D12.
                # No OBS settings or other processes are changed.
                create_mutex = c.windll.kernel32.CreateMutexW
                create_mutex.argtypes = [c.c_void_p, w.BOOL, w.LPCWSTR]
                create_mutex.restype = w.HANDLE
                obs_capture_guard = create_mutex(None, False, f'graphics_hook_dup_mutex{process.pid}')
                if not obs_capture_guard:
                    raise c.WinError()
                injector = args.renderdoc.resolve()
                injected = subprocess.run([str(injector), 'inject', '--PID=' + str(process.pid),
                    '--capture-file=' + str(output / 'gpu-frame')],
                    capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                (output / 'renderdoc-inject.log').write_text(injected.stdout + injected.stderr)
                ident = re.search(r'Launched as ID (\d+)', injected.stdout + injected.stderr)
                if not ident or injected.returncode != int(ident[1]):
                    raise RuntimeError('RenderDoc injection failed; see renderdoc-inject.log')
                # Popen owns the process handle, but closes its initial thread handle.
                # Resume only this suspended child after injection has completed.
                resume = c.WinDLL('ntdll').NtResumeProcess
                resume.argtypes = [w.HANDLE]
                resume.restype = c.c_long
                status = resume(int(process._handle))
                if status < 0:
                    raise RuntimeError(f'NtResumeProcess failed: {status:#x}')
                report['renderdoc'] = {'injector': str(injector),
                    'ident': int(ident[1]),
                    'obs_capture_isolated_for_pid': process.pid,
                    'sha256': hashlib.sha256(injector.read_bytes()).hexdigest(),
                    'injection_log': injected.stdout + injected.stderr}
                (output / 'renderdoc.json').write_text(json.dumps(report['renderdoc'], indent=2))
            except BaseException:
                process.terminate()
                process.wait(timeout=10)
                if obs_capture_guard:
                    c.windll.kernel32.CloseHandle(w.HANDLE(obs_capture_guard))
                raise
        report['pid'] = process.pid
        # Diagnostics may target this exact child while a probe is running.
        # Only probe.json, written after cleanup, is a completion report.
        (output / 'running.json').write_text(json.dumps({'pid': process.pid, 'command': command, 'start_unix_seconds': start_unix}, indent=2) + '\n')
        last_capture = 0
        try:
            while process.poll() is None and time.monotonic() - start < args.seconds:
                elapsed = time.monotonic() - start
                if args.script_seconds is not None:
                    runtime_log = output / 'runtime.log'
                    if script_start is None and runtime_log.exists():
                        match = re.search(r'AOT scripted controller clock started: unix_ms=(\d+)',
                                          runtime_log.read_text(errors='replace'))
                        if match:
                            script_start = int(match[1]) / 1000 - start_unix
                            if not 0 <= script_start <= elapsed + 1:
                                raise RuntimeError('Script clock marker is outside this process lifetime')
                            report['script_clock_started_seconds'] = script_start
                    if script_start is not None and elapsed - script_start >= args.script_seconds:
                        break
                if args.capture_interval and elapsed - last_capture >= args.capture_interval:
                    last_capture = elapsed
                    for index, (hwnd, title, width, height) in enumerate(windows(process.pid)):
                        report['windows'].append({'seconds': elapsed, 'title': title, 'width': width, 'height': height})
                        filename = f'frame-{int(elapsed):03d}-{index}.png'
                        if capture(hwnd, width, height, output / filename):
                            report['captures'].append(filename)
                time.sleep(.1)
            report['elapsed_seconds'] = time.monotonic() - start
            report['exit_code_before_cleanup'] = process.poll()
            report['timed_out'] = process.poll() is None
            if args.script_seconds is not None:
                report['script_observation_seconds'] = args.script_seconds
                report['script_elapsed_seconds'] = None if script_start is None else report['elapsed_seconds'] - script_start
        except BaseException:
            report['elapsed_seconds'] = time.monotonic() - start
            report['exit_code_before_cleanup'] = process.poll()
            report['timed_out'] = False
            report['harness_error'] = traceback.format_exc()
            raise
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            if obs_capture_guard:
                c.windll.kernel32.CloseHandle(w.HANDLE(obs_capture_guard))
            (output / 'probe.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    if args.script_seconds is not None and script_start is None:
        raise RuntimeError('No scripted controller clock marker; replay timing was not verified')


if __name__ == '__main__':
    main()
