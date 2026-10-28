"""Install the validated native overlay with guarded restart/reboot and recovery."""
import argparse
import json
from pathlib import Path
from device_connection import Device
from device_model import ROOT, verify_build, file_hash
from device_test import stage, activate, wait_healthy, reboot, restore, router_health
from package_update import validate_reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--build", type=Path, default=Path("build"))
    parser.add_argument("--router-host", required=True)
    args = parser.parse_args()
    expected = verify_build(args.build)
    validation = validate_reports(
        json.loads((args.build / "device-test-all.json").read_text()),
        json.loads((args.build / "device-benchmark-report.json").read_text()),
        expected, file_hash(args.build / "libshared-vlan-bench.so"), 1.05)
    report = {"status": "RUNNING", "build": expected, "validation": validation,
              "firmware_bank_written": False}
    device = Device(args.host, args.user, args.known_hosts)
    armed = False
    try:
        device.connect()
        report["baseline_router"] = router_health(args.router_host)
        stage(device, args.build, report)
        armed = True
        print("Rollback guard armed; installing validated libraries.", flush=True)
        previous_pid = activate(device, expected)
        report["integration"] = wait_healthy(device, expected, args.router_host, previous_pid=previous_pid)
        print("OMCI integration: PASS; rebooting with recovery armed.", flush=True)
        report["reboot"] = reboot(device, expected, args.router_host)
        # The patch has survived a real boot with its old workaround disabled.
        # Retain recovery files, but remove the now-unneeded temporary service.
        report["router"] = router_health(args.router_host)
        device.run(f"cp /etc/rc.local {ROOT}/installed-rc.local; "
                   f"if [ -f /etc/lan2-vlan20.sh ]; then rm /etc/lan2-vlan20.sh; fi; "
                   f"touch {ROOT}/installed; rm -f {ROOT}/armed; sync")
        device.run("/etc/init.d/vsol-test-rollback stop; "
                   "/etc/init.d/vsol-test-rollback disable; rm /etc/init.d/vsol-test-rollback; sync")
        device.run("test ! -e /etc/init.d/vsol-test-rollback; "
                   "test ! -e /etc/lan2-vlan20.sh")
        armed = False
        report["status"] = "PASS"
        report["recovery_directory"] = ROOT
        print("Installation and reboot: PASS; patch left installed, recovery files retained.", flush=True)
    except Exception as exc:
        report["status"], report["error"] = "FAIL", str(exc)
        try:
            live_armed = device.run(f"if [ -f {ROOT}/armed ]; then echo yes; else echo no; fi") == b"yes"
        except Exception:
            live_armed = False
        if armed or live_armed:
            try:
                report["failure_rollback"] = restore(device, args.router_host)
            except Exception as recovery_error:
                report["rollback_error"] = str(recovery_error)
        raise
    finally:
        (args.build / "device-install.json").write_text(json.dumps(report, indent=2) + "\n")
        device.close()


if __name__ == "__main__":
    main()
