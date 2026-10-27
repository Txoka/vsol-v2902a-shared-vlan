"""Redirect eight imports in the exact supported owner-supplied adaptor.

No machine instructions are replaced. No original or patched DSO is shipped.
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
    null = elf.null_offset
    dh = elf.dynamic_header
    require(null + 32 <= dh[2] + dh[5] and result[null:null + 32] == bytes(32),
            "Insufficient dynamic segment padding")
    start = elf.file_offset(elf.tag_value(5))
    strings = bytearray(original[start:start + elf.tag_value(10)])
    symbols = elf.file_offset(elf.tag_value(6))
    hash_offset = elf.file_offset(elf.tag_value(4))
    buckets_count, count = elf.unpack("<II", hash_offset)
    gnu_first = elf.unpack("<I", elf.file_offset(elf.tag_value(0x6FFFFEF5)) + 4)[0]
    versions = elf.file_offset(elf.tag_value(0x6FFFFFF0))
    changes = []
    for index in range(count):
        offset = symbols + index * 24
        name_offset = elf.unpack("<I", offset)[0]
        name = strings[name_offset:strings.index(0, name_offset)].decode()
        if name not in IMPORTS:
            continue
        require(elf.unpack("<H", offset + 6)[0] == 0, "Cannot rename a defined export")
        require(index < gnu_first, "Cannot modify a GNU-hashed symbol")
        require(elf.unpack("<H", versions + index * 2)[0] in (0, 1), "Versioned import unsupported")
        replacement = "sp_" + name
        struct.pack_into("<I", result, offset, len(strings))
        strings.extend(replacement.encode() + b"\0")
        changes.append({"index": index, "from": name, "to": replacement})
    require({item["from"] for item in changes} == IMPORTS, "Expected import set not found")
    needed = len(strings)
    strings.extend(b"libshared-vlan.so\0")
    writable = [(offset, header) for offset, header in elf.loads if header[1] & 2]
    require(len(writable) == 1, "Expected one writable segment")
    rw_offset, rw = writable[0]
    require(rw[2] + rw[5] == len(original), "Unexpected writable segment layout")
    append = (rw[2] + rw[6] + 7) & ~7
    result.extend(bytes(append - len(result)))
    result.extend(strings)
    new_address = rw[3] + append - rw[2]
    struct.pack_into("<Q", result, elf.tags[5][0] + 8, new_address)
    struct.pack_into("<Q", result, elf.tags[10][0] + 8, len(strings))
    struct.pack_into("<QQ", result, null, 1, needed)
    new_size = len(result) - rw[2]
    struct.pack_into("<QQ", result, rw_offset + 32, new_size, new_size)
    buckets, chains = [0] * buckets_count, [0] * count
    for index in range(1, count):
        name_offset = struct.unpack_from("<I", result, symbols + index * 24)[0]
        name = strings[name_offset:strings.index(0, name_offset)]
        bucket = elf_hash(name) % buckets_count
        chains[index] = buckets[bucket]
        buckets[bucket] = index
    struct.pack_into("<" + "I" * (buckets_count + count), result, hash_offset + 8,
                     *(buckets + chains))
    require(result[0x42AD8:0xEBE40] == original[0x42AD8:0xEBE40], "Machine code changed")
    require(result[rw[2] + rw[5]:rw[2] + rw[6]] == bytes(rw[6] - rw[5]), "BSS changed")
    return bytes(result), changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    require(args.source.resolve() != args.output.resolve(), "Refusing to overwrite the source DSO")
    result, changes = patch(args.source.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    require(not args.output.exists(), "Output exists; remove it explicitly before rebuilding")
    args.output.write_bytes(result)
    manifest = {"source_sha256": SUPPORTED_SHA256,
                "patched_sha256": hashlib.sha256(result).hexdigest(),
                "redirected_imports": changes, "status": "EXPERIMENTAL_NOT_BOOT_VALIDATED"}
    args.output.with_suffix(args.output.suffix + ".json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
