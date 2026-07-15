import zlib, gzip, tempfile, os
from dataclasses import replace
from archivey.internal.config import DEFAULT_STREAM_CONFIG
from archivey.config import AcceleratorMode
from archivey.internal.streams.codecs import open_codec_stream, Codec

payload = b"The quick brown fox. "*5000
def corrupt_mid(data):
    b = bytearray(data); 
    for i in range(len(b)//2, len(b)//2+20): b[i] ^= 0xFF
    return bytes(b)

cfg = replace(DEFAULT_STREAM_CONFIG, seekable=True, use_rapidgzip=AcceleratorMode.ON)
for codec, data, label in [
    (Codec.GZIP, gzip.compress(payload,9), "gzip"),
    (Codec.DEFLATE, (lambda: (lambda c: c.compress(payload)+c.flush())(zlib.compressobj(9,zlib.DEFLATED,-15)))(), "deflate"),
    (Codec.ZLIB, zlib.compress(payload,9), "zlib"),
]:
    cd = corrupt_mid(data)
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(cd); path=f.name
    try:
        with open_codec_stream(codec, path, config=cfg) as s:
            out = s.read()
        print(f"{label} corrupt [rapidgzip]: read {len(out)}B NO error")
    except Exception as e:
        print(f"{label} corrupt [rapidgzip]: {type(e).__name__}: {str(e)[:70]}")
    finally:
        os.unlink(path)
