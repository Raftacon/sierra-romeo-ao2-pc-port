"""Send a bounded keyboard/mouse test to this project's foreground game window."""
import argparse
import ctypes as c
from ctypes import wintypes as w
from pathlib import Path
import time
from probe import game_windows as windows, capture

u = c.WinDLL('user32', use_last_error=True)
k = c.WinDLL('kernel32', use_last_error=True)
u.GetForegroundWindow.restype = w.HWND
u.SetForegroundWindow.argtypes = [w.HWND]
u.BringWindowToTop.argtypes = [w.HWND]
u.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
u.AttachThreadInput.argtypes = [w.DWORD, w.DWORD, w.BOOL]
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
k.CloseHandle.argtypes = [w.HANDLE]

class Mouse(c.Structure):
    _fields_ = [('dx', w.LONG), ('dy', w.LONG), ('data', w.DWORD), ('flags', w.DWORD), ('time', w.DWORD), ('extra', c.c_size_t)]
class Keyboard(c.Structure):
    _fields_ = [('vk', w.WORD), ('scan', w.WORD), ('flags', w.DWORD), ('time', w.DWORD), ('extra', c.c_size_t)]
class Payload(c.Union):
    _fields_ = [('mouse', Mouse), ('keyboard', Keyboard)]
class Input(c.Structure):
    _fields_ = [('type', w.DWORD), ('payload', Payload)]
u.SendInput.argtypes = [w.UINT, c.POINTER(Input), c.c_int]

def send(event):
    if u.SendInput(1, c.byref(event), c.sizeof(event)) != 1:
        raise OSError(c.get_last_error(), 'SendInput failed')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pid', type=int)
    parser.add_argument('--focus', action='store_true')
    parser.add_argument('--key', help='Single letter/digit, Enter, Escape, Space, Tab, Ctrl, Shift or arrow key')
    parser.add_argument('--mouse-button', choices=['left', 'right', 'middle'])
    parser.add_argument('--move', nargs=2, type=int, metavar=('DX', 'DY'))
    parser.add_argument('--seconds', type=float, default=.2)
    parser.add_argument('--capture-during', type=Path, help='Capture this game window halfway through the held input')
    args = parser.parse_args()
    if not 0 < args.seconds <= 10: parser.error('Duration must be in (0, 10] seconds')
    if args.key and args.mouse_button: parser.error('Use one key or mouse button per invocation')
    handle = k.OpenProcess(0x1000, False, args.pid)
    if not handle: raise OSError(c.get_last_error(), 'Could not open target process')
    try:
        name = c.create_unicode_buffer(32768); count = w.DWORD(len(name))
        if not k.QueryFullProcessImageNameW(handle, 0, name, c.byref(count)): raise OSError(c.get_last_error())
        expected = Path(__file__).resolve().parents[1] / 'out/build/RelWithDebInfo/army_of_two.exe'
        if Path(name.value).resolve() != expected.resolve(): raise RuntimeError('Target is not this project game executable')
    finally: k.CloseHandle(handle)
    targets = windows(args.pid)
    if len(targets) != 1: raise RuntimeError(f'Expected one game window, found {len(targets)}')
    hwnd = targets[0][0]
    if args.focus:
        previous = u.GetForegroundWindow()
        own = k.GetCurrentThreadId(); other = u.GetWindowThreadProcessId(previous, None)
        attached = u.AttachThreadInput(own, other, True)
        try: u.BringWindowToTop(hwnd); u.SetForegroundWindow(hwnd)
        finally:
            if attached: u.AttachThreadInput(own, other, False)
        time.sleep(.1)
    def check_focus():
        if u.GetForegroundWindow() != hwnd: raise RuntimeError('Game is not foreground; input test aborted')
    check_focus()
    release = None
    try:
        if args.key:
            keys = {'Enter': 13, 'Escape': 27, 'Space': 32, 'Tab': 9, 'Ctrl': 17, 'Shift': 16, 'Up': 38, 'Down': 40, 'Left': 37, 'Right': 39, 'Tilde': 192, 'F3': 114, 'F4': 115}
            vk = ord(args.key.upper()) if len(args.key) == 1 and args.key.isalnum() else keys[args.key]
            flags = 8 | (1 if vk in (37, 38, 39, 40) else 0)
            scan = u.MapVirtualKeyW(vk, 0)
            send(Input(1, Payload(keyboard=Keyboard(0, scan, flags, 0, 0))))
            release = Input(1, Payload(keyboard=Keyboard(0, scan, flags | 2, 0, 0)))
        if args.mouse_button:
            down, up = {'left': (2, 4), 'right': (8, 16), 'middle': (32, 64)}[args.mouse_button]
            send(Input(0, Payload(mouse=Mouse(0, 0, 0, down, 0, 0))))
            release = Input(0, Payload(mouse=Mouse(0, 0, 0, up, 0, 0)))
        steps = max(1, round(args.seconds * 100)); last_x = last_y = 0
        # Individual 10 ms sleeps may round up to the Windows timer quantum.
        # Absolute deadlines prevent that overshoot accumulating into a long
        # held key (and unintended menu auto-repeat) across every sample.
        input_started = time.perf_counter()
        for step in range(1, steps + 1):
            check_focus()
            if args.capture_during and step == max(1, steps // 2):
                current = windows(args.pid)
                if len(current) != 1 or not capture(current[0][0], current[0][2], current[0][3], args.capture_during):
                    raise RuntimeError('Could not capture the target game window')
            if args.move:
                x, y = (round(value * step / steps) for value in args.move)
                send(Input(0, Payload(mouse=Mouse(x - last_x, y - last_y, 0, 1, 0, 0))))
                last_x, last_y = x, y
            remaining = input_started + args.seconds * step / steps - time.perf_counter()
            if remaining > 0:
                time.sleep(remaining)
    finally:
        if release: send(release)
    print(f'Completed input test for game PID {args.pid}')

if __name__ == '__main__': main()
