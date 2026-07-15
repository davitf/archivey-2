import io
from archivey.internal.streams.xz import XzDecompressorStream

data = open("evil_zero_blocks.xz","rb").read()

s = XzDecompressorStream(io.BytesIO(data), seekable=True)
try:
    s.seek(0, io.SEEK_END)   # triggers _ensure_index_built -> build_index -> add_seek_points
    print("no crash, size=", s.tell())
except AssertionError as e:
    print("ASSERTIONERROR (crash on hostile input):", repr(e)[:200])
except Exception as e:
    print("other:", type(e).__name__, str(e)[:200])
