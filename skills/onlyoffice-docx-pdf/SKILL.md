---
name: onlyoffice-docx-pdf
description: Convert local DOCX files to PDF with ONLYOFFICE in an agent-friendly way. Use this whenever the user asks to turn `.docx` into `.pdf` with ONLYOFFICE, wants a local CLI flow for agents, mentions `documentbuilder` or `docbuilder`, or needs to understand why ONLYOFFICE Desktop Editors / `x2t` is not the right scripted interface.
---

# ONLYOFFICE DOCX to PDF

Use this skill when the task is local file conversion and ONLYOFFICE is the intended tool.

Prefer the supported CLI path: `ONLYOFFICE Document Builder`.

Do not default to:

- `desktopeditors` GUI flags
- the bundled `x2t` converter inside Desktop Editors

Those paths are not the primary supported automation interface for this workflow.

## Layout

Skill root: `~/.agents/skills/onlyoffice-docx-pdf/`

- `scripts/install_onlyoffice_documentbuilder_venv.sh`
- `scripts/docx2pdf_onlyoffice.sh`
- `references/caveats.md`
- `evals/evals.json`

## Install the supported CLI first

Preferred install for agent use:

```bash
~/.agents/skills/onlyoffice-docx-pdf/scripts/install_onlyoffice_documentbuilder_venv.sh
```

This installs the modern `document-builder` runtime into a user-local virtual environment and creates `~/.local/bin/documentbuilder` without using `sudo`.
After the installer finishes, the conversion wrapper can use `documentbuilder` directly.

## Default workflow

1. Install Builder with `scripts/install_onlyoffice_documentbuilder_venv.sh`.
2. Confirm the input file path is absolute or resolve it to an absolute path before passing it to the script.
3. Run the wrapper script:

```bash
~/.agents/skills/onlyoffice-docx-pdf/scripts/docx2pdf_onlyoffice.sh /abs/path/input.docx [/abs/path/output.pdf]
```

4. Verify that the output file exists and is non-empty.
5. If the wrapper reports that `documentbuilder` is missing, install it with the installer script instead of trying to silently fall back to `desktopeditors` or `x2t`.

## Wrapper behavior

- Accepts `input.docx` and an optional `output.pdf`
- Defaults the output path to the input basename with a `.pdf` extension
- Creates the output directory if it does not exist
- Supports `ONLYOFFICE_DOCUMENTBUILDER_BIN` to pin a specific executable path
- Generates a temporary `.docbuilder` script compatible with the working runtime used by this skill
- Emits clear non-zero failures for missing prerequisites or conversion failures

## Environment handling

If `documentbuilder` is not on `PATH`, check:

- `ONLYOFFICE_DOCUMENTBUILDER_BIN`
- `~/.local/bin/documentbuilder`
- other local install locations if the user chose a custom target

If it is still unavailable, run `scripts/install_onlyoffice_documentbuilder_venv.sh`.

## Troubleshooting

Read `references/caveats.md` when:

- the machine only has `onlyoffice-desktopeditors`
- someone asks about using `x2t`
- the user wants to know whether watermarks or licensing apply

## Output expectations

For normal conversions, report:

- input path
- output path
- the exact wrapper command used
- whether verification succeeded

If the conversion cannot be run, report the missing prerequisite clearly and do not imply success.
