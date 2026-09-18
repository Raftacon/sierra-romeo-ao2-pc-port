import struct
import tempfile
import unittest
from pathlib import Path

from tools.disc import Disc, MAGIC, SECTOR


def image(name=b'default.xex', left=0, sector=34, length=4):
    data = bytearray(36 * SECTOR)
    base = 32 * SECTOR
    data[base:base + 20] = MAGIC
    data[base + SECTOR - 20:base + SECTOR] = MAGIC
    struct.pack_into('<II', data, base + 20, 33, SECTOR)
    struct.pack_into('<HHIIBB', data, 33 * SECTOR, left, 0, sector, length, 0, len(name))
    data[33 * SECTOR + 14:33 * SECTOR + 14 + len(name)] = name
    data[34 * SECTOR:34 * SECTOR + 4] = b'XEX2'
    return data


class DiscTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.iso = self.root / 'input.iso'

    def load(self, data):
        self.iso.write_bytes(data)
        return Disc(self.iso)

    def test_extract_bytes_and_verify_existing(self):
        with self.load(image()) as disc:
            entry, = disc.entries()
            digest = disc.extract(entry, self.root / 'output')
            self.assertEqual((self.root / 'output/default.xex').read_bytes(), b'XEX2')
            self.assertEqual(disc.extract(entry, self.root / 'output'), digest)
            (self.root / 'output/default.xex').write_bytes(b'FAIL')
            with self.assertRaisesRegex(ValueError, 'differs'):
                disc.extract(entry, self.root / 'output')

    def test_reject_missing_header(self):
        with self.assertRaisesRegex(ValueError, 'partition'):
            self.load(bytearray(36 * SECTOR))

    def test_reject_path_traversal(self):
        for name in (b'../outside', b'..', b'C:bad', b'CON.txt', b'name.'):
            with self.subTest(name=name), self.load(image(name)) as disc:
                with self.assertRaises(ValueError):
                    disc.entries()

    def test_reject_out_of_bounds_extent(self):
        with self.load(image(sector=36, length=1)) as disc:
            with self.assertRaisesRegex(ValueError, 'outside image'):
                disc.entries()

    def test_reject_tree_cycle(self):
        data = image(left=8)
        struct.pack_into('<HHIIBB', data, 33 * SECTOR + 32, 8, 0, 34, 4, 0, 1)
        data[33 * SECTOR + 46] = ord('b')
        with self.load(data) as disc:
            with self.assertRaisesRegex(ValueError, 'Cyclic'):
                disc.entries()

    def test_partition_offset(self):
        # Supported image layout with a nonzero game partition.
        with self.load(bytearray(0xFB20) + image()) as disc:
            self.assertEqual(disc.partition, 0xFB20)
            self.assertEqual(disc.read(disc.entries()[0].offset, 4), b'XEX2')

    def test_subdirectory(self):
        data = image(b'folder', sector=35, length=SECTOR)
        data[33 * SECTOR + 12] = 0x10
        struct.pack_into('<HHIIBB', data, 35 * SECTOR, 0, 0, 34, 4, 0, 5)
        data[35 * SECTOR + 14:35 * SECTOR + 19] = b'a.xex'
        with self.load(data) as disc:
            entries = disc.entries()
            self.assertEqual([e.path for e in entries], ['folder', 'folder/a.xex'])
            self.assertTrue(entries[0].directory)


if __name__ == '__main__':
    unittest.main()
