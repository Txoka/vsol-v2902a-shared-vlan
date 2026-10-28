"""Validate device evidence without requiring an SSH connection."""
import hashlib
import struct
from pathlib import Path
from elf64 import require
from patch_adaptor import SUPPORTED_SHA256

ROOT = "/root/vsol-shared-vlan-test"
ADAPTOR = "/lib/libapi_omci_adpt.so.1.0.0"
EXTENSION = "/lib/libsp.so"


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_mappings(raw):
    lines = raw.decode().splitlines()
    for path in (ADAPTOR, EXTENSION):
        relevant = [line for line in lines if line.rstrip().endswith(path) or
                    line.rstrip().endswith(path + " (deleted)")]
        require(bool(relevant), "OMCI has not mapped " + path)
        require(all("(deleted)" not in line for line in relevant), "Stale mapping: " + path)
    # SYSV/anonymous IPC mappings commonly contain '(deleted)' and do not
    # indicate a stale shared-library mapping.
    return True


def parse_rules(raw):
    records, offset = {}, 0
    while offset < len(raw):
        require(len(raw) - offset >= 8, "Truncated rule header")
        port, count = struct.unpack_from("<Ii", raw, offset)
        offset += 8
        require(port not in records and 0 <= count <= 32, "Invalid port/count in device evidence")
        require(len(raw) - offset >= count * 64, "Truncated rule records")
        records[port] = [raw[offset + i * 64:offset + (i + 1) * 64] for i in range(count)]
        offset += count * 64
    require(set(records) == {11, 12}, "Expected physical LAN1/LAN2 logical ports")
    return records


def semantic_key(row):
    # Padding, generated downstream fields and the derived complex flag are not
    # customer-policy inputs. Match the native engine's comparison convention.
    return row[:16] + row[32:33] + row[34:37] + row[40:60]


def validate_shared_policy(raw):
    records = parse_rules(raw)
    require(bool(records[11]), "Source policy is empty")
    require([semantic_key(row) for row in records[11]] ==
            [semantic_key(row) for row in records[12]], "LAN1/LAN2 policies differ")
    for port in (11, 12):
        require(any(row[32] == 1 and struct.unpack_from("<H", row, 14)[0] == 20 and row[36] == 0
                    for row in records[port]), f"Port {port} lacks VLAN-20 transparency")
    return {str(port): {"count": len(rows), "rules": [row.hex() for row in rows]}
            for port, rows in records.items()}


def rollback_script():
    """Persistent rollback uses stock files; no dependency on the new extension."""
    return f'''#!/bin/sh
set -eu
root={ROOT}
cp "$root/backup/adaptor" {ADAPTOR}.rollback
chmod 755 {ADAPTOR}.rollback
mv {ADAPTOR}.rollback {ADAPTOR}
rm -f {EXTENSION}
cp -p "$root/backup/rc.local" /etc/rc.local
if [ -f "$root/backup/lan2-vlan20.sh" ]; then
    cp -p "$root/backup/lan2-vlan20.sh" /etc/lan2-vlan20.sh
fi
sync
/etc/init.d/pon restart
helper_status=0
if [ -f "$root/backup/lan2-vlan20.sh" ]; then
    /bin/sh /etc/lan2-vlan20.sh --startup >"$root/old-helper-restore.log" 2>&1 || helper_status=$?
fi
echo "$helper_status" >"$root/helper-restore.status"
sha256sum {ADAPTOR} >"$root/restored.sha256"
rm -f "$root/armed"
/etc/init.d/vsol-test-rollback disable
rm -f /etc/init.d/vsol-test-rollback
touch "$root/rolled-back"
'''


def watchdog_script():
    return f'''#!/bin/sh
set -eu
root={ROOT}
echo $$ >"$root/guard.pid"
age=0
while [ -f "$root/armed" ]; do
    if [ -f "$root/request-rollback" ]; then
        rm -f "$root/request-rollback"
        exec /bin/sh "$root/rollback.sh"
    fi
    if [ -f "$root/request-reboot" ]; then
        rm -f "$root/request-reboot"
        sync
        /sbin/reboot
    fi
    if [ -f "$root/keepalive" ]; then
        rm -f "$root/keepalive"
        age=0
    fi
    if [ "$age" -ge 300 ]; then
        exec /bin/sh "$root/rollback.sh"
    fi
    sleep 1
    age=$((age + 1))
done
'''


def guard_service():
    return f'''#!/bin/sh /etc/rc.common
START=01
STOP=99
USE_PROCD=1
start_service() {{
    procd_open_instance
    procd_set_param command /bin/sh {ROOT}/watchdog.sh
    procd_close_instance
}}
'''


def without_old_helper(rc_local):
    lines = rc_local.decode().splitlines()
    allowed = {"", "exit 0", "# Restore the verified LAN2 VLAN-20 exception after vendor OMCI initialisation.",
               "/etc/lan2-vlan20.sh --startup >/tmp/lan2-vlan20-startup.log 2>&1 &"}
    require(all(line.strip() in allowed for line in lines),
            "rc.local contains other commands; this exact-device harness will not rewrite it")
    return b"# Native shared VLAN policy is loaded by OMCI.\nexit 0\n"


def verify_build(build):
    adaptor, extension = build / "libapi_omci_adpt.so.1.0.0", build / "libsp.so"
    require(adaptor.is_file() and extension.is_file(), "Build the local patch first")
    # The generated manifest is evidence, never an authorization to accept a
    # different original build. Source SHA remains pinned in the patch tool.
    import json
    manifest = json.loads(adaptor.with_suffix(adaptor.suffix + ".json").read_text())
    require(manifest["source_sha256"] == SUPPORTED_SHA256, "Unsupported source manifest")
    require(manifest["patched_sha256"] == file_hash(adaptor), "Patched adaptor hash mismatch")
    return {"adaptor_sha256": file_hash(adaptor), "extension_sha256": file_hash(extension)}
