---
name: rw-test-harness
description: Use the rw/mmap test harness in /home/nzzhao/learn_os/myscripts/shared_with_qemu/test to run, extend, or refactor buffered I/O and mmap test cases around rw_test.py, rw_matrix.sh, rw_matrix_inline.sh, mkwrite_test.py, and mmap_wp_fault_test.py. Trigger this whenever the user asks to add or modify read/write test cases, matrix cases, inlinecrypt or fscrypt coverage, hole/existing/truncate cases, read-then-write coverage, mmap fault cases, or wants agents to use this framework rather than inventing one-off scripts.
---

# RW Test Harness

Use this skill for the project-local rw test framework under:

- `/home/nzzhao/learn_os/myscripts/shared_with_qemu/test`

The framework has one core provider:

- `rw_test.py`

And a small set of thin frontends:

- `rw_matrix.sh`
- `rw_matrix_inline.sh`
- `mkwrite_test.py`
- `mmap_wp_fault_test.py`

## Goal

Keep all future buffered-I/O and mmap testing inside the existing framework instead of creating more one-off scripts or duplicating verifier logic in shell.

## Start Here

1. Open `references/framework-map.md`.
2. Confirm whether the user needs:
   - run existing cases,
   - add buffered-I/O cases,
   - add mmap cases,
   - adjust thin shell wrappers,
   - or update the skill itself.
3. Prefer editing `rw_test.py` provider logic first.
4. Keep wrappers thin. Do not reintroduce heredoc Python into shell scripts.

## Framework Rules

### Buffered-I/O family

Use these entrypoints:

- `python3 rw_test.py case ...`
- `python3 rw_test.py matrix ...`
- `./rw_matrix.sh ...`
- `./rw_matrix_inline.sh ...`

When adding buffered-I/O coverage:

- extend the provider in `rw_test.py`,
- prefer data-driven case additions over ad hoc functions,
- keep `MatrixCase` and builtin matrix generation authoritative,
- add `read_then_write` variants through the existing structured knobs rather than custom pre-read code in shell.

### mmap family

Use these entrypoints:

- `python3 rw_test.py mmap-case ...`
- `python3 rw_test.py mmap-matrix ...`
- `python3 mkwrite_test.py ...`
- `python3 mmap_wp_fault_test.py ...`

When adding mmap coverage:

- add the reusable case implementation to `rw_test.py`,
- register the case in the builtin mmap case registry,
- keep `mkwrite_test.py` and `mmap_wp_fault_test.py` as thin frontends unless they truly need specialized behavior,
- keep trace-specific reporting separate from generic case execution when possible.

## Editing Policy

1. Prefer extending `rw_test.py` provider helpers and case registries.
2. Only touch shell wrappers for environment selection, root bootstrap, mount checks, or argument forwarding.
3. If a repeated command sequence appears, consider whether it belongs in:
   - the skill references,
   - the framework itself,
   - or a reusable script.
4. Do not fork parallel test architectures inside the same repo.

## Validation Workflow

After changes, prefer this order:

1. `python3 -m py_compile rw_test.py mkwrite_test.py mmap_wp_fault_test.py`
2. targeted `rw_test.py case` or `rw_test.py mmap-case` smoke run on `/tmp`
3. targeted `rw_test.py matrix` or `rw_test.py mmap-matrix` smoke run on `/tmp`
4. `bash -n rw_matrix.sh`
5. `bash -n rw_matrix_inline.sh`
6. only then run real f2fs/inlinecrypt/tracefs environments if needed

If root, fscrypt, inlinecrypt, or tracefs are unavailable, still complete static validation and local smoke tests, then state that environment validation is pending.

## Output Contract

When working with this framework:

- say which family you changed: buffered-I/O, mmap, wrappers, or skill
- name the command(s) you used for validation
- call out any unverified root-only or tracefs-only paths
- mention whether you changed provider logic or only frontends

## References

- `references/framework-map.md`
- `references/case-authoring.md`

## Evaluation Prompts

See `evals/evals.json` for realistic prompts that should trigger this skill.
