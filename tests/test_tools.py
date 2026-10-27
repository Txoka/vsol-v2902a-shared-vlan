import hashlib
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from elf64 import Elf64, elf_hash
from link_view import link_view
from patch_adaptor import patch


def fixture():
    """Generated ELF metadata, not bytes copied from firmware."""
    data = bytearray(0x600)
    data[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", data, 16, 3, 183, 1)
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HHHHHH", data, 52, 64, 56, 2, 64, 0, 0)
    struct.pack_into("<IIQQQQQQ", data, 64, 1, 6, 0, 0, 0, len(data), 0x700, 0x10000)
    struct.pack_into("<IIQQQQQQ", data, 120, 2, 6, 0x100, 0x100, 0x100, 0x80, 0x80, 8)
    for index, item in enumerate([(5, 0x300), (6, 0x380), (4, 0x340), (10, 8), (0, 0)]):
        struct.pack_into("<QQ", data, 0x100 + index * 16, *item)
    data[0x300:0x308] = b"\0sample\0"
    struct.pack_into("<II", data, 0x340, 1, 2)
    struct.pack_into("<IBBHQQ", data, 0x398, 1, 0x12, 0, 5, 0x400, 4)
    return bytes(data)


class ToolsTest(unittest.TestCase):
    def test_reject_unsupported_firmware(self):
        with self.assertRaisesRegex(ValueError, "Unsupported adaptor"):
            patch(fixture())

    def test_bad_elf(self):
        for value in (b"", b"\0" * 64):
            with self.assertRaises(ValueError):
                Elf64(value)

    def test_build_only_section_metadata(self):
        original = fixture()
        result = link_view(original)
        self.assertEqual(original[:40], result[:40])
        self.assertEqual(original[48:58], result[48:58])
        self.assertEqual(original[64:], result[64:len(original)])
        self.assertEqual(struct.unpack_from("<H", result, 60)[0], 5)
        self.assertEqual(link_view(result), result)
        self.assertNotEqual(hashlib.sha256(original).digest(), hashlib.sha256(result).digest())

    def test_hash_reference(self):
        self.assertEqual(elf_hash(b"printf"), 0x077905A6)


if __name__ == "__main__":
    unittest.main()
