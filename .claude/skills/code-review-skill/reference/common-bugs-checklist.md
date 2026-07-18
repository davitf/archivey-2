# Common Bugs Checklist

Quick-reference bug patterns for this Python archive library. See also
[python.md](python.md) and [security-review-guide.md](security-review-guide.md).

## Universal Issues

### Logic Errors
- [ ] Off-by-one errors in loops and buffer/slice bounds
- [ ] Incorrect boolean logic (De Morgan's law violations)
- [ ] Missing `None` checks on optional metadata fields
- [ ] Race conditions if sharing archive objects across threads
- [ ] Using `is` where value equality (`==`) is required
- [ ] Integer overflow / huge lengths accepted from headers
- [ ] Floating point comparison issues (rare here; watch ratios/budgets)

### Resource Management
- [ ] File / archive handles not closed
- [ ] Temp files/dirs not cleaned up
- [ ] Decompressor / subprocess helpers left running
- [ ] Unbounded buffers holding entire members

### Error Handling
- [ ] Swallowed exceptions (bare/`except Exception: pass`)
- [ ] Generic catches hiding format-specific failures
- [ ] Missing exception chaining (`raise … from exc`)
- [ ] Wrong public exception type (breaks caller contracts)
- [ ] Missing cleanup in `finally` / context managers

## Python

- [ ] Mutable default arguments (`def f(x=[])`)
- [ ] Bare `except:` catching `KeyboardInterrupt` / `SystemExit`
- [ ] Shared mutable class attributes (`class C: items = []`)
- [ ] Modifying a list while iterating
- [ ] String concatenation in tight loops (prefer `bytearray` / `join`)
- [ ] Not using `with` for files/archives
- [ ] Missing type annotations on public APIs

**Full guide:** [Python Review Guide](python.md)

## Library / Archive Specific

- [ ] Path traversal or symlink escape on extract
- [ ] Silent re-decompression of solid blocks (cost-model surprise)
- [ ] Assuming well-formed input — hostile/truncated archives must be handled
- [ ] Breaking format parity (behavior differs silently across backends)
- [ ] Optional extra imported at module import time when it should be lazy

## Testing

- [ ] Testing implementation details instead of behavior
- [ ] Missing edge / hostile / truncated fixtures
- [ ] Flaky tests (time, ordering, leftover temp state)
- [ ] Missing negative tests (corrupt input, wrong password, missing member)
- [ ] Overly complex test setup when corpus fixtures exist
