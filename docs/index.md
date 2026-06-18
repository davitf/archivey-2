# Archivey

Archivey reads, streams, and safely extracts ZIP / TAR / RAR / 7z / ISO / directory /
single-file-compressed archives behind one uniform interface.

This site is the generated **API reference**. The narrative design lives in the
repository's `SPEC.md`, `ARCHITECTURE.md`, and the authoritative capability specs under
`openspec/specs/`.

- **[API reference](api.md)** — `open_archive()`, the `ArchiveReader` surface, and the
  public data model (`ArchiveMember`, `ArchiveInfo`, `ArchiveFormat`, the `CostReceipt`
  and its enums, and the error hierarchy).

```python
import archivey

with archivey.open_archive("photos.zip") as reader:
    for member in reader:
        print(member.name, member.size)
```
