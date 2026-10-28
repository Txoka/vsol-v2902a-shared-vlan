"""Prompted, host-key-pinned device connection and bounded shell transfers."""
import base64
import getpass
import hashlib
import re
import secrets
import shlex
import threading
import time
from elf64 import require


class Device:
    def __init__(self, host, user, known_hosts):
        import paramiko
        self.paramiko = paramiko
        self.host, self.user, self.known_hosts = host, user, known_hosts
        self.password = getpass.getpass("ONT password: ")
        self.client = self.channel = None

    def close(self):
        if self.channel:
            self.channel.close()
        if self.client:
            self.client.close()
        self.client = self.channel = None

    def connect(self):
        self.close()
        self.client = self.paramiko.SSHClient()
        self.client.load_host_keys(str(self.known_hosts))
        self.client.set_missing_host_key_policy(self.paramiko.RejectPolicy())
        self.client.connect(self.host, username=self.user, password=self.password,
                            look_for_keys=False, allow_agent=False, timeout=10, auth_timeout=10)
        self.channel = self.client.invoke_shell(width=4096, height=24)

    def run(self, command, timeout=90, check=True):
        marker = "TEST_" + secrets.token_hex(16)
        payload = (f"printf '\\n{marker}_BEGIN\\n'; (set -e\n{command}\n); "
                   f"_vsol_status=$?; printf '\\n{marker}_STATUS:%s\\n' \"$_vsol_status\"; "
                   f"printf '\\n{marker}_END\\n'\n")
        errors = []

        def send():
            try:
                self.channel.sendall(payload)
            except Exception as exc:
                errors.append(str(exc))

        writer = threading.Thread(target=send, daemon=True)
        writer.start()
        begin = re.compile(rb"(?m)^" + (marker + "_BEGIN").encode() + rb"\r?$")
        end = re.compile(rb"(?m)^" + (marker + "_END").encode() + rb"\r?$")
        status = re.compile(rb"(?m)^" + (marker + "_STATUS:").encode() + rb"(\d+)\r?$")
        raw = bytearray()
        deadline = time.monotonic() + timeout
        while not end.search(raw):
            if self.channel.recv_ready():
                part = self.channel.recv(262144)
                require(bool(part), "Device connection closed")
                raw.extend(part)
                require(len(raw) <= 64 * 1024 * 1024, "Device response too large")
            elif self.channel.closed or time.monotonic() >= deadline:
                raise TimeoutError("Device command did not complete")
            else:
                time.sleep(0.01)
        writer.join(1)
        require(not errors, "Device send failed")
        first, last = begin.search(raw), status.search(raw)
        require(first is not None and last is not None, "Invalid command framing")
        result = bytes(raw[first.end():last.start()]).strip(b"\r\n")
        code = int(last.group(1))
        if check:
            require(code == 0, f"Device command failed (exit {code}): {result[-1000:]!r}")
        return result

    def upload(self, path, data, mode="700"):
        require(re.fullmatch(r"[0-7]{3}", mode), "Invalid upload mode")
        marker = "PAYLOAD_" + secrets.token_hex(16)
        quoted = shlex.quote(path)
        encoded = base64.encodebytes(data).decode()
        output = self.run(f"base64 -d > {quoted} <<'{marker}'\n{encoded}{marker}\n"
                          f"chmod {mode} {quoted}\nsha256sum {quoted}")
        hashes = re.findall(rb"(?m)^([a-f0-9]{64})  " + re.escape(path.encode()) + rb"\r?$", output)
        require(bool(hashes) and hashes[-1].decode() == hashlib.sha256(data).hexdigest(),
                "Uploaded file checksum mismatch")

    def download(self, path):
        encoded = self.run("base64 " + shlex.quote(path))
        return base64.b64decode(re.sub(rb"\s+", b"", encoded), validate=True)

    def keepalive(self):
        from device_model import ROOT
        self.run(f"test -f {ROOT}/armed && touch {ROOT}/keepalive")
