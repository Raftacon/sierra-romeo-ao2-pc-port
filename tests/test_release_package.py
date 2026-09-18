import importlib.util
from pathlib import Path
import tempfile
import unittest

from tools.package_source import allowed, collect, ROOT


class ReleaseBoundaryTests(unittest.TestCase):
    def test_excludes_private_and_game_material(self):
        for name in ('assets/default.xex', 'ref/game.iso', 'generated/default/foo.cpp',
                     'src/hud_ink_bounds.h', 'userdata/player/save', '.git/config',
                     'out/build/game.exe', 'artifacts/branding/pc-font.rgba',
                     'resources/retail/logo.png', 'tools/server.key',
                     'release/dist/old.zip', 'src/../../assets/default.xex',
                     'docs/installer.md', 'docs/coverage.md', 'notes/session.md',
                     'tools/session-notes.md', 'src/coverage-notes.txt'):
            with self.subTest(name=name):
                self.assertFalse(allowed(name))

    def test_keeps_build_and_attribution(self):
        for name in ('Setup.cmd', 'tools/setup.py', 'src/vendor/REXGLUE-LICENSE.txt',
                     'resources/input/LICENSE.txt', 'resources/input/keyboard_a_outline.png',
                     'generated/rexglue.cmake', 'release/version.json'):
            self.assertTrue(allowed(name), name)

    def test_actual_export_has_no_game_outputs(self):
        files = collect(ROOT)
        self.assertIn('tools/prepare_hud_ink_bounds.py', files)
        self.assertNotIn('src/hud_ink_bounds.h', files)
        self.assertFalse(any(n.startswith(('docs/', 'artifacts/', 'notes/', 'reports/')) for n in files))
        self.assertIn('tools/coop_directory.py', files)
        self.assertIn('tools/coop_rendezvous.py', files)
        self.assertIn('src/vendor/nlohmann/json.hpp', files)
        self.assertIn('src/vendor/nlohmann/LICENSE.MIT', files)


if __name__ == '__main__':
    unittest.main()
