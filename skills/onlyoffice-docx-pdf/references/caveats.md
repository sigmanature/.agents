# ONLYOFFICE DOCX to PDF Caveats

## Supported path

For local agent automation, prefer `ONLYOFFICE Document Builder` and its CLI.

On Linux, the most reliable setup tested in this session is:

- a user-local Python virtual environment containing `document-builder 9.3.0.140`
- a wrapper at `~/.local/bin/documentbuilder` that execs the embedded `docbuilder` binary with the correct runtime library path

This skill's wrapper uses:

- `documentbuilder script.docbuilder`
- `builder.OpenFile(...)`
- `builder.SaveFile("pdf", ...)`

Reason:

- the currently tested `document-builder 9.3.0.140` wheel also accepts the legacy `.docbuilder` script format through its embedded binary

## Why not Desktop Editors

`onlyoffice-desktopeditors` is primarily a GUI application. Its public CLI flags focus on opening or creating editor windows, not on headless document export.

## Why not `x2t`

Desktop Editors packages may include an internal converter binary named `x2t`, but it is not treated here as the stable primary interface for agent-driven conversion.

Local tests on this machine showed:

- `onlyoffice-desktopeditors 9.3.1-8` is installed
- `x2t` advertises an input/output CLI
- direct `docx -> pdf` attempts failed with `DoctRenderer:<result><error code="open" /></result>`

Because of that, this skill refuses to silently fall back to `x2t`.

## Builder prerequisite

If the machine only has Desktop Editors, the user still needs a Builder installation for this skill to run the supported local path.

If the user wants the recommended no-sudo path, they should run `scripts/install_onlyoffice_documentbuilder_venv.sh`.

## Verified behavior on this machine

- venv install: `document-builder 9.3.0.140`
- runtime path: embedded binary under `.../site-packages/docbuilder/lib/docbuilder`
- result: produced a valid PDF for the tested DOCX, while also printing `docbuilder: license is invalid!`

Practical implication:

- treat the venv-based modern runtime as the default path for agent use

## Output and verification

After the wrapper runs, always verify:

- the returned PDF path exists
- the file is non-empty

## Licensing note

ONLYOFFICE Document Builder can add a watermark in free usage scenarios. If the user cares about branding-free output, ask them to verify their license before adopting this in production.
