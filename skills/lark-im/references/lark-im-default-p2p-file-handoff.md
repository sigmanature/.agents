# Default P2P File Handoff

Use this reference when the user wants the agent to exchange files with them through Feishu/Lark.

## Default target

Verified default Agent<->User P2P chat in this environment:

- `chat_id`: `oc_0342e7185c23a67db703d32a42afcd68`
- identity: `--as bot`

This chat should be the default target unless the user explicitly provides another `chat_id` or asks for a different recipient.

## Send a file to the user

Generic file:

```bash
lark-cli im +messages-send \
  --as bot \
  --chat-id oc_0342e7185c23a67db703d32a42afcd68 \
  --file ./artifact.docx
```

Image:

```bash
lark-cli im +messages-send \
  --as bot \
  --chat-id oc_0342e7185c23a67db703d32a42afcd68 \
  --image ./figure.png
```

Notes:

- `+messages-send` only accepts cwd-relative local paths for local files.
- For arbitrary binary / archive / office artifacts, prefer `--file`.
- For images intended to render inline, prefer `--image`.

## Inspect historical files

List recent messages and look for `msg_type: "file"` or `msg_type: "image"`:

```bash
lark-cli im +chat-messages-list \
  --as bot \
  --chat-id oc_0342e7185c23a67db703d32a42afcd68 \
  --page-size 20 \
  --no-reactions \
  --format json
```

Useful fields from each file message:

- `message_id` like `om_xxx`
- message `content` containing:
  - `file key="file_xxx"` for files
  - `image_key` / `img_xxx` for images

## Pull a historical file to local disk

```bash
lark-cli im +messages-resources-download \
  --as bot \
  --message-id om_xxx \
  --file-key file_xxx \
  --type file \
  --output ./downloaded_artifact.bin
```

For images:

```bash
lark-cli im +messages-resources-download \
  --as bot \
  --message-id om_xxx \
  --file-key img_xxx \
  --type image \
  --output ./downloaded_image.png
```

## Validated examples in this environment

Historical file listing in the default P2P chat has already been validated with `--as bot`, including file messages such as:

- `VMA_boundary_readahead_patch_report.docx`
- `mmap_align_analysis_FINAL.docx`

Historical file download has also been validated with:

- `message_id`: `om_x100b6ca5d4bf28a8c1de5b3bf90dd95`
- `file_key`: `file_v3_0012t_a61d14bf-5fcb-4f3a-b158-f53d34595cdg`

which successfully downloaded a `.docx` artifact to local disk.
