from archivey.internal.streams.unix_compress import LzwState

def encode_growth(byte_val, n_codes, max_width):
    flag = 0x80 | (max_width & 0x1F)
    out = bytearray([0x1F, 0x9D, flag])
    bit_buffer = 0; bits_in = 0
    code_width = 9
    codes_in_era = 0
    codes_per_era = 1 << (code_width - 1)
    starting_code = 257
    next_code = starting_code
    prev_none = True

    def put(code):
        nonlocal bit_buffer, bits_in
        bit_buffer |= code << bits_in
        bits_in += code_width
        while bits_in >= 8:
            out.append(bit_buffer & 0xFF)
            bit_buffer >>= 8; bits_in -= 8

    def after_code():
        nonlocal codes_in_era, codes_per_era, code_width, bit_buffer, bits_in, next_code, prev_none
        codes_in_era += 1
        if not prev_none and next_code <= (1 << code_width) - 1:
            next_code += 1
        prev_none = False
        if codes_in_era >= codes_per_era and code_width < max_width:
            code_width += 1
            codes_in_era = 0
            codes_per_era = 1 << (code_width - 1)
            bit_buffer = 0; bits_in = 0

    # literal first
    put(byte_val); after_code()
    emitted = 0
    while emitted < n_codes:
        c = next_code  # KwKwK: reference the entry being created
        put(c); after_code()
        emitted += 1
    if bits_in > 0:
        out.append(bit_buffer & 0xFF)
    return bytes(out)

for mw, nc in [(16, 4000), (16, 20000), (31, 20000), (31, 60000)]:
    data = encode_growth(0x41, nc, mw)
    st = LzwState()
    try:
        o1, _ = st.feed(data)
        o2, _ = st.flush()
        out_len = len(o1) + len(o2)
        dict_mem = sum(len(e) for e in st._dictionary)
        ratio = out_len / len(data)
        print(f"mw={mw} codes={nc}: input={len(data)}B decoded={out_len}B (x{ratio:.0f}) "
              f"dict_entries={len(st._dictionary)} dict_mem={dict_mem/1e6:.1f}MB")
    except Exception as e:
        print(f"mw={mw} codes={nc}: {type(e).__name__}: {str(e)[:70]}")

print("\n--- validity + stream buffer check ---")
import io, tracemalloc
from archivey.internal.streams.unix_compress import UnixCompressDecompressorStream

data = encode_growth(0x41, 30000, 16)
# validity: output is all 'A'
st = LzwState(); o1,_ = st.feed(data); o2,_=st.flush()
full = o1+o2
print(f"input={len(data)}B decoded={len(full)}B  all-'A'={set(full)=={0x41}}")

# stream-level: does a single read(1) balloon the internal buffer?
tracemalloc.start()
s = UnixCompressDecompressorStream(io.BytesIO(data), seekable=False)
one = s.read(1)
cur, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
print(f"read(1) returned {len(one)} byte; internal buffer now {len(s._buffer)/1e6:.0f}MB; peak alloc {peak/1e6:.0f}MB")
