"""Read three stock libraries from an owner-operated ONT; never change it.

An interactive channel supports vendor SSH servers that force a shell instead
of executing SCP/SFTP/exec requests. Verify and trust the host key beforehand.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from elf64 import Elf64, require
from patch_adaptor import SUPPORTED_SHA256

FILES = ("libxpon.so.1", "libc.so", "libapi_omci_adpt.so.1.0.0")


class Shell:
    def __init__(self, channel):
        self.channel = channel

    def run(self, command):
        marker = "COPY_" + secrets.token_hex(16)
        self.channel.sendall(
            f"printf '\\n{marker}_BEGIN\\n'; {command}; printf '\\n{marker}_END\\n'\n"
        )
        begin = re.compile(rb"(?m)^" + (marker + "_BEGIN").encode() + rb"\r?$")
        end = re.compile(rb"(?m)^" + (marker + "_END").encode() + rb"\r?$")
        received = bytearray()
        deadline = time.monotonic() + 90
        while not end.search(received):
            if self.channel.recv_ready():
                chunk = self.channel.recv(262144)
                require(bool(chunk), "SSH connection closed during transfer")
                received.extend(chunk)
                require(len(received) <= 16 * 1024 * 1024, "Unexpectedly large response")
            elif self.channel.closed or time.monotonic() > deadline:
                raise TimeoutError("SSH transfer did not complete")
            else:
                time.sleep(0.01)
        first, last = begin.search(received), end.search(received)
        require(first is not None, "Transfer framing was not found")
        return bytes(received[first.end():last.start()]).strip(b"\r\n")


def fetch(client):
    result = {}
    with client.invoke_shell(width=4096, height=24) as channel:
        shell = Shell(channel)
        for name in FILES:
            source = "/rom/lib/" + name
            remote_hash = shell.run("sha256sum " + source).decode().split()[0]
            require(re.fullmatch(r"[0-9a-f]{64}", remote_hash), "Remote checksum failed")
            encoded = shell.run("base64 " + source)
            data = base64.b64decode(re.sub(rb"\s+", b"", encoded), validate=True)
            require(hashlib.sha256(data).hexdigest() == remote_hash, "Transfer checksum mismatch")
            Elf64(data)
            if name == "libapi_omci_adpt.so.1.0.0":
                require(remote_hash == SUPPORTED_SHA256, "ONT adaptor is not the supported build")
            result[name] = data
            print("Verified", name, remote_hash, flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="admin")
    parser.add_argument("--known-hosts", type=Path, default=Path.home() / ".ssh/known_hosts")
    parser.add_argument("--output", type=Path, default=Path("vendor"))
    args = parser.parse_args()
    try:
        import paramiko
    except ImportError:
        parser.error("Install optional dependency: python -m pip install paramiko==5.1.0")
    require(args.known_hosts.is_file(), "Verify the ONT host key using SSH first")
    for name in (*FILES, "checksums.json"):
        require(not (args.output / name).exists(), "Output exists; refusing to overwrite " + name)
    with paramiko.SSHClient() as client:
        client.load_host_keys(str(args.known_hosts))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(args.host, username=args.user, password=getpass.getpass("ONT password: "),
                       look_for_keys=False, allow_agent=False, timeout=15, auth_timeout=15)
        data = fetch(client)
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    checksums = {}
    for name, content in data.items():
        target = args.output / name
        with target.open("xb") as stream:
            os.chmod(target, 0o600)
            stream.write(content)
        checksums[name] = hashlib.sha256(content).hexdigest()
    with (args.output / "checksums.json").open("x") as stream:
        json.dump(checksums, stream, indent=2)
        stream.write("\n")
    print("Stock libraries copied locally. No ONT files or settings were modified.")


if __name__ == "__main__":
    main()
