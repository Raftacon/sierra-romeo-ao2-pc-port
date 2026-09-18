"""Briefly sample a verified native probe thread; resume before analysis or file I/O."""
import argparse
import collections
import ctypes as c
from ctypes import wintypes as w
import datetime
import hashlib
import json
import platform
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--thread', type=int)
    selection.add_argument('--thread-name', help='Exact Windows thread description; must match one live thread')
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--stack-bytes', type=int, choices=(0, 64, 256), default=0,
                        help='Optional raw stack snapshot, not an unwound call stack')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != 'Windows' or c.sizeof(c.c_void_p) != 8 or platform.machine().upper() not in ('AMD64', 'X86_64'):
        parser.error('Requires native Windows x64 Python')
    if not 1 <= args.seconds <= 30 or args.output.exists():
        parser.error('Use a new output file and 1 to 30 seconds')
    k, p = c.WinDLL('kernel32', use_last_error=True), c.WinDLL('psapi', use_last_error=True)
    def api(dll, name, result, *arguments):
        fn = getattr(dll, name); fn.restype = result; fn.argtypes = arguments; return fn
    op = api(k, 'OpenProcess', w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    ot = api(k, 'OpenThread', w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
    close = api(k, 'CloseHandle', w.BOOL, w.HANDLE)
    owner = api(k, 'GetProcessIdOfThread', w.DWORD, w.HANDLE)
    description = api(k, 'GetThreadDescription', c.c_long, w.HANDLE, c.POINTER(w.LPWSTR))
    local_free = api(k, 'LocalFree', c.c_void_p, c.c_void_p)
    def thread_description(handle):
        value = w.LPWSTR()
        try:
            if description(handle, c.byref(value)) < 0:
                raise RuntimeError('GetThreadDescription failed')
            return value.value or ''
        finally:
            if value: local_free(c.cast(value, c.c_void_p))
    query = api(k, 'QueryFullProcessImageNameW', w.BOOL, w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD))
    suspend = api(k, 'SuspendThread', w.DWORD, w.HANDLE)
    resume = api(k, 'ResumeThread', w.DWORD, w.HANDLE)
    context = api(k, 'GetThreadContext', w.BOOL, w.HANDLE, c.c_void_p)
    read_memory = api(k, 'ReadProcessMemory', w.BOOL, w.HANDLE, c.c_void_p, c.c_void_p, c.c_size_t, c.POINTER(c.c_size_t))
    modules = api(p, 'EnumProcessModules', w.BOOL, w.HANDLE, c.POINTER(w.HMODULE), w.DWORD, c.POINTER(w.DWORD))
    module_name = api(p, 'GetModuleFileNameExW', w.DWORD, w.HANDLE, w.HMODULE, w.LPWSTR, w.DWORD)
    class ModuleInfo(c.Structure):
        _fields_ = [('base', c.c_void_p), ('size', w.DWORD), ('entry', c.c_void_p)]
    module_info = api(p, 'GetModuleInformation', w.BOOL, w.HANDLE, w.HMODULE, c.POINTER(ModuleInfo), w.DWORD)
    pid = json.loads((args.probe / 'running.json').read_text())['pid']
    expected = (Path(__file__).resolve().parents[1] / 'out/build/RelWithDebInfo/army_of_two.exe').resolve()
    process, thread = op(0x410, False, pid), None
    if not process: raise c.WinError(c.get_last_error())
    report = {'pid': pid, 'thread': args.thread, 'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'requested_seconds': args.seconds, 'samples': [], 'modules': [], 'errors': [],
              'limitation': 'RIP snapshots perturb scheduling and identify sampled locations, not complete stacks or causes of individual stutters.'}
    try:
        name, length = c.create_unicode_buffer(32768), w.DWORD(32768)
        if not query(process, 0, name, c.byref(length)) or Path(name.value).resolve() != expected:
            raise RuntimeError('PID is not the expected native build')
        if args.thread_name:
            class ThreadEntry(c.Structure):
                _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('tid', w.DWORD),
                            ('pid', w.DWORD), ('priority', w.LONG), ('delta', w.LONG), ('flags', w.DWORD)]
            snapshot = api(k, 'CreateToolhelp32Snapshot', w.HANDLE, w.DWORD, w.DWORD)(4, 0)
            if snapshot == c.c_void_p(-1).value: raise c.WinError(c.get_last_error())
            first = api(k, 'Thread32First', w.BOOL, w.HANDLE, c.POINTER(ThreadEntry))
            next_thread = api(k, 'Thread32Next', w.BOOL, w.HANDLE, c.POINTER(ThreadEntry))
            report['thread_inventory'] = []
            try:
                entry = ThreadEntry(); entry.size = c.sizeof(entry)
                present = first(snapshot, c.byref(entry))
                while present:
                    if entry.pid == pid:
                        candidate = ot(0x0800, False, entry.tid)
                        if candidate:
                            try:
                                if owner(candidate) == pid:
                                    report['thread_inventory'].append({'thread': entry.tid, 'name': thread_description(candidate)})
                            finally:
                                close(candidate)
                    entry.size = c.sizeof(entry)
                    present = next_thread(snapshot, c.byref(entry))
                if c.get_last_error() != 18: raise c.WinError(c.get_last_error())  # ERROR_NO_MORE_FILES
            finally:
                close(snapshot)
            matches = [t for t in report['thread_inventory'] if t['name'] == args.thread_name]
            if len(matches) != 1:
                raise RuntimeError(f'Expected one thread named {args.thread_name!r}; found {matches}; inventory={report["thread_inventory"]}')
            args.thread = report['thread'] = matches[0]['thread']
        thread = ot(0x0800 | 0x0008 | 0x0002, False, args.thread)
        if not thread or owner(thread) != pid: raise RuntimeError('Thread does not belong to this native probe')
        report['thread_name'] = thread_description(thread)
        if args.thread_name and report['thread_name'] != args.thread_name:
            raise RuntimeError('Selected thread description changed before sampling')
        report['executable_sha256'] = hashlib.sha256(expected.read_bytes()).hexdigest()
        handles, needed = (w.HMODULE * 1024)(), w.DWORD()
        if not modules(process, handles, c.sizeof(handles), c.byref(needed)) or needed.value > c.sizeof(handles):
            raise RuntimeError('Could not enumerate all native modules')
        for handle in handles[:needed.value // c.sizeof(w.HMODULE)]:
            info, path = ModuleInfo(), c.create_unicode_buffer(32768)
            if not module_info(process, handle, c.byref(info), c.sizeof(info)) or not module_name(process, handle, path, len(path)):
                raise RuntimeError('Module inventory incomplete')
            report['modules'].append({'path': path.value, 'base': info.base, 'size': info.size})
        # AMD64 CONTEXT: 16-byte alignment, flags at 48, RIP at 248.
        storage = c.create_string_buffer(1232 + 15)
        address = (c.addressof(storage) + 15) & ~15
        c.c_uint32.from_address(address + 48).value = 0x00100001  # CONTEXT_CONTROL
        stack = c.create_string_buffer(max(1, args.stack_bytes))
        read_size = c.c_size_t()
        report['stack_bytes'] = args.stack_bytes
        start = time.perf_counter()
        while time.perf_counter() - start < args.seconds:
            previous, rip, error, rsp, stack_ok = 0xFFFFFFFF, None, None, None, False
            entered = time.perf_counter()
            try:
                previous = suspend(thread)
                if previous == 0xFFFFFFFF: error = 'SuspendThread failed'
                elif previous != 0: error = 'Thread was already suspended; sample excluded'
                elif not context(thread, address): error = 'GetThreadContext failed'
                else:
                    rip = c.c_uint64.from_address(address + 248).value
                    rsp = c.c_uint64.from_address(address + 152).value
                    if args.stack_bytes:
                        stack_ok = bool(read_memory(process, rsp, stack, args.stack_bytes, c.byref(read_size))) and read_size.value == args.stack_bytes
            finally:
                if previous != 0xFFFFFFFF and resume(thread) == 0xFFFFFFFF:
                    raise RuntimeError('ResumeThread failed; stop sampling and inspect the probe')
            # The thread is running before appending, formatting, sleeping or writing.
            resumed = time.perf_counter()
            if error:
                report['errors'].append(error)
                break
            report['samples'].append({'seconds': entered - start, 'rip': rip, 'suspend_window_ms': (resumed - entered) * 1000})
            if args.stack_bytes:
                report['samples'][-1].update(rsp=rsp, stack_read_ok=stack_ok,
                    stack_qwords=[int.from_bytes(stack.raw[i:i+8], 'little') for i in range(0, args.stack_bytes, 8)] if stack_ok else [])
            time.sleep(.005)
        report['elapsed_seconds'] = time.perf_counter() - start
    finally:
        if thread: close(thread)
        close(process)
    counts = collections.Counter()
    for sample in report['samples']:
        match = next((m for m in report['modules'] if m['base'] <= sample['rip'] < m['base'] + m['size']), None)
        sample['module'] = match['path'] if match else None
        sample['rva'] = sample['rip'] - match['base'] if match else None
        counts[(sample['module'], sample['rva'])] += 1
    report['locations'] = [{'module': key[0], 'rva': key[1], 'samples': n} for key, n in counts.most_common()]
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'samples': len(report['samples']), 'errors': report['errors'], 'top_locations': report['locations'][:10]}, indent=2))
    if report['errors']: raise SystemExit(1)


if __name__ == '__main__':
    main()
