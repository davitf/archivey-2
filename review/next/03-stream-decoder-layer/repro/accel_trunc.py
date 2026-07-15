import io, zlib, tempfile, os
from dataclasses import replace
from archivey.internal.config import DEFAULT_STREAM_CONFIG
from archivey.config import AcceleratorMode
from archivey.internal.streams.codecs import open_codec_stream, Codec

payload = b"The quick brown fox. "*5000   # ~105KB
# raw deflate
co = zlib.compressobj(9, zlib.DEFLATED, -15)
raw_deflate = co.compress(payload) + co.flush()
# zlib-wrapped
zlib_data = zlib.compress(payload, 9)

def run(codec, data, label):
    trunc = data[:len(data)*2//3]   # cut off the end
    for mode,name in [(AcceleratorMode.OFF,"stdlib"), (AcceleratorMode.ON,"rapidgzip")]:
        cfg = replace(DEFAULT_STREAM_CONFIG, seekable=True, use_rapidgzip=mode)
        # write to a temp path (rapidgzip prefers a real file)
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            f.write(trunc); path=f.name
        try:
            with open_codec_stream(codec, path, config=cfg) as s:
                out = s.read()
            print(f"{label} [{name}]: read {len(out)} bytes, NO error (truncation swallowed)")
        except Exception as e:
            print(f"{label} [{name}]: raised {type(e).__name__}: {str(e)[:60]}")
        finally:
            os.unlink(path)

run(Codec.DEFLATE, raw_deflate, "raw-deflate")
run(Codec.ZLIB, zlib_data, "zlib")
