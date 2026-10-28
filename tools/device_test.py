"""Exact-device guarded OMCI/reboot validation; never writes a firmware bank.

The full test deliberately returns to stock at the end. A temporary procd guard
restores stock independently if the host stops sending keepalives for 5 minutes.
"""
import argparse
import base64
import hashlib
import json
import shlex
import subprocess
import time
from pathlib import Path
from device_connection import Device
from device_model import (ROOT, ADAPTOR, EXTENSION, rollback_script, watchdog_script,
                          guard_service, without_old_helper, validate_shared_policy, verify_build,
                          validate_mappings)
from elf64 import require
from patch_adaptor import SUPPORTED_SHA256


def digest(data):
    return hashlib.sha256(data).hexdigest()


def router_health(host):
    if not host:
        return {"status": "NOT_REQUESTED"}
    result = subprocess.run(
        ["ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
         "root@" + host, "ubus call network.interface.wan status"],
        capture_output=True, text=True, timeout=15, check=True)
    state = json.loads(result.stdout)
    require(state.get("up") is True and bool(state.get("ipv4-address")), "Router WAN is not up")
    ping = subprocess.run(
        ["ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
         "root@" + host, "ping -c 2 -W 2 1.1.1.1 >/dev/null"],
        capture_output=True, timeout=15)
    require(ping.returncode == 0, "Router Internet probe failed")
    return {"status": "PASS", "protocol": state.get("proto"), "l3_device": state.get("l3_device")}


def remote_hash(device, path):
    return device.run("sha256sum " + shlex.quote(path)).decode().split()[0]


def rules(device, probe=ROOT + "/query-rules"):
    raw = device.run(shlex.quote(probe) + " | base64")
    return base64.b64decode(raw)


def guard_running(device):
    state = json.loads(device.run("ubus call service list '{\"name\":\"vsol-test-rollback\"}'"))
    instances = state.get("vsol-test-rollback", {}).get("instances", {})
    require(any(item.get("running") for item in instances.values()), "Rollback guard is not running")


def stage(device, build, report):
    require(remote_hash(device, ADAPTOR) == SUPPORTED_SHA256, "Live adaptor is not stock")
    device.run(f"test ! -e {EXTENSION}")
    device.run("test ! -e /etc/init.d/vsol-test-rollback")
    service = json.loads(device.run("ubus call service list '{\"name\":\"pon\"}'"))
    instances = service.get("pon", {}).get("instances", {})
    require(any(item.get("running") and item.get("command") == ["/sbin/airoha_network"]
                for item in instances.values()), "Unexpected PON supervision")
    rc_local = device.download("/etc/rc.local")
    clean_rc = without_old_helper(rc_local)
    # Reuse only a previously verified, completed rollback. Never remove an
    # active session or silently replace its recovery files.
    exists = device.run(f"if [ -d {ROOT} ]; then echo yes; else echo no; fi") == b"yes"
    if exists:
        device.run(f"test -f {ROOT}/rolled-back && test ! -f {ROOT}/armed")
        require(remote_hash(device, ROOT + "/backup/adaptor") == SUPPORTED_SHA256,
                "Existing rollback backup is invalid")
        require(device.download(ROOT + "/backup/rc.local") == rc_local, "Baseline rc.local changed")
    else:
        device.run(f"umask 077; mkdir -p {ROOT}/backup")
        device.run(f"cp -p {ADAPTOR} {ROOT}/backup/adaptor; cp -p /etc/rc.local {ROOT}/backup/rc.local")
        device.run(f"if [ -f /etc/lan2-vlan20.sh ]; then cp -p /etc/lan2-vlan20.sh {ROOT}/backup/; fi")
    require(remote_hash(device, ROOT + "/backup/adaptor") == SUPPORTED_SHA256,
            "Original adaptor backup failed verification")
    device.upload(ROOT + "/rollback.sh", rollback_script().encode())
    device.upload(ROOT + "/watchdog.sh", watchdog_script().encode())
    device.upload("/etc/init.d/vsol-test-rollback", guard_service().encode(), "755")
    device.upload(ROOT + "/query-rules", (build / "query-rules").read_bytes())
    device.upload(ROOT + "/new-adaptor", (build / "libapi_omci_adpt.so.1.0.0").read_bytes())
    device.upload(ROOT + "/new-extension", (build / "libsp.so").read_bytes())
    # Guard is armed before either library or rc.local is changed.
    device.run(f"rm -f {ROOT}/rolled-back {ROOT}/request-rollback {ROOT}/request-reboot; "
               f"touch {ROOT}/armed {ROOT}/keepalive; /etc/init.d/vsol-test-rollback enable; "
               "/etc/init.d/vsol-test-rollback start")
    guard_running(device)
    device.run("test -L /etc/rc.d/S01vsol-test-rollback")
    device.upload(ROOT + "/clean-rc.local", clean_rc, "644")
    report["baseline_rules"] = validate_shared_policy(rules(device))
    report["baseline_boot_id"] = device.run("cat /proc/sys/kernel/random/boot_id").decode()
    report["stage"] = "PASS"


