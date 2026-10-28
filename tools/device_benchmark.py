"""Benchmark actual driver operations in empty unused slots, not physical ports."""
import argparse
import base64
import hashlib
import json
import secrets
from pathlib import Path
from device_connection import Device
from elf64 import require
from patch_adaptor import SUPPORTED_SHA256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--build", type=Path, default=Path("build"))
    parser.add_argument("--max-ratio", type=float, default=1.05)
    args = parser.parse_args()
    report = {"status": "RUNNING", "maximum_ratio": args.max_ratio}
    temporary = "/tmp/vsol-benchmark-" + secrets.token_hex(8)
    device = Device(args.host, args.user, args.known_hosts)
    created = False
    try:
        device.connect()
        require(device.run("sha256sum /rom/lib/libapi_omci_adpt.so.1.0.0").decode().split()[0] ==
                SUPPORTED_SHA256, "Unsupported device firmware")
        device.run("mkdir -m 700 " + temporary)
        created = True
        hashes = {}
        for name in ("query-rules", "libshared-vlan-bench.so", "benchmark-real-driver"):
            data = (args.build / name).read_bytes()
            device.upload(temporary + "/" + name, data)
            hashes[name] = hashlib.sha256(data).hexdigest()
        before = base64.b64decode(device.run(temporary + "/query-rules | base64"))
        output = device.run("LD_LIBRARY_PATH=" + temporary + ":/lib:/usr/lib " +
                            temporary + "/benchmark-real-driver", timeout=120)
        after = base64.b64decode(device.run(temporary + "/query-rules | base64"))
        report.update(json.loads(output))
        report["physical_tables_unchanged"] = before == after
        report["binary_sha256"] = hashes
        require(before == after, "Physical port tables changed during benchmark")
        require(report["both_slots_empty_after"], "Test slots were not restored")
        require(report["ratio"] <= args.max_ratio, "Measured overhead exceeds the configured gate")
        report["status"] = "PASS"
        print(json.dumps(report, indent=2), flush=True)
    except Exception as exc:
        report["status"], report["error"] = "FAIL", str(exc)
        raise
    finally:
        (args.build / "device-benchmark-report.json").write_text(json.dumps(report, indent=2) + "\n")
        try:
            if created:
                device.run("rm -f " + temporary + "/*; rmdir " + temporary)
        finally:
            device.close()


if __name__ == "__main__":
    main()
