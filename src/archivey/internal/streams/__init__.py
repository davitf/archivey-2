"""Compressed + seekable stream layer used by format backends.

Backends never import codec libraries directly; they compose the pieces below
(see ``compressed-streams`` / ``seekable-decompressor-streams``).

Package map:

- :mod:`.streamtools` — codec-/format-agnostic ``BinaryIO`` plumbing (slice, lock,
  shared views, solid demux). Must not import the rest of archivey.
- :mod:`.codecs` — ``Codec`` / ``StreamCodec`` / ``open_codec_stream`` (the uniform
  pull-based codec table + detection signals).
- :mod:`.decompressor_stream` — seekable decode *engine* (``DecompressorStream`` +
  ``Decoder`` protocol).
- :mod:`.decompress` — thin ``BaseDecoder`` adapters (zlib, Brotli, PPMd, BCJ,
  Deflate64) that plug into that engine.
- :mod:`.xz` / :mod:`.lzip` / :mod:`.unix_compress` — larger codec-specific decoders
  (index scan / LZW) that also plug into ``DecompressorStream``.
- :mod:`.archive_stream` — public member/codec handle: exception translate+stamp,
  lazy open, nested collapse, fused digest verify, lease/finalizer.
- :mod:`.verify` — ``MemberVerifier`` (+ standalone ``VerifyingStream`` for codec
  length backstops).
- :mod:`.crypto` — AES decrypt stage (``[crypto]``) + 7z-local KDF helpers.
- :mod:`.counting` — measurement wrappers (bytes / seeks).
- :mod:`.peekable` — non-seekable detection peek/replay (used by ``open_archive``).
"""
