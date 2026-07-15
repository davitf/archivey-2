import io
from archivey.internal.streams.unix_compress import UnixCompressDecompressorStream, LzwState

# Build a small valid .Z then truncate mid-code so leftover bits are nonzero.
def encode_simple(payload, max_width=16):
    # trivial: literals only (no dict refs) -> valid .Z of the payload
    flag = 0x80 | max_width
    out = bytearray([0x1F,0x9D,flag]); bb=0; bits=0; cw=9
    for b in payload:
        bb |= b << bits; bits += cw
        while bits>=8:
            out.append(bb&0xFF); bb>>=8; bits-=8
    if bits>0: out.append(bb&0xFF)
    return bytes(out)

data = encode_simple(b"HELLO WORLD"*4)
# corrupt: append a byte with nonzero high bits after a truncation point
truncated = data + b"\x01"  # stray nonzero leftover bits -> should be TruncatedError

print("=== read(-1) on truncated .Z ===")
s = UnixCompressDecompressorStream(io.BytesIO(truncated), seekable=False)
try:
    d = s.read(-1)   # readall path
    print(f"read(-1) returned {len(d)} bytes, NO error raised (truncation swallowed on this call?)")
    d2 = s.read()    # subsequent read
    print(f"second read() returned {len(d2)} bytes")
except Exception as e:
    print("raised:", type(e).__name__, str(e)[:80])

print("\n=== maxbits>16 acceptance ===")
from archivey.internal.streams.unix_compress import _parse_header
for mw in (16, 17, 24, 31):
    hdr = bytes([0x1F,0x9D, 0x80 | mw])
    try:
        w, bm = _parse_header(hdr)
        print(f"maxbits={mw}: ACCEPTED (max_width={w})")
    except Exception as e:
        print(f"maxbits={mw}: rejected {type(e).__name__}")
