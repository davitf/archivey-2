# Archivey

**Archivey** is a Python library that provides a unified interface for reading,
streaming, and safely extracting archives: ZIP, TAR (all variants), RAR, 7z,
ISO 9660, plain directories, and single-file compressed streams
(GZ / BZ2 / XZ / ZST).

```python
from archivey import open_archive

with open_archive("example.zip") as archive:  # Automatic format detection
    archive.extractall("output_dir/")

    for member, stream in archive.iter_members_with_streams():
        print(member.filename, member.type, member.file_size)
        if stream is not None:  # File-like stream for files, None for dirs/links
            data = stream.read()
```

This is the **v2** rewrite of Archivey. See `openspec/` for the capability
specs and `PLAN.md` for the phased implementation roadmap.

## Installation

The core installs with no third-party runtime dependencies and supports ZIP,
TAR, the single-file GZ/BZ2/XZ compressors, directories, and (interim) 7z/RAR
reading. Optional formats are gated behind extras:

```bash
pip install archivey          # core
pip install archivey[all]     # every optional format
pip install archivey[iso]     # just ISO 9660 support
```

Requires Python 3.11+.
