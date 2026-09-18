import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('local_profile', Path(__file__).resolve().parents[1] / 'tools/local_profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


class LocalProfileTests(unittest.TestCase):
    def test_migration_preserves_complete_profile_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, destination = root / 'old', root / 'player'
            for name in ('title/profile/User/settings', 'xuid/title/Headers/checkpoint', 'xuid/title/checkpoint/Use'):
                file = source / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(name.encode())
            self.assertEqual(profile.prepare(destination, source), 'copied')
            for file in source.rglob('*'):
                if file.is_file():
                    self.assertEqual(file.read_bytes(), (destination / file.relative_to(source)).read_bytes())
            (destination / 'xuid/title/checkpoint/Use').write_bytes(b'new player progress')
            self.assertEqual(profile.prepare(destination, source), 'existing')
            self.assertEqual((destination / 'xuid/title/checkpoint/Use').read_bytes(), b'new player progress')
            self.assertNotEqual((source / 'xuid/title/checkpoint/Use').read_bytes(), b'new player progress')

    def test_missing_source_creates_fresh_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / 'player'
            self.assertEqual(profile.prepare(destination, Path(temp) / 'missing'), 'created')
            self.assertTrue(destination.is_dir())

    def test_nested_copy_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                profile.prepare(Path(temp) / 'recursive', Path(temp))