def activate(device, expected):
    device.keepalive()
    device.run(f"cp {ROOT}/new-extension {EXTENSION}.new; chmod 755 {EXTENSION}.new; "
               f"mv {EXTENSION}.new {EXTENSION}; cp {ROOT}/new-adaptor {ADAPTOR}.new; "
               f"chmod 755 {ADAPTOR}.new; mv {ADAPTOR}.new {ADAPTOR}; "
               f"cp {ROOT}/clean-rc.local /etc/rc.local; chmod 664 /etc/rc.local; sync")
    require(remote_hash(device, ADAPTOR) == expected["adaptor_sha256"], "Installed adaptor differs")
    require(remote_hash(device, EXTENSION) == expected["extension_sha256"], "Installed extension differs")
    output = device.run(f"LD_PRELOAD={ADAPTOR} /lib/ld-musl-aarch64.so.1 "
                        "--list /userfs/bin/omci 2>&1")
    require(b"libsp.so" in output, "Target loader did not load the extension")
    previous_pid = device.run("pidof omci").decode()
    device.run("/etc/init.d/pon restart", timeout=30)
    return previous_pid


def wait_healthy(device, expected, router, timeout=120, new_boot=None, previous_pid=None):
    deadline, last_error = time.monotonic() + timeout, None
    while time.monotonic() < deadline:
        try:
            device.keepalive()
            boot = device.run("cat /proc/sys/kernel/random/boot_id").decode()
            if new_boot:
                require(boot != new_boot, "Still on the previous boot")
            require(remote_hash(device, ADAPTOR) == expected["adaptor_sha256"], "Patch rolled back")
            require(remote_hash(device, EXTENSION) == expected["extension_sha256"], "Wrong extension")
            pid = device.run("pidof omci").decode()
            require(len(pid.split()) == 1 and pid != previous_pid, "OMCI restart is not complete")
            maps = device.run('p=$(pidof omci); test -n "$p" && cat /proc/$p/maps')
            validate_mappings(maps)
            rc = device.download("/etc/rc.local")
            require(b"/etc/lan2-vlan20.sh" not in rc, "Old workaround is still enabled")
            shared = validate_shared_policy(rules(device))
            health = router_health(router)
            guard_running(device)
            return {"status": "PASS", "boot_id": boot, "omci_pid": pid, "rules": shared, "router": health}
        except Exception as exc:
            last_error = str(exc)
            time.sleep(3)
            try:
                device.connect()
            except Exception:
                pass
    raise RuntimeError("Native integration did not become healthy: " + str(last_error))


def reboot(device, expected, router):
    previous = device.run("cat /proc/sys/kernel/random/boot_id").decode()
    device.keepalive()
    # The independent guard performs reboot after this SSH command completes.
    device.run(f"touch {ROOT}/request-reboot")
    device.close()
    time.sleep(5)
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        try:
            device.connect()
            return wait_healthy(device, expected, router, timeout=100, new_boot=previous)
        except (TimeoutError, OSError):
            time.sleep(3)
    raise TimeoutError("ONT did not return after reboot; independent rollback remains armed")


