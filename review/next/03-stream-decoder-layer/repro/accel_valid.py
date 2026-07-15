import zlib, tempfile, os
from dataclasses import replace
from archivey.internal.config import DEFAULT_STREAM_CONFIG
from archivey.config import AcceleratorMode
from archivey.internal.streams.codecs import open_codec_stream, Codec

payload = b"The quick brown fox. "*5000
co = zlib.compressobj(9, zlib.DEFLATED, -15)
raw_deflate = co.compress(payload) + co.flush()
zlib_data = zlib.compress(payload, 9)

def run(codec, data, label, trunc_frac):
    data2 = data if trunc_frac==1.0 else data[:int(len(data)*trunc_frac)]
    cfg = replace(DEFAULT_STREAM_CONFIG, seekable=True, use_rapidgzip=AcceleratorMode.ON)
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(data2); path=f.name
    try:
        with open_codec_stream(codec, path, config=cfg) as s:
            out = s.read()
        print(f"{label} frac={trunc_frac}: rapidgzip read {len(out)}B (expected {len(payload)}), match_prefix={payload.startswith(out) if out else 'n/a'}")
    except Exception as e:
        print(f"{label} frac={trunc_frac}: raised {type(e).__name__}: {str(e)[:50]}")
    finally:
        os.unlink(path)

for frac in (1.0, 0.9, 0.66):
    run(Codec.DEFLATE, raw_deflate, "raw-deflate", frac)
for frac in (1.0, 0.9, 0.66):
    run(Codec.ZLIB, zlib_data, "zlib", frac)
