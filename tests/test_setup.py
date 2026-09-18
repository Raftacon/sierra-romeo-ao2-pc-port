import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tests.test_disc import image

# Match the installer CLI's tools import root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from tools import setup


class SetupIdentityTests(unittest.TestCase):
    def test_cancel_stops_before_next_child_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'artifacts').mkdir()
            (root / 'artifacts/setup.cancel').write_text('cancel')
            with patch.object(setup, 'ROOT', root), patch.object(setup.subprocess, 'run') as child:
                with self.assertRaisesRegex(SystemExit, 'cancelled'):
                    setup.run('must-not-run.exe')
                child.assert_not_called()

    def test_supported_image_is_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); iso = root / 'owned.iso'
            original = bytes(image()); iso.write_bytes(original)
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'identify', return_value={'sha256': 'expected'}):
                setup.validate_image(iso, {'sha256': 'expected'})
            self.assertEqual(iso.read_bytes(), original)
            self.assertFalse((root / 'assets').exists())
            self.assertFalse((root / 'userdata').exists())

    def test_wrong_revision_is_rejected_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); iso = root / 'wrong.iso'; iso.write_bytes(image())
            with patch.object(setup, 'ROOT', root), patch.object(setup, 'identify', return_value={'sha256': 'wrong'}):
                with self.assertRaisesRegex(ValueError, 'Unsupported game revision'):
                    setup.validate_image(iso, {'sha256': 'expected'})
            self.assertFalse((root / 'assets').exists())

    def test_missing_executable_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); iso = root / 'wrong.iso'; iso.write_bytes(image(name=b'other.bin'))
            with patch.object(setup, 'ROOT', root), self.assertRaisesRegex(ValueError, 'no root default.xex'):
                setup.validate_image(iso, {'sha256': 'expected'})
