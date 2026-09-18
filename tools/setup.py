"""Build Sierra Romeo locally from an owned, revision-matched disc image.

No game data, generated game code, or compiled game executable is downloaded.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from disc import Disc
from identify import identify

ROOT = Path(__file__).resolve().parents[1]


def validate_image(iso: Path, expected: dict) -> None:
    # Check the executable BEFORE copying several GB or installing dependencies.
    import tempfile
    scratch = ROOT / 'artifacts/setup-temp'
    scratch.mkdir(parents=True, exist_ok=True)
    with Disc(iso) as disc, tempfile.TemporaryDirectory(prefix='aot-identity-', dir=scratch) as temp:
        entries = disc.entries()
        xex = next((e for e in entries if e.path.casefold() == 'default.xex' and not e.directory), None)
        if xex is None:
            raise ValueError('Image has no root default.xex')
        disc.extract(xex, Path(temp))
        if identify(Path(temp) / xex.path)['sha256'] != expected['sha256']:
            raise ValueError('Unsupported game revision. This build requires the original USA retail release; no title update.')


def run(*args: object) -> None:
    if (ROOT / 'artifacts/setup.cancel').exists():
        raise SystemExit('Setup cancelled at a safe step boundary. Partial outputs retained.')
    command = ' '.join(map(str, args))
    stages = [('tools/disc.py', 'Extracting and verifying your game'),
              ('tools/bootstrap.py', 'Preparing pinned compiler and runtime'),
              ('-m pip', 'Preparing image tools'), ('umodel.exe', 'Preparing local game fonts'),
              ('tools/build.ps1', 'Generating and compiling your PC build'),
              ('tools/extract_icon.py', 'Preparing the game icon')]
    for match, label in stages:
        if match in command:
            print('AOT_STAGE|' + label, flush=True)
            break
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--iso', type=Path, help='Your own legally obtained Xbox 360 Army of Two ISO')
    parser.add_argument('--use-extracted', action='store_true', help='Use existing assets/default.xex and game files')
    parser.add_argument('--check-only', action='store_true', help='Validate identity without installing or building')
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    if args.iso and args.use_extracted:
        parser.error('Choose --iso or --use-extracted')
    if args.jobs < 1 or args.jobs > 64:
        parser.error('--jobs must be 1..64')
    if not args.iso and not args.use_extracted:
        import tkinter as tk
        from tkinter import filedialog, messagebox
        window = tk.Tk()
        window.withdraw()
        messagebox.showinfo('Sierra Romeo setup',
            'Select an ISO dumped from a copy of Army of Two you legally own. '
            'No game is included. Setup builds locally and requires Visual Studio C++ tools, '
            'CMake, Ninja, Git and 7-Zip. Existing saves are preserved.')
        selected = filedialog.askopenfilename(title='Select your Army of Two Xbox 360 ISO', filetypes=[('Xbox disc image', '*.iso')])
        window.destroy()
        if not selected:
            raise SystemExit('Setup cancelled.')
        args.iso = Path(selected)
    expected = json.loads((ROOT / 'config/retail-usa.identity.json').read_text())
    if args.iso:
        args.iso = args.iso.resolve(strict=True)
        validate_image(args.iso, expected)
    elif identify(ROOT / 'assets/default.xex')['sha256'] != expected['sha256']:
        raise ValueError('Unsupported assets/default.xex revision')
    if args.check_only:
        print('Supported retail executable verified. No files installed.')
        return
    if os.name != 'nt':
        raise SystemExit('The complete installer currently supports Windows x64 only.')
    scratch = ROOT / 'artifacts/setup-temp'
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(scratch)
    missing = [name for name in ('git', 'cmake', 'ninja', '7z') if not shutil.which(name)]
    vswhere = Path(os.environ.get('ProgramFiles(x86)', '')) / 'Microsoft Visual Studio/Installer/vswhere.exe'
    if not vswhere.is_file():
        missing.append('Visual Studio 2022 C++ build tools')
    if missing:
        raise SystemExit('Install these prerequisites first: ' + ', '.join(missing))
    if args.iso:
        run(sys.executable, 'tools/disc.py', args.iso, '--manifest', 'artifacts/setup-disc.json', '--extract', 'assets')
    # Dedicated setup environment; no changes to a global Python installation.
    python = ROOT / '.tools/setup-python/Scripts/python.exe'
    if not python.is_file():
        run(sys.executable, '-m', 'venv', ROOT / '.tools/setup-python')
    run(python, '-m', 'pip', '--disable-pip-version-check', 'install', '--no-cache-dir', '--only-binary=:all:',
        '--require-hashes', '-r', ROOT / 'tools/setup-requirements.txt')
    run(python, 'tools/bootstrap.py', '--with-analysis', '--with-source')
    run(ROOT / '.tools/umodel/umodel.exe', '-export', '-groups', '-out=artifacts/font-groups', 'assets/AO2Game/CookedXenon/00_fonts.xxx')
    for script in ('prepare_skip_prompt', 'prepare_menu_prompt', 'prepare_keyboard_glyphs', 'prepare_hud_ink_bounds'):
        run(python, 'tools/' + script + '.py')
    # Put the isolated interpreter first for build/run helpers using `python`.
    os.environ['PATH'] = str(python.parent) + os.pathsep + os.environ['PATH']
    run('powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'tools/build.ps1', '-SpatialUpscaling', '-Jobs', args.jobs)
    # Start only the local loader, dump its title resource and exit before guest
    # execution. This isolated setup profile never touches userdata/player.
    image = ROOT / 'artifacts/setup-loaded-image.bin'
    previous = {name: os.environ.get(name) for name in ('AOT_DUMP_IMAGE', 'AOT_SETUP_ONLY')}
    try:
        os.environ['AOT_DUMP_IMAGE'] = str(image)
        os.environ['AOT_SETUP_ONLY'] = '1'
        run(ROOT / 'out/build/RelWithDebInfo/army_of_two.exe',
            '--game_data_root=' + str(ROOT / 'assets'),
            '--user_data_root=' + str(ROOT / 'artifacts/setup-profile'),
            '--cache_root=' + str(ROOT / 'artifacts/setup-cache'))
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    run(python, 'tools/extract_icon.py', image)
    # Reconfigure to pick up the new local icon resource, then relink.
    run('powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'tools/build.ps1',
        '-SpatialUpscaling', '-SkipCodegen', '-Target', 'army_of_two', '-Jobs', args.jobs)
    print('Sierra Romeo is ready. Open Play.cmd. Saves: userdata/player. Keep this installation private; it now contains game data.')


if __name__ == '__main__':
    main()
