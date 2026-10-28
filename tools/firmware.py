"""Fixed-layout V2902A container checks. No signing or signature bypass."""
import hashlib
import struct
import zlib
from elf64 import require

TRX = 0x2000
ROOTFS = 0x3261C8
ROOTFS_BUDGET = 0x8A0000


def fit_properties(image):
    base = TRX + 256
    require(image[base:base + 4] == b"\xd0\x0d\xfe\xed", "Missing FIT image")
    header = struct.unpack_from(">10I", image, base)
    require(base + header[1] == len(image), "Unexpected FIT length")
    position, limit, strings = base + header[2], base + header[2] + header[9], base + header[3]
    stack, props = [], {}
    while position < limit:
        token = struct.unpack_from(">I", image, position)[0]
        position += 4
        if token == 1:
            end = image.index(0, position, limit)
            stack.append(image[position:end].decode())
            position = (end + 4) & ~3
        elif token == 2:
            require(bool(stack), "Unbalanced FIT nodes")
            stack.pop()
        elif token == 3:
            size, name_offset = struct.unpack_from(">II", image, position)
            position += 8
            require(name_offset < header[8] and position + size <= limit, "Invalid FIT property")
            end = image.index(0, strings + name_offset, strings + header[8])
            name = image[strings + name_offset:end].decode()
            key = ("/".join(stack), name)
            require(key not in props, "Duplicate FIT property")
            props[key] = (position, size)
            position = (position + size + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            require(not stack, "Unclosed FIT node")
            return props
        else:
            raise ValueError("Unknown FIT token")
    raise ValueError("FIT end marker missing")


def validate_container(raw):
    require(len(raw) > ROOTFS + ROOTFS_BUDGET, "Firmware input is too small")
    require(raw[TRX:TRX + 4] == b"HDR2", "Unsupported TRX/FIP layout")
    require(b"V2902A" in raw[TRX + 0x5C:TRX + 0x7C], "Unexpected product")
    length = struct.unpack_from("<I", raw, TRX + 8)[0]
    require(TRX + length <= len(raw), "Truncated TRX body")
    image = raw[:TRX + length]
    expected_crc = struct.unpack_from("<I", image, TRX + 12)[0]
    require(zlib.crc32(image[TRX + 256:]) ^ 0xFFFFFFFF == expected_crc, "Invalid TRX CRC")
    props = fit_properties(image)
    require(props[("/images/filesystem@1", "data")] == (ROOTFS, ROOTFS_BUDGET), "Unexpected rootfs layout")
    require(image[ROOTFS:ROOTFS + 4] == b"hsqs", "Expected SquashFS rootfs")
    require(not any("signature" in path or path.startswith("/images/filesystem@1/hash")
                    for path, _ in props), "Authenticated filesystem layout unsupported")
    kernel_offset, kernel_size = props[("/images/kernel@1", "data")]
    hash_offset, hash_size = props[("/images/kernel@1/hash@1", "value")]
    require(hash_size == 20 and hashlib.sha1(image[kernel_offset:kernel_offset + kernel_size]).digest() ==
            image[hash_offset:hash_offset + hash_size], "Invalid FIT kernel hash")
    return image


def replace_rootfs(original, rootfs):
    source = validate_container(original)
    require(rootfs[:4] == b"hsqs" and len(rootfs) <= ROOTFS_BUDGET, "Rootfs exceeds original layout")
    output = bytearray(source)
    output[ROOTFS:ROOTFS + ROOTFS_BUDGET] = rootfs + b"\xff" * (ROOTFS_BUDGET - len(rootfs))
    struct.pack_into("<I", output, TRX + 12, zlib.crc32(output[TRX + 256:]) ^ 0xFFFFFFFF)
    result = bytes(output)
    validate_container(result)
    require(result[:TRX] == source[:TRX], "FIP wrapper changed")
    require(result[TRX:TRX + 12] == source[TRX:TRX + 12] and
            result[TRX + 16:ROOTFS] == source[TRX + 16:ROOTFS], "Header, kernel or DTB changed")
    require(result[ROOTFS + ROOTFS_BUDGET:] == source[ROOTFS + ROOTFS_BUDGET:], "FIT trailer changed")
    return result
