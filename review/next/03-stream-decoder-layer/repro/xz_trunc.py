import io, lzma
from archivey.internal.streams.xz import XzDecompressorStream
from archivey.internal.streams.lzip import LzipDecompressorStream

# valid xz then truncate mid-stream
raw = lzma.compress(b"HELLO WORLD"*100, format=lzma.FORMAT_XZ)
trunc = raw[:len(raw)//2]
s = XzDecompressorStream(io.BytesIO(trunc), seekable=False)
try:
    d = s.read(-1)
    print(f"xz read(-1) -> {len(d)} bytes NO raise")
except Exception as e:
    print("xz read(-1) raised on FIRST call:", type(e).__name__)
