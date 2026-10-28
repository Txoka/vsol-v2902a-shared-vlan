"""Exact-build, size-neutral dependency shim and import redirection.

The adaptor's libxpon.so dependency becomes libsp.so. The shim itself depends on
the original libxpon.so.1. Only eight undefined names change from pon_ to spn_.
No instructions, exported names, segment sizes or BSS layout are changed.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from elf64 import Elf64, elf_hash, require

SUPPORTED_SHA256 = "05bd0a0b82bdfd5c910878069b8ef68a07ea87b4b591fea6dbcdddc4b5de0726"
IMPORTS = {
    "pon_add_vlan_rule", "pon_del_vlan_rule", "pon_clean_vlan_rule",
    "pon_clean_all_vlan_rule", "pon_set_vlan_tpid", "pon_set_vlan_dscp_map",
    "pon_set_vlan_downstream_mode", "pon_enable_default_vlan_rule",
}


def patch(original):
    require(hashlib.sha256(original).hexdigest() == SUPPORTED_SHA256,
            "Unsupported adaptor SHA-256; no force mode is available")
    elf = Elf64(original)
    result = bytearray(original)
    start, size = elf.file_offset(elf.tag_value(5)), elf.tag_value(10)
    strings = bytearray(original[start:start + size])

    def name_at(offset):
        require(0 <= offset < len(strings), "Invalid string offset")
        return strings[offset:strings.index(0, offset)].decode()

    symbols = elf.file_offset(elf.tag_value(6))
    hash_offset = elf.file_offset(elf.tag_value(4))
    buckets_count, count = elf.unpack("<II", hash_offset)
    gnu_first = elf.unpack("<I", elf.file_offset(elf.tag_value(0x6FFFFEF5)) + 4)[0]
    versions = elf.file_offset(elf.tag_value(0x6FFFFFF0))
    names = [(index, elf.unpack("<I", symbols + index * 24)[0]) for index in range(count)]
    before = {index: name_at(offset) for index, offset in names}
    allowed, changes = set(), []
    for index, offset in names:
        name = before[index]
        if name not in IMPORTS:
            continue
        require(elf.unpack("<H", symbols + index * 24 + 6)[0] == 0, "Cannot rename a defined export")
        require(index < gnu_first, "Cannot modify a GNU-hashed symbol")
        require(elf.unpack("<H", versions + index * 2)[0] in (0, 1), "Versioned import unsupported")
        replacement = "spn_" + name[4:]
        require(len(replacement) == len(name), "Import length changed")
        strings[offset:offset + 3] = b"spn"
        allowed.update(range(start + offset, start + offset + 3))
        changes.append({"index": index, "from": name, "to": replacement})
    require({item["from"] for item in changes} == IMPORTS, "Expected import set not found")
    replacements = {item["index"]: item["to"] for item in changes}
    for index, offset in names:
        require(name_at(offset) == replacements.get(index, before[index]), "Shared symbol string would change")
    needed = []
    for position in range(elf.dynamic_header[2], elf.null_offset, 16):
        tag, value = elf.unpack("<QQ", position)
        if tag == 1:
            needed.append((value, name_at(value)))
    matching = [offset for offset, name in needed if name == "libxpon.so"]
    require(len(matching) == 1, "Expected one libxpon.so dependency")
    offset = matching[0]
    replacement = b"libsp.so\0".ljust(len(b"libxpon.so\0"), b"\0")
    strings[offset:offset + len(replacement)] = replacement
    allowed.update(range(start + offset, start + offset + len(replacement)))
    for index, symbol_offset in names:
        require(name_at(symbol_offset) == replacements.get(index, before[index]), "Dependency overlaps a symbol name")
    for dependency_offset, dependency in needed:
        require(name_at(dependency_offset) == ("libsp.so" if dependency == "libxpon.so" else dependency),
                "Another dependency was changed")
    result[start:start + size] = strings
    buckets, chains = [0] * buckets_count, [0] * count
    for index, offset in names[1:]:
        bucket = elf_hash(name_at(offset).encode()) % buckets_count
        chains[index] = buckets[bucket]
        buckets[bucket] = index
    length = 4 * (buckets_count + count)
    struct.pack_into("<" + "I" * (buckets_count + count), result, hash_offset + 8, *(buckets + chains))
    allowed.update(range(hash_offset + 8, hash_offset + 8 + length))
    require(len(result) == len(original), "Adaptor file size changed")
    require(all(a == b or index in allowed for index, (a, b) in enumerate(zip(original, result))),
            "Unexpected bytes changed outside import strings and SysV hash metadata")
    require(result[0x42AD8:0xEBE40] == original[0x42AD8:0xEBE40], "Machine code changed")
    return bytes(result), changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    require(args.source.resolve() != args.output.resolve(), "Refusing to overwrite the source DSO")
    result, changes = patch(args.source.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        require(args.output.read_bytes() == result, "Output exists with different bytes; refusing to overwrite")
    else:
        args.output.write_bytes(result)
    manifest = {"source_sha256": SUPPORTED_SHA256, "patched_sha256": hashlib.sha256(result).hexdigest(),
                "redirected_imports": changes, "dependency_shim": "libsp.so",
                "adaptor_size_unchanged": True, "load_segments_unchanged": True,
                "status": "EXPERIMENTAL_NOT_BOOT_VALIDATED"}
    args.output.with_suffix(args.output.suffix + ".json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
