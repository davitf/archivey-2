import io, struct, zlib
from archivey.internal.streams.xz import (
    _encode_mbi, _round_up_4, _XZ_STREAM_MAGIC, _XZ_FOOTER_MAGIC,
    _read_xz_index_backwards,
)

CHECK = 0x00  # check "None": no per-block check field

def stream_header(check):
    flags = bytes([0x00, check])
    crc = zlib.crc32(flags) & 0xFFFFFFFF
    return _XZ_STREAM_MAGIC + flags + struct.pack("<I", crc)

def build_index(records):
    body = b"\x00" + _encode_mbi(len(records))
    for unpadded, uncomp in records:
        body += _encode_mbi(unpadded) + _encode_mbi(uncomp)
    body += b"\x00" * (_round_up_4(len(body)) - len(body))
    crc = zlib.crc32(body) & 0xFFFFFFFF
    return body + struct.pack("<I", crc)

def footer(index_bytes, check):
    backward_raw = (len(index_bytes)//4) - 1
    flags = bytes([0x00, check])
    fbody = struct.pack("<I", backward_raw) + flags
    fcrc = zlib.crc32(fbody) & 0xFFFFFFFF
    return struct.pack("<I", fcrc) + fbody + _XZ_FOOTER_MAGIC

# Three "blocks": first has uncompressed_size 100, next two are zero-size.
records = [(10, 100), (10, 0), (10, 0)]
blocks_total = sum(_round_up_4(u) for u, _ in records)   # 12*3 = 36
block_payload = b"\x00" * blocks_total                    # never decoded by the scan

idx = build_index(records)
ft = footer(idx, CHECK)
data = stream_header(CHECK) + block_payload + idx + ft

print("file size", len(data))
bounds = _read_xz_index_backwards(io.BytesIO(data), len(data))
for b in bounds:
    print("block: decompressed_start", b.decompressed_start, "compressed_start", b.compressed_start, "uncomp", b.uncompressed_size)

open("evil_zero_blocks.xz","wb").write(data)
