"""Launch bounded native startup runs under ProcDump from the first instruction."""
import argparse
import ctypes as c
from ctypes import wintypes as w
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from probe import windows


kernel = c.WinDLL('kernel32', use_last_error=True)


class ProcessEntry(c.Structure):
    _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
                ('heap', c.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
                ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD),
                ('exe', w.WCHAR * 260)]


kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
kernel.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
kernel.Process32NextW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel.OpenProcess.restype = w.HANDLE
kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
kernel.CloseHandle.argtypes = [w.HANDLE]


def child_process(parent, executable):
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    try:
        entry = ProcessEntry()
        entry.size = c.sizeof(entry)
        more = kernel.Process32FirstW(snapshot, c.byref(entry))
        while more:
            if entry.parent == parent and entry.exe.lower() == executable.name.lower():
                handle = kernel.OpenProcess(0x1000 | 1, False, entry.pid)
                if handle:
                    path = c.create_unicode_buffer(32768)
                    size = w.DWORD(len(path))
                    if (kernel.QueryFullProcessImageNameW(handle, 0, path, c.byref(size))
                            and Path(path.value).resolve() == executable):
                        return entry.pid, handle
                    kernel.CloseHandle(handle)
            more = kernel.Process32NextW(snapshot, c.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return None


def exit_code(handle):
    code = w.DWORD()
    if not kernel.GetExitCodeProcess(handle, c.byref(code)):
        raise c.WinError(c.get_last_error())
    return None if code.value == 259 else code.value


def fingerprints(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob('*') if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', type=Path, required=True)
    parser.add_argument('--runs', type=int, default=3)
    parser.add_argument('--seconds', type=int, default=75)
    args = parser.parse_args()
    if not 1 <= args.runs <= 5 or not 15 <= args.seconds <= 120:
        parser.error('Use 1 to 5 runs, each 15 to 120 seconds')
    root = Path(__file__).resolve().parents[1]
    output, source = args.output.resolve(), args.profile.resolve()
    debugger = root / '.tools/procdump/procdump64.exe'
    executable = root / 'out/build/RelWithDebInfo/army_of_two.exe'
    if not debugger.is_file() or not executable.is_file():
        parser.error('Build the native executable and install ProcDump locally first')
    if output.exists() or not source.is_dir() or source in output.parents:
        parser.error('Use a new output directory outside the existing source profile')
    hashes = fingerprints(source)
    output.mkdir(parents=True)
    env = dict(os.environ)
    for name in list(env):
        if name.startswith('AOT_'):
            del env[name]
    env.update(AOT_INPUT_SCRIPT=str(root / 'config/input-graphics.script'), AOT_TEST_KBM='1')
    report = {'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(),
              'plugin_sha256': hashlib.sha256(executable.with_name('rexgpu-xenosrd.dll').read_bytes()).hexdigest(),
              'runtime_sha256': hashlib.sha256(executable.with_name('rexruntimerd.dll').read_bytes()).hexdigest(),
              'procdump_sha256': hashlib.sha256(debugger.read_bytes()).hexdigest(),
              'input_environment': {name: env[name] for name in
                  ('AOT_INPUT_SCRIPT', 'AOT_TEST_KBM', '_NO_DEBUG_HEAP') if name in env},
              'source_profile_sha256': hashes, 'runs': [],
              'interpretation': 'Debugger-instrumented startup; no performance or rendering-parity claim.'}
    try:
        for index in range(args.runs):
            case = output / f'run-{index + 1:02d}'
            case.mkdir()
            shutil.copytree(source, case / 'user')
            command = [str(debugger), '-accepteula', '-e', '1', '-f', 'C0000374',
                       '-mm', '-x', str(case), str(executable),
                       '--game_data_root=' + str(root / 'assets'),
                       '--user_data_root=' + str(case / 'user'),
                       '--cache_root=' + str(root / 'artifacts/native-cache'),
                       '--gpu_plugin=xenos', '--mnk_mode=true', '--input_backend=xinput',
                       '--log_file=' + str(case / 'runtime.log'), '--log_level=debug',
                       '--log_flush_interval=1', '--fullscreen=false',
                       '--window_width=1280', '--window_height=720',
                       '--readback_resolve=fast', '--vsync=true', '--aot_fps=60',
                       '--resolution_scale=1', '--swap_post_effect=fxaa',
                       '--anisotropic_override=5', '--readback_resolve_half_pixel_offset=true']
            item = {'command': command, 'pid': None, 'close_requested': False,
                    'forced_cleanup': False}
            report['runs'].append(item)
            target = None
            start = time.monotonic()
            with (case / 'procdump.log').open('wb') as log:
                process = subprocess.Popen(command, cwd=case, env=env, stdout=log,
                                           stderr=subprocess.STDOUT,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    while process.poll() is None:
                        elapsed = time.monotonic() - start
                        if target is None:
                            target = child_process(process.pid, executable)
                            if target:
                                item['pid'] = target[0]
                                (case / 'running.json').write_text(json.dumps(item, indent=2))
                                print(f'Run {index + 1}: native PID {target[0]} under ProcDump', flush=True)
                        if elapsed >= args.seconds and not item['close_requested']:
                            if target and exit_code(target[1]) is None:
                                for hwnd, *_ in windows(target[0]):
                                    c.windll.user32.PostMessageW(w.HWND(hwnd), 0x0010, 0, 0)
                            item['close_requested'] = True
                        if elapsed >= args.seconds + 15:
                            break
                        time.sleep(.1)
                finally:
                    if target:
                        item['exit_code_before_cleanup'] = exit_code(target[1])
                        if item['exit_code_before_cleanup'] is None:
                            item['forced_cleanup'] = True
                            kernel.TerminateProcess(target[1], 1)
                        kernel.CloseHandle(target[1])
                    if process.poll() is None:
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            process.terminate()
                    process.wait(timeout=10)
                    item['monitor_exit_code'] = process.returncode
                    item['elapsed_seconds'] = time.monotonic() - start
                    item['dumps'] = [p.name for p in case.glob('*.dmp')]
                    (case / 'result.json').write_text(json.dumps(item, indent=2) + '\n')
                    print(f'Run {index + 1}: exit={item.get("exit_code_before_cleanup")}, dumps={item["dumps"]}', flush=True)
            if item['dumps'] or item.get('exit_code_before_cleanup') != 0:
                break
    finally:
        report['source_profile_unchanged'] = fingerprints(source) == hashes
        (output / 'heap-probe.json').write_text(json.dumps(report, indent=2) + '\n')
    if not report['source_profile_unchanged']:
        raise RuntimeError('Source profile changed during the probe')
    if not any(run['dumps'] for run in report['runs']) and any(
            run.get('exit_code_before_cleanup') != 0 or run['forced_cleanup']
            or run['monitor_exit_code'] != 0 for run in report['runs']):
        raise RuntimeError('A probe did not close normally and produced no dump; inspect the report')


if __name__ == '__main__':
    main()
