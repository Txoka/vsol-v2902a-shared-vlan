import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from package_update import validate_reports


class ReleaseGateTest(unittest.TestCase):
    def setUp(self):
        self.hashes = {"adaptor_sha256": "adaptor", "extension_sha256": "extension"}
        phase = {"status": "PASS", "router": {"status": "PASS"}, "boot_id": "new"}
        self.device = {"status": "PASS", "phase": "all", "build": self.hashes,
                       "baseline_boot_id": "old", **{name: copy.deepcopy(phase)
                       for name in ("integration", "reboot", "rollback")}}
        self.benchmark = {"status": "PASS", "binary_sha256": {"libshared-vlan-bench.so": "bench"},
                          "physical_tables_unchanged": True, "both_slots_empty_after": True, "ratio": 1.01}

    def test_accept_matching_evidence(self):
        result = validate_reports(self.device, self.benchmark, self.hashes, "bench", 1.05)
        self.assertEqual(result["real_reboot"], "PASS")

    def test_reject_old_binary(self):
        with self.assertRaises(ValueError):
            validate_reports(self.device, self.benchmark, {"different": "build"}, "bench", 1.05)

    def test_reject_fake_reboot(self):
        self.device["reboot"]["boot_id"] = "old"
        with self.assertRaises(ValueError):
            validate_reports(self.device, self.benchmark, self.hashes, "bench", 1.05)

    def test_reject_failed_or_slow_benchmark(self):
        self.benchmark["ratio"] = 1.07
        with self.assertRaises(ValueError):
            validate_reports(self.device, self.benchmark, self.hashes, "bench", 1.05)


if __name__ == "__main__":
    unittest.main()
