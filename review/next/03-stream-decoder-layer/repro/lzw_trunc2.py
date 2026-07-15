import io
from archivey.internal.streams.unix_compress import UnixCompressDecompressorStream, LzwState

def encode_literals(payload, max_width=16):
    flag = 0x80 | max_width
    out = bytearray([0x1F,0x9D,flag]); bb=0; bits=0; cw=9
    for b in payload:
        bb |= b << bits; bits += cw
        while bits>=8:
            out.append(bb&0xFF); bb>>=8; bits-=8
    if bits>0: out.append(bb&0xFF)
    return bytes(out)

base = encode_literals(b"HELLO")
# find an appended byte that trips the truncated flag (nonzero leftover < 1 code)
truncated_data = None
for extra in range(1,256):
    cand = base + bytes([extra])
    st = LzwState()
    try:
        st.feed(cand); st.flush()
    except Exception:
        continue
    if st.truncated:
        truncated_data = cand; break
print("found truncated input:", truncated_data is not None, "state.truncated flag set")

if truncated_data:
    print("\n=== read(-1) (readall) path ===")
    s = UnixCompressDecompressorStream(io.BytesIO(truncated_data), seekable=False)
    err=None
    try:
        d = s.read(-1)
        print(f"read(-1) -> {len(d)} bytes, no raise")
        d2 = s.read()
        print(f"next read() -> {len(d2)} bytes, no raise  => TRUNCATION SWALLOWED" )
    except Exception as e:
        print("raised:", type(e).__name__)

    print("\n=== bounded read(n) path (loop to EOF) ===")
    s2 = UnixCompressDecompressorStream(io.BytesIO(truncated_data), seekable=False)
    try:
        while True:
            d = s2.read(4)
            if not d: break
        print("loop ended with NO TruncatedError => swallowed")
    except Exception as e:
        print("raised:", type(e).__name__, "=> truncation surfaced")
