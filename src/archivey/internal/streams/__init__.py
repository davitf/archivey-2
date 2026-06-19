"""The compressed + seekable stream layer.

Format backends never call codec libraries directly; they compose the backends in
this package (see the ``compressed-streams`` and ``seekable-decompressor-streams``
capabilities). The detection peek/rewind primitive (``PeekableStream``) is *not* here
— it lands with format detection in Phase 3.
"""
