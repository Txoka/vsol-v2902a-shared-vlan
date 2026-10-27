"""Small ELF64 reader shared by the local build and exact-build patch tools."""
import struct


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Elf64:
    def __init__(self, data):
        self.data = data
        require(len(data) >= 64, "Truncated ELF header")
        require(data[:7] == b"\x7fELF\x02\x01\x01", "Expected little-endian ELF64")
        require(self.unpack("<H", 18)[0] == 183, "Expected AArch64")
        require(self.unpack("<H", 16)[0] == 3, "Expected a shared ELF object")
        phoff = self.unpack("<Q", 32)[0]
        phsize, phnum = self.unpack("<HH", 54)
        require(phsize == 56 and 0 < phnum < 128, "Invalid program header table")
        self.headers = [(phoff + i * phsize, self.unpack("<IIQQQQQQ", phoff + i * phsize))
                        for i in range(phnum)]
        self.loads = [(offset, header) for offset, header in self.headers if header[0] == 1]
        dynamic = [header for _, header in self.headers if header[0] == 2]
        require(len(dynamic) == 1, "Expected one dynamic segment")
        self.dynamic_header = dynamic[0]
        self.tags = {}
        self.null_offset = None
        for offset in range(dynamic[0][2], dynamic[0][2] + dynamic[0][5], 16):
            tag, value = self.unpack("<QQ", offset)
            if tag == 0:
                self.null_offset = offset
                break
            self.tags[tag] = (offset, value)
        require(self.null_offset is not None, "Missing dynamic terminator")

    def unpack(self, fmt, offset):
        require(0 <= offset <= len(self.data) - struct.calcsize(fmt), "ELF field out of bounds")
        return struct.unpack_from(fmt, self.data, offset)

    def file_offset(self, address):
        for _, header in self.loads:
            if header[3] <= address < header[3] + header[5]:
                return header[2] + address - header[3]
        raise ValueError("Address is not file-backed")

    def tag_value(self, tag):
        require(tag in self.tags, f"Missing ELF dynamic tag {tag}")
        return self.tags[tag][1]


def elf_hash(name):
    value = 0
    for byte in name:
        value = (value << 4) + byte
        high = value & 0xF0000000
        if high:
            value ^= high >> 24
        value &= ~high
    return value & 0xFFFFFFFF
