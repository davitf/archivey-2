import gzip, io, tempfile, os
from dataclasses import replace
from archivey.internal.config import DEFAULT_STREAM_CONFIG
from archivey.config import AcceleratorMode
from archivey.internal.streams.codecs import open_codec_stream, Codec

payload = b"The quick brown fox. "*5000
gz = gzip.compress(payload, 9)
trunc = gz[:len(gz)*2//3]
cfg = replace(DEFAULT_STREAM_CONFIG, seekable=True, use_rapidgzip=AcceleratorMode.ON)

# path source (backstop should be active)
with tempfile.NamedTemporaryFile(suffix=".gz", delete=False) as f:
    f.write(trunc); path=f.name
try:
    with open_codec_stream(Codec.GZIP, path, config=cfg) as s:
        out = s.read()
    print(f"gzip trunc [path, backstop]: read {len(out)}B NO error")
except Exception as e:
    print(f"gzip trunc [path, backstop]: raised {type(e).__name__}")
finally:
    os.unlink(path)

# non-path (BytesIO) source: backstop is skipped
try:
    with open_codec_stream(Codec.GZIP, io.BytesIO(trunc), config=cfg) as s:
        out = s.read()
    print(f"gzip trunc [BytesIO, no backstop]: read {len(out)}B NO error")
except Exception as e:
    print(f"gzip trunc [BytesIO, no backstop]: raised {type(e).__name__}: {str(e)[:50]}")
