"""Read the metadata-preserving pseudo-file format used by SquashFS tools."""
import hashlib
import shlex
from elf64 import require

MARKER = b"#\n# START OF DATA - DO NOT MODIFY\n#\n"


def split_pseudo(raw):
    require(raw.count(MARKER) >= 1, "Unsupported SquashFS pseudo-file format")
    header, data = raw.split(MARKER, 1)
    return header.decode().splitlines(), data


def inventory(raw):
    header, data = split_pseudo(raw)
    result = {}
    for line in header:
        if not line or line.startswith("#"):
            continue
        fields = shlex.split(line)
        require(fields[0] not in result, "Duplicate filesystem path")
        if fields[1] == "R":
            require(len(fields) == 9, "Unsupported regular-file record")
            size, offset = int(fields[6]), int(fields[7])
            require(size >= 0 and offset >= 0 and offset + size <= len(data), "Truncated file data")
            result[fields[0]] = {"metadata": fields[1:6] + fields[8:], "size": size,
                                 "sha256": hashlib.sha256(data[offset:offset + size]).hexdigest()}
        else:
            result[fields[0]] = {"metadata": fields[1:]}
    return result


def verify_changes(original, updated, adaptor_hash, extension_hash):
    before, after = inventory(original), inventory(updated)
    adaptor, extension = "lib/libapi_omci_adpt.so.1.0.0", "lib/libsp.so"
    require(set(after) - set(before) == {extension}, "Unexpected files added")
    require(not set(before) - set(after), "Files were removed")
    changed = [name for name in before if before[name] != after[name]]
    require(changed == [adaptor], "Unexpected filesystem changes: " + repr(changed))
    require(before[adaptor]["metadata"] == after[adaptor]["metadata"], "Adaptor metadata changed")
    require(after[adaptor]["sha256"] == adaptor_hash, "Wrong patched adaptor content")
    require(after[extension]["sha256"] == extension_hash, "Wrong extension content")
    return {"original_entries": len(before), "updated_entries": len(after),
            "changed": changed, "added": [extension], "all_other_entries_identical": True}
