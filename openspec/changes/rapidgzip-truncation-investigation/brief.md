# rapidgzip-truncation-investigation — pin down gzip truncation handling

**Status:** Linux characterization done (`FINDINGS.md`); macOS/Windows (1.3) and §2 lock-in still open; §3 not started. **Pre-0.2.0 pay item** (debt-ledger Q4).

**Why it matters:** rapidgzip does not reliably report truncation. Archivey’s ISIZE backstop was built on incomplete knowledge — including a hypothesis that silence might be limited to a bare ~10-byte header.

**What Linux showed:** that hypothesis is **false**. Mid-body truncations with a valid header commonly return `b""` with no exception (stdlib raises on `readall()`, but sized reads stream a correct prefix then raise). The current ISIZE check is load-bearing for `size ≥ 18` but misses the `< 18` band and multi-member bailouts; ISIZE-only also fails priority (2) by not recovering the prefix stdlib could have returned. See `FINDINGS.md`.

**Priorities:** (1) no silent success, (2) recover partial data, (3) seekability on good inputs.

**Recommendation (awaiting your lock-in):** **extend**, refined: empty→stdlib fallback for silent-empty (gets partial recovery) **plus** ISIZE for silent short/full; not remove; not narrow-only; not DIY reverse gzip seek (trailer is CRC+ISIZE only).

**Your call:** lock §2 (including task 2.5 empty-fallback) after reviewing `FINDINGS.md`; optionally run the sweep on macOS / add CI. §3 implements after that.
