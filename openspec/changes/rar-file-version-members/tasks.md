## 1. Parser: retain file-version rows

- [x] 1.1 Stop dropping RAR5 `0x04` / RAR3 `FILE_VERSION` FILE blocks in `rar_parser`; record version vint on `RarMemberInfo`
- [x] 1.2 Keep split-merge / service-comment behavior unchanged for versioned and non-versioned rows

## 2. Reader: present names, flags, and `unrar` wiring

- [x] 2.1 Map versioned rows to `ArchiveMember` with presented name `path;n`, `is_current=False`, `extra["rar.file_version"]=n`; live path stays plain + current
- [x] 2.2 Pass presented `path;n` into named `unrar p` for non-direct `open`/`read`
- [x] 2.3 Extend `open_unrar_p` (or caller) to pass `-ver` for solid ALL-pipe demux when any versioned payload FILE is present
- [x] 2.4 Ensure direct M0 nonsolid reads still work for versioned rows when `_can_direct_read`

## 3. Fixtures and tests

- [x] 3.1 Add RAR5 `-ver` fixture generation in `scripts/gen_rar_fixtures.py` and commit binaries under `tests/fixtures/rar/`
- [x] 3.2 Tests: list shape (`path;n` + live), `read` bytes per revision, default `extract_all` skips history, solid demux with `-ver` stays aligned
- [x] 3.3 Carve rarfile list-equality / oracle paths so `-ver` history rows are not required to match rarfile’s omit behavior

## 4. Verify

- [ ] 4.1 Targeted pytest for the new fixture + solid/`unrar` cases (`requires_binary("unrar")` where needed)
- [ ] 4.2 `openspec validate --strict rar-file-version-members`
