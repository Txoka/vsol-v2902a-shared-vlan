"""Verify size-neutral adaptor layout, shim dependencies and exact ABI aliases."""
import argparse
import json
import struct
from pathlib import Path
from elf64 import Elf64, require
from patch_adaptor import IMPORTS, patch


def dependencies(elf):
    strings = elf.file_offset(elf.tag_value(5))
    result = []
    for offset in range(elf.dynamic_header[2], elf.null_offset, 16):
        tag, value = elf.unpack("<QQ", offset)
        if tag == 1:
            end = elf.data.index(0, strings + value)
            result.append(elf.data[strings + value:end].decode())
    return result


def defined_symbols(elf):
    start = elf.unpack("<Q", 40)[0]
    size, count = elf.unpack("<HH", 58)
    sections = [elf.unpack("<IIQQQQIIQQ", start + i * size) for i in range(count)]
    dynamic = [section for section in sections if section[1] == 11]
    require(len(dynamic) == 1, "Expected one dynamic symbol section in our shim")
    section = dynamic[0]
    strings = sections[section[6]][4]
    result = {}
    for offset in range(section[4], section[4] + section[5], 24):
        name, info, _, index, value, _ = elf.unpack("<IBBHQQ", offset)
        if index:
            end = elf.data.index(0, strings + name)
            result[elf.data[strings + name:end].decode()] = (value, info & 15)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--build", type=Path, default=Path("build"))
    args = parser.parse_args()
    original = args.source.read_bytes()
    patched = (args.build / "libapi_omci_adpt.so.1.0.0").read_bytes()
    extension = (args.build / "libsp.so").read_bytes()
    require(patched == patch(original)[0], "Patched adaptor differs from the exact patch result")
    base, updated, shim = Elf64(original), Elf64(patched), Elf64(extension)
    require(base.headers == updated.headers and len(original) == len(patched), "Adaptor layout expanded")
    require("libsp.so" in dependencies(updated) and "libxpon.so" not in dependencies(updated), "Wrong shim dependency")
    require({"libxpon.so.1", "libc.so"} <= set(dependencies(shim)), "Original vendor dependencies are missing")
    symbols = defined_symbols(shim)
    for name in IMPORTS:
        wrapper, alias = "sp_" + name, "spn_" + name[4:]
        require(symbols.get(wrapper) == symbols.get(alias) and symbols.get(alias, (0, 0))[1] == 2,
                "ABI alias is not the same function: " + alias)
    memory = lambda elf: sum(header[6] for _, header in elf.loads)
    report = {"status": "PASS", "original_adaptor_bytes": len(original), "patched_adaptor_bytes": len(patched),
              "extension_bytes": len(extension), "total_bytes": len(patched) + len(extension),
              "extra_file_bytes": len(patched) + len(extension) - len(original),
              "extra_segment_memory_bytes": memory(updated) + memory(shim) - memory(base),
              "adaptor_segments_unchanged": True, "all_eight_aliases_share_function_addresses": True}
    (args.build / "binary-check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
