"""Prepare a local firmware candidate from an owner-supplied stock bank dump.

Preserve kernel/FIP/layout and verify every filesystem entry. Never install.
"""
import argparse
import hashlib
import json
import shlex
import struct
import subprocess
from pathlib import Path
from elf64 import require
from firmware import validate_container, replace_rootfs, ROOTFS
from squashfs import MARKER, split_pseudo, inventory, verify_changes
from device_model import verify_build
from patch_adaptor import SUPPORTED_SHA256


def tool(command, log):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    log.write_bytes(result.stdout)
    require(result.returncode == 0, "Filesystem tool failed; see " + str(log))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock", type=Path, required=True)
    parser.add_argument("--build", type=Path, default=Path("build"))
    parser.add_argument("--unsquashfs", required=True)
    parser.add_argument("--mksquashfs", required=True)
    args = parser.parse_args()
    hashes = verify_build(args.build)
    prepared = json.loads((args.build / "update-manifest.json").read_text())
    require(prepared.get("status") == "NATIVE_OVERLAY_VALIDATED", "Run the native device tests and make update first")
    require(all(prepared.get(key) == value for key, value in hashes.items()), "Native validation is stale")
    image_dir = args.build / "image"
    image_dir.mkdir(mode=0o700, exist_ok=True)
    stock_raw = args.stock.read_bytes()
    stock = validate_container(stock_raw)
    used = struct.unpack_from("<Q", stock, ROOTFS + 40)[0]
    original_root = image_dir / "stock-rootfs.squashfs"
    original_root.write_bytes(stock[ROOTFS:ROOTFS + used])
    original_pseudo = image_dir / "stock-rootfs.pseudo"
    tool([args.unsquashfs, "-pf", str(original_pseudo), str(original_root)], image_dir / "export.log")
    original = original_pseudo.read_bytes()
    entries = inventory(original)
    require(entries["lib/libapi_omci_adpt.so.1.0.0"]["sha256"] == SUPPORTED_SHA256,
            "Stock image contains a different adaptor")
    lines, data = split_pseudo(original)
    rewritten, count = [], 0
    for line in lines:
        if line.startswith("lib/libapi_omci_adpt.so.1.0.0 R "):
            fields = line.split()
            path = str((args.build / "libapi_omci_adpt.so.1.0.0").resolve())
            require("\n" not in path and "\r" not in path, "Unsupported build path")
            line = " ".join([fields[0], "F", *fields[2:6], "cat", shlex.quote(path)])
            count += 1
        rewritten.append(line)
    require(count == 1, "Expected exactly one adaptor file")
    epoch = struct.unpack_from("<I", stock, ROOTFS + 8)[0]
    extension = str((args.build / "libsp.so").resolve())
    require("\n" not in extension and "\r" not in extension, "Unsupported build path")
    rewritten.append(f"lib/libsp.so F {epoch} 755 0 0 cat " + shlex.quote(extension))
    modified = image_dir / "new-rootfs.pseudo"
    modified.write_bytes(("\n".join(rewritten) + "\n").encode() + MARKER + data)
    rootfs = image_dir / "rootfs.squashfs"
    tool([args.mksquashfs, "-", str(rootfs), "-pf", str(modified), "-noappend", "-comp", "xz",
          "-b", "524288", "-no-tailends", "-mkfs-time", str(epoch), "-processors", "4", "-no-progress"],
         image_dir / "build.log")
    verification = image_dir / "verified-rootfs.pseudo"
    tool([args.unsquashfs, "-pf", str(verification), str(rootfs)], image_dir / "verify.log")
    diff = verify_changes(original, verification.read_bytes(), hashes["adaptor_sha256"], hashes["extension_sha256"])
    output_data = replace_rootfs(stock, rootfs.read_bytes())
    output = args.build / "V2902A-241017-shared-vlan-CANDIDATE.bin"
    output.write_bytes(output_data)
    output.chmod(0o600)
    digest = hashlib.sha256(output_data).hexdigest()
    report = {"status": "STRUCTURALLY_VERIFIED_NOT_FLASH_BOOT_TESTED", "sha256": digest,
              "bytes": len(output_data), "stock_bank_sha256": hashlib.sha256(stock_raw).hexdigest(),
              "native_validation": prepared["validation"], **hashes, "filesystem": diff,
              "rootfs_block_size": 524288, "original_rootfs_block_size": 262144,
              "kernel_dtb_fip_layout_preserved": True, "TRX_CRC_and_FIT_kernel_hash_valid": True,
              "firmware_bank_written": False,
              "caveat": "Native overlay reboot was tested; this repacked image has not been flashed or booted."}
    (args.build / "image-manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    output.with_suffix(".bin.sha256").write_text(digest + "  " + output.name + "\n")
    print(json.dumps({"image": str(output), "manifest": report}, indent=2))


if __name__ == "__main__":
    main()
