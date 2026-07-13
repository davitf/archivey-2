# Sevenzip fixtures

`lz4.7z` is copied from py7zr's `tests/data/lz4.7z` (method `0x04f71104`).
Neither stock 7-Zip nor py7zr can extract it; Archivey decodes it via shared
`Codec.LZ4`. Used by `tests/test_sevenzip_reader.py`.
