import io
from archivey.internal.streams.xz import XzDecompressorStream
data = open("evil_zero_blocks.xz","rb").read()
s = XzDecompressorStream(io.BytesIO(data), seekable=True)
try:
    print("size via try_get_size:", s.try_get_size())
except AssertionError as e:
    print("ASSERTIONERROR via try_get_size:", repr(e)[:120])
