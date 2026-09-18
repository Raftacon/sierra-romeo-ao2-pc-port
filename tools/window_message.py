"""Send a click or text only to one test process's window message queue.

For host dialogs (such as Xenia's local profile setup), not gameplay input.
Coordinates are client-relative; this never moves the desktop cursor.
"""
import argparse
import ctypes as c
import time
from probe import windows, user

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('pid', type=int)
parser.add_argument('--click', nargs=2, type=int, metavar=('X', 'Y'))
parser.add_argument('--text')
parser.add_argument('--key', type=lambda s: int(s, 0), help='Windows virtual key, e.g. 0xC0 for tilde')
args = parser.parse_args()
matches = windows(args.pid)
if len(matches) != 1:
    parser.error(f'Expected exactly one window; found {len(matches)}')
hwnd = matches[0][0]
user.PostMessageW.argtypes = [c.c_void_p, c.c_uint, c.c_size_t, c.c_ssize_t]
if args.click:
    x, y = args.click
    point = (y << 16) | (x & 0xffff)
    user.PostMessageW(hwnd, 0x200, 0, point)
    user.PostMessageW(hwnd, 0x201, 1, point)
    time.sleep(.15)
    user.PostMessageW(hwnd, 0x202, 0, point)
if args.text:
    for char in args.text:
        user.PostMessageW(hwnd, 0x102, ord(char), 0)
if args.key:
    scan = user.MapVirtualKeyW(args.key, 0)
    user.PostMessageW(hwnd, 0x100, args.key, 1 | scan << 16)
    time.sleep(.1)
    user.PostMessageW(hwnd, 0x101, args.key, 1 | scan << 16 | 3 << 30)
time.sleep(.3)
