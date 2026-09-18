"""Distribution invariants; no dependency installer or game is run."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import unittest
from urllib.parse import urlparse

from tools.package_source import ROOT, allowed, collect


class InstallerReleaseTests(unittest.TestCase):
    def test_prerequisite_lock_has_exact_https_sources_and_hashes(self):
        entries = json.loads((ROOT / 'release/gui/prerequisites.json').read_text(encoding='utf-8-sig'))['windows_x64']
        self.assertEqual({e['name'] for e in entries}, {'python', 'git', 'cmake', 'ninja', '7z', 'vs', 'vcredist'})
        for entry in entries:
            with self.subTest(tool=entry['name']):
                self.assertEqual(urlparse(entry['url']).scheme, 'https')
                self.assertRegex(entry['sha256'], r'^[a-f0-9]{64}$')
                self.assertRegex(entry['version'], r'^\d+(\.\d+)+$')
                self.assertNotIn('/latest/', entry['url'])

    def test_alpha_release_and_corresponding_installer_source(self):
        files = collect(ROOT)
        version = json.loads(files['release/version.json'])['version']
        self.assertRegex(version, r'^0\.\d+\.\d+-alpha\.\d+$')
        for name in ('release/gui/Installer.cs', 'release/gui/install.ps1',
                     'release/gui/prerequisites.json', 'tools/setup-requirements.txt',
                     'tools/build_gui_installer.ps1', '.gitattributes'):
            self.assertIn(name, files)
        for name in ('launch-preferences.json', 'tools/cleanup-obsolete-recordings.ps1',
                     'tools/build_source_installer.ps1', 'release/install.ps1',
                     'release/gui/private.pfx', 'release/gui/compiled.exe'):
            self.assertFalse(allowed(name), name)

    def test_python_dependency_requires_hashes(self):
        text = (ROOT / 'tools/setup-requirements.txt').read_text()
        self.assertTrue(text.startswith('pillow==11.3.0 '))
        self.assertGreaterEqual(len(re.findall(r'--hash=sha256:[a-f0-9]{64}', text)), 4)

    def test_git_ignores_private_outputs_not_release_source(self):
        private = ['assets/default.xex', 'userdata/player/save', 'ref/disc.iso',
                   'artifacts/installer.log', '.tools/compiler.exe', 'src/hud_ink_bounds.h',
                   'launch-preferences.json', 'release/dist/setup.exe', 'docs/installer.md', 'notes/session.md', 'server.pfx',
                   '.env.local', 'capture.rdc', 'capture.mp4', 'game.xxx']
        result = subprocess.run(['git', 'check-ignore', '--no-index', '--stdin'],
                                input=('\n'.join(private)+'\n').encode(),
                                capture_output=True, cwd=ROOT)
        self.assertEqual(set(result.stdout.decode().splitlines()), set(private))
        public = ['release/gui/Installer.cs', 'resources/branding/sierra_romeo.png',
                  'resources/demos/hold-b-to-skip.gif', 'generated/rexglue.cmake']
        result = subprocess.run(['git', 'check-ignore', '-v', '--no-index', '--stdin'],
                                input=('\n'.join(public)+'\n').encode(),
                                capture_output=True, cwd=ROOT)
        # Older Git emits matching negation rules too; those explicitly KEEP files.
        for line in result.stdout.decode().splitlines():
            self.assertTrue(line.split('\t', 1)[0].split(':', 2)[2].startswith('!'), line)


if __name__ == '__main__':
    unittest.main()
