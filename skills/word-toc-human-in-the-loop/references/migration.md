# Migration Notes

This skill is portable across devices, but only the workflow logic is bundled here. The user's thesis data is not generic skill data.

## What To Copy

Copy these three groups separately:

- the skill directory `word-toc-human-in-the-loop`
- the current thesis `.docx`
- the template `.docx` used for final `restyle`

Do not assume the current working thesis and the template truth are the same file.

## Python Dependencies

The workflow script needs these Python packages:

- `python-docx`
- `Pillow`
- `lxml`

Typical install command:

```bash
python3 -m pip install python-docx Pillow lxml
```

## Font Dependency

`restyle` computes dot-leader length from real font metrics. On a new machine, the most common migration failure is missing Chinese serif/sans font files.

Run:

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/selfcheck.py
```

or:

```bash
python3 ~/.agents/skills/word-toc-human-in-the-loop/scripts/word_toc_workflow.py doctor
```

If the default candidates are missing, set one or both environment variables:

```bash
export WORD_TOC_SONG_FONTS="/path/to/song-font-1.ttc:/path/to/song-font-2.ttc"
export WORD_TOC_HEI_FONTS="/path/to/hei-font-1.ttc:/path/to/hei-font-2.ttc"
```

The separator is the host OS path separator. On Linux and macOS it is typically `:`.

## Minimal Bring-Up Procedure On A New Device

1. Install the Python dependencies.
2. Copy this skill directory to `~/.agents/skills/`.
3. Copy your current thesis `.docx`.
4. Copy your template `.docx`.
5. Run `selfcheck.py` first.
6. Then run `audit`, `prepare`, and later `restyle` using the local bundled script.

## Important Boundary

The skill is portable. Final page-number calculation is still not headless-portable.

You still need Windows Word for:

- `更新整个目录`
- final pagination truth
- final human visual judgment
