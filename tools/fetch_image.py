"""Read the active stock firmware bank, excluding factory/calibration partitions."""
import argparse
import hashlib
import re
from pathlib import Path
from device_connection import Device
from elf64 import require
from firmware import validate_container
from patch_adaptor import SUPPORTED_SHA256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("vendor/stock-tclinux.bin"))
    args = parser.parse_args()
    require(not args.output.exists(), "Output exists; refusing to overwrite the backup")
    device = Device(args.host, args.user, args.known_hosts)
    try:
        device.connect()
        require(device.run("sha256sum /rom/lib/libapi_omci_adpt.so.1.0.0").decode().split()[0] ==
                SUPPORTED_SHA256, "Unsupported stock build")
        # Boot arguments may contain private settings: extract the bank only,
        # never print or store the complete command line.
        command_line = device.run("cat /proc/cmdline").decode()
        flag = re.search(r"\bbootflag=([01])\b", command_line)
        require(flag is not None, "Cannot determine the active firmware bank")
        bank = int(flag.group(1))
        require(f"root=/dev/mtdblock{3 if bank == 0 else 6}" in command_line, "Inconsistent active root partition")
        source = "/dev/mtd1ro" if bank == 0 else "/dev/mtd4ro"
        expected = device.run("sha256sum " + source).decode().split()[0]
        data = device.download(source)
        require(len(data) == 32 * 1024 * 1024, "Unexpected bank size")
        require(hashlib.sha256(data).hexdigest() == expected, "Bank transfer checksum mismatch")
        validate_container(data)
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with args.output.open("xb") as stream:
            args.output.chmod(0o600)
            stream.write(data)
        print("Read-only backup saved:", args.output, "SHA-256:", expected)
    finally:
        device.close()


if __name__ == "__main__":
    main()
