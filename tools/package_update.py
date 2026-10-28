"""Prepare a local, exact-build native update bundle; never install or publish it."""
import argparse
import hashlib
import io
import json
import subprocess
import tarfile
from pathlib import Path
from device_model import verify_build
from patch_adaptor import SUPPORTED_SHA256
from elf64 import require


def validate_reports(device, benchmark, hashes, benchmark_hash, maximum_ratio):
    require(device.get("status") == "PASS" and device.get("phase") == "all", "Full device test is not green")
    require(device.get("build") == hashes, "Device test belongs to a different binary build")
    for phase in ("integration", "reboot", "rollback"):
        require(device.get(phase, {}).get("status") == "PASS", "Missing successful " + phase)
        require(device[phase].get("router", {}).get("status") == "PASS", "Missing WAN health for " + phase)
    require(device["reboot"]["boot_id"] != device.get("baseline_boot_id"), "No real reboot was verified")
    require(benchmark.get("status") == "PASS", "Device benchmark is not green")
    require(benchmark.get("binary_sha256", {}).get("libshared-vlan-bench.so") == benchmark_hash,
            "Benchmark belongs to a different build")
    require(benchmark.get("physical_tables_unchanged") and benchmark.get("both_slots_empty_after"),
            "Benchmark did not verify physical port preservation and cleanup")
    require(benchmark.get("ratio", float("inf")) <= maximum_ratio, "Benchmark exceeds the release gate")
    return {"omci_integration": "PASS", "real_reboot": "PASS", "rollback": "PASS",
            "wan_health": "PASS", "configuration_latency_ratio": benchmark["ratio"],
            "maximum_ratio": maximum_ratio}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=Path("build"))
    parser.add_argument("--require-device-tests", action="store_true")
    parser.add_argument("--max-ratio", type=float, default=1.05)
    args = parser.parse_args()
    hashes = verify_build(args.build)
    validation = None
    if args.require_device_tests:
        device = json.loads((args.build / "device-test-all.json").read_text())
        benchmark = json.loads((args.build / "device-benchmark-report.json").read_text())
        validation = validate_reports(device, benchmark, hashes,
                                      hashlib.sha256((args.build / "libshared-vlan-bench.so").read_bytes()).hexdigest(),
                                      args.max_ratio)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"]))
    manifest = {"model": "V-SOL V2902A-XGS", "vendor_build": "241017",
                "source_adaptor_sha256": SUPPORTED_SHA256, **hashes,
                "source_commit": commit, "source_dirty": dirty,
                "status": "NATIVE_OVERLAY_VALIDATED" if validation else "EXPERIMENTAL_REQUIRE_DEVICE_TESTS",
                "validation": validation,
                "format": "native-library bundle; NOT a sysupgrade image",
                "firmware_bank_write": False}
    content = json.dumps(manifest, indent=2).encode() + b"\n"
    output = args.build / "V2902A-241017-native-update.tar.gz"
    # Fix gzip/tar timestamps for repeatable preparation from identical inputs.
    import gzip
    with output.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode="w") as archive:
        for name in ("libapi_omci_adpt.so.1.0.0", "libsp.so"):
            data = (args.build / name).read_bytes()
            entry = tarfile.TarInfo("lib/" + name)
            entry.size, entry.mode = len(data), 0o755
            archive.addfile(entry, io.BytesIO(data))
        entry = tarfile.TarInfo("manifest.json")
        entry.size, entry.mode = len(content), 0o600
        archive.addfile(entry, io.BytesIO(content))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(digest + "  " + output.name + "\n")
    (args.build / "update-manifest.json").write_bytes(content)
    print(json.dumps({"bundle": str(output), "sha256": digest, "manifest": manifest}, indent=2))


if __name__ == "__main__":
    main()
