import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from device_model import (parse_rules, validate_shared_policy, without_old_helper, watchdog_script,
                          guard_service, validate_mappings, ADAPTOR, EXTENSION)


def evidence(rows):
    return b"".join(struct.pack("<Ii", port, len(values)) + b"".join(values)
                    for port, values in rows.items())


def vlan20():
    row = bytearray(64)
    row[32] = 1
    struct.pack_into("<H", row, 14, 20)
    return row


class EvidenceTest(unittest.TestCase):
    def test_ipc_deleted_mapping_is_not_a_stale_library(self):
        current = f"0000-1000 r-xp 0 0 0 {ADAPTOR}\n1000-2000 r-xp 0 0 0 {EXTENSION}\n"
        self.assertTrue(validate_mappings((current + "2000-3000 rw-s 0 0 0 /SYSV00000000 (deleted)\n").encode()))
        with self.assertRaises(ValueError):
            validate_mappings(current.replace(EXTENSION, EXTENSION + " (deleted)").encode())

    def test_compare_policy_not_padding(self):
        source, target = vlan20(), vlan20()
        target[16:32] = bytes([8] * 16)
        target[33] = 0xFF
        target[60] = 1
        self.assertEqual(validate_shared_policy(evidence({11: [source], 12: [target]}))["12"]["count"], 1)

    def test_reject_different_policy(self):
        source, target = vlan20(), vlan20()
        target[14] = 21
        with self.assertRaises(ValueError):
            validate_shared_policy(evidence({11: [source], 12: [target]}))

    def test_reject_bad_or_incomplete_evidence(self):
        for data in (b"", b"bad", struct.pack("<Ii", 11, -1),
                     struct.pack("<Ii", 11, 33), struct.pack("<Ii", 11, 1)):
            with self.assertRaises(ValueError):
                parse_rules(data)

    def test_do_not_rewrite_unrelated_startup(self):
        with self.assertRaises(ValueError):
            without_old_helper(b"/usr/bin/my-startup\nexit 0\n")
        self.assertNotIn(b"lan2-vlan20.sh", without_old_helper(b"exit 0\n"))

    def test_independent_early_boot_guard(self):
        self.assertIn("START=01", guard_service())
        self.assertIn('"$age" -ge 300', watchdog_script())
        self.assertIn("request-rollback", watchdog_script())


if __name__ == "__main__":
    unittest.main()
