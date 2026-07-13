## 1. Implement

- [x] 1.1 Infer presented 7z member names from the archive stem when `FILES_INFO` omits `NAME` (`raw_name` stays empty; no list-time `_1` suffixes)
- [x] 1.2 Tests: single- and multi-member nameless fixtures (stem / volume suffix / anonymous `contents`); named archives unchanged

## 2. Verify

- [x] 2.1 Targeted reader + corpus checks for nameless naming
- [x] 2.2 `openspec validate --strict nameless-7z-member-names`