def restore(device, router):
    require(remote_hash(device, ROOT + "/backup/adaptor") == SUPPORTED_SHA256, "Invalid rollback backup")
    try:
        guard_running(device)
    except ValueError:
        # Recover even if an earlier attempt failed before the guard started.
        device.upload("/etc/init.d/vsol-test-rollback", guard_service().encode(), "755")
        device.run(f"touch {ROOT}/armed; /etc/init.d/vsol-test-rollback enable; "
                   "/etc/init.d/vsol-test-rollback start")
        guard_running(device)
    device.run(f"test -f {ROOT}/backup/adaptor; touch {ROOT}/request-rollback")
    deadline = time.monotonic() + 220
    while time.monotonic() < deadline:
        try:
            done = device.run(f"if [ -f {ROOT}/rolled-back ]; then echo yes; else echo no; fi")
            if done == b"yes":
                require(remote_hash(device, ADAPTOR) == SUPPORTED_SHA256, "Rollback hash mismatch")
                device.run(f"test ! -e {EXTENSION}; test ! -e /etc/init.d/vsol-test-rollback")
                require(device.download("/etc/rc.local") == device.download(ROOT + "/backup/rc.local"),
                        "rc.local was not restored")
                shared = validate_shared_policy(rules(device))
                try:
                    health = router_health(router)
                except (ValueError, subprocess.SubprocessError):
                    time.sleep(3)
                    continue
                return {"status": "PASS", "router": health, "rules": shared}
        except (OSError, TimeoutError):
            try:
                device.connect()
            except Exception:
                pass
        time.sleep(3)
    raise TimeoutError("Rollback has not completed; preserve the recovery directory")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--build", type=Path, default=Path("build"))
    parser.add_argument("--router-host")
    parser.add_argument("--phase", choices=("all", "integration", "rollback", "preflight"), default="all")
    args = parser.parse_args()
    report = {"status": "RUNNING", "firmware_bank_written": False, "phase": args.phase}
    args.build.mkdir(exist_ok=True)
    report_path = args.build / ("device-test-" + args.phase + ".json")
    expected = verify_build(args.build) if args.phase != "rollback" else {}
    report["build"] = expected
    device = Device(args.host, args.user, args.known_hosts)
    armed = False
    try:
        device.connect()
        if args.phase == "rollback":
            report["rollback"] = restore(device, args.router_host)
        elif args.phase == "preflight":
            import secrets
            probe = "/tmp/vsol-query-" + secrets.token_hex(8)
            require(remote_hash(device, ADAPTOR) == SUPPORTED_SHA256, "Live adaptor is not stock")
            try:
                device.upload(probe, (args.build / "query-rules").read_bytes())
                report["rules"] = validate_shared_policy(rules(device, probe))
                report["router"] = router_health(args.router_host)
                report["pon_supervision"] = json.loads(device.run("ubus call service list '{\"name\":\"pon\"}'"))
                report["boot_id"] = device.run("cat /proc/sys/kernel/random/boot_id").decode()
            finally:
                device.run("rm -f " + probe)
        else:
            report["baseline_router"] = router_health(args.router_host)
            stage(device, args.build, report)
            armed = True
            print("Rollback guard armed; restarting vendor PON/OMCI service.", flush=True)
            previous_pid = activate(device, expected)
            report["integration"] = wait_healthy(device, expected, args.router_host, previous_pid=previous_pid)
            report_path.write_text(json.dumps(report, indent=2) + "\n")
            print("OMCI integration: PASS", flush=True)
            if args.phase == "all":
                print("Running guarded reboot test.", flush=True)
                report["reboot"] = reboot(device, expected, args.router_host)
                print("Reboot persistence: PASS; restoring original device setup.", flush=True)
            report["rollback"] = restore(device, args.router_host)
            armed = False
        report["status"] = "PASS"
        print(json.dumps({"status": "PASS", "report": str(report_path),
                          "device_left_on_stock": True}), flush=True)
    except Exception as exc:
        report["status"], report["error"] = "FAIL", str(exc)
        try:
            live_armed = device.run(f"if [ -f {ROOT}/armed ]; then echo yes; else echo no; fi") == b"yes"
            if armed or live_armed:
                print("Test failed; requesting immediate stock restoration.", flush=True)
                report["failure_rollback"] = restore(device, args.router_host)
        except Exception as recovery_error:
            report["automatic_rollback"] = "Guard remains armed if staging reached the activation step"
            report["rollback_request_error"] = str(recovery_error)
        raise
    finally:
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        device.close()


if __name__ == "__main__":
    main()
