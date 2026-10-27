"""Create local linker-readable copies of owner-supplied vendor DSOs.

The vendor removes section headers. Add build-only section metadata without
changing their loadable program bytes, except ELF section-table header fields.
Never install these linker views on the device.
"""
import argparse
import struct
from pathlib import Path
from elf64 import Elf64, require


def link_view(data):
    elf = Elf64(data)
    if elf.unpack("<H", 60)[0]:
        return data
    hash_offset = elf.file_offset(elf.tag_value(4))
    count = elf.unpack("<II", hash_offset)[1]
    require(0 < count < 100000, "Invalid symbol count")
    start = elf.file_offset(elf.tag_value(6))
    symbols = bytearray(data[start:start + count * 24])
    require(len(symbols) == count * 24, "Truncated dynamic symbols")
    for index in range(count):
        section = struct.unpack_from("<H", symbols, index * 24 + 6)[0]
        if 0 < section < 0xFF00:
            struct.pack_into("<H", symbols, index * 24 + 6, 1)
    globals_ = [i for i in range(count) if symbols[i * 24 + 4] >> 4]
    require(bool(globals_), "No global symbols")
    first = globals_[0]
    require(all(symbols[i * 24 + 4] >> 4 for i in range(first, count)), "Unordered local symbols")
    result = bytearray(data)
    symbol_offset = len(result)
    result.extend(symbols)
    names = b"\0.image\0.dynsym\0.dynstr\0.shstrtab\0"
    name_offset = len(result)
    result.extend(names)
    result.extend(bytes((-len(result)) % 8))
    section_offset = len(result)
    sections = [
        bytes(64),
        struct.pack("<IIQQQQIIQQ", 1, 1, 2, 0, 0, symbol_offset, 0, 0, 8, 0),
        struct.pack("<IIQQQQIIQQ", 8, 11, 2, 0, symbol_offset, len(symbols), 3, first, 8, 24),
        struct.pack("<IIQQQQIIQQ", 16, 3, 2, 0, elf.file_offset(elf.tag_value(5)),
                    elf.tag_value(10), 0, 0, 1, 0),
        struct.pack("<IIQQQQIIQQ", 24, 3, 0, 0, name_offset, len(names), 0, 0, 1, 0),
    ]
    result.extend(b"".join(sections))
    struct.pack_into("<Q", result, 40, section_offset)
    struct.pack_into("<HHH", result, 58, 64, 5, 4)
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    require(args.source.resolve() != args.output.resolve(), "Refusing to overwrite the source DSO")
    result = link_view(args.source.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result)


if __name__ == "__main__":
    main()
