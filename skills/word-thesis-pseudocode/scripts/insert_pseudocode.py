#!/usr/bin/env python3

import argparse
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W_NS}
ET.register_namespace("w", W_NS)

DEFAULT_FONT_SIZE = "21"
TITLE_FONT_SIZE = "21"
SECTION_TITLE_FONT_SIZE = "24"
LINE_NUMBER_FONT_SIZE = "19"
RULE_COLOR = "444444"
TEXT_COLOR = "222222"
LINE_NUMBER_COLOR = "666666"


@dataclass
class AlgorithmLine:
    indent: int
    text: str


@dataclass
class AlgorithmSpec:
    alg_id: str
    chapter_token: str
    index_token: str
    title: str
    lines: list[AlgorithmLine]
    source_path: str = ""
    placeholder_index: int | None = None
    resolved_chapter: str | None = None
    resolved_index: str | None = None
    bookmark_id: int | None = None

    @property
    def placeholder(self) -> str:
        return "{{ALG:%s}}" % self.alg_id

    @property
    def label(self) -> str:
        return "算法 %s-%s" % (self.resolved_chapter, self.resolved_index)

    @property
    def caption(self) -> str:
        return "%s %s" % (self.label, self.title)

    @property
    def bookmark_name(self) -> str:
        return "alg-%s" % sanitize_bookmark_name(self.alg_id)


def main() -> int:
    args = parse_args()
    specs = [parse_algorithm_file(Path(path)) for path in args.alg]
    transform_docx(Path(args.input), Path(args.output), specs)
    return 0


def parse_args():
    parser = argparse.ArgumentParser(
        description="Replace Word placeholders with thesis-style pseudocode blocks."
    )
    parser.add_argument("--input", required=True, help="Input .docx path")
    parser.add_argument(
        "--alg",
        action="append",
        required=True,
        help="Algorithm DSL file. Repeat this flag for multiple algorithms.",
    )
    parser.add_argument("--output", required=True, help="Output .docx path")
    return parser.parse_args()


def transform_docx(input_docx: Path, output_docx: Path, specs: list[AlgorithmSpec]):
    entries = read_docx_entries(input_docx)
    root = parse_document_root(entries)
    body = root.find("w:body", NS)
    if body is None:
        raise SystemExit("document body not found")

    resolve_algorithm_positions_and_numbers(body, specs)
    replace_algorithm_placeholders(body, specs)
    replace_loa_placeholders(body, specs)
    replace_reference_placeholders(root, specs)

    entries["word/document.xml"] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    write_docx_entries(output_docx, entries)


def parse_algorithm_file(path: Path) -> AlgorithmSpec:
    lines = path.read_text(encoding="utf-8").splitlines()
    first_non_empty_index = next((idx for idx, line in enumerate(lines) if line.strip()), None)
    if first_non_empty_index is None:
        raise SystemExit("algorithm file is empty: %s" % path)

    header = lines[first_non_empty_index].strip()
    if not header.startswith("@algorithm "):
        raise SystemExit("first non-empty line must start with @algorithm: %s" % path)

    attrs = parse_header_attributes(header[len("@algorithm ") :])
    required = ["id", "title"]
    missing = [key for key in required if key not in attrs]
    if missing:
        raise SystemExit("missing header keys in %s: %s" % (path, ", ".join(missing)))

    chapter_token = attrs.get("chapter", "auto")
    index_token = attrs.get("index", "auto")

    body_lines: list[AlgorithmLine] = []
    for raw in lines[first_non_empty_index + 1 :]:
        if not raw.strip():
            continue
        expanded = raw.replace("\t", "    ")
        indent_spaces = len(expanded) - len(expanded.lstrip(" "))
        indent = indent_spaces // 4
        normalized = normalize_algorithm_line(expanded.strip())
        if normalized is None:
            continue
        body_lines.append(AlgorithmLine(indent=indent, text=normalized))

    if not body_lines:
        raise SystemExit("algorithm body is empty: %s" % path)

    return AlgorithmSpec(
        alg_id=attrs["id"],
        chapter_token=chapter_token,
        index_token=index_token,
        title=attrs["title"],
        lines=body_lines,
        source_path=str(path),
    )


def parse_header_attributes(text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    title_match = re.search(r'title=(".*?"|.+)$', text)
    title_value = None
    if title_match:
        title_value = title_match.group(1).strip()
        text = text[: title_match.start()].rstrip()

    for key, value in re.findall(r'(\w+)=(".*?"|\S+)', text):
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        attrs[key] = value

    if title_value is not None:
        attrs["title"] = title_value.strip('"')
    return attrs


def normalize_algorithm_line(text: str) -> str | None:
    if re.match(r"\\End(If|For|While|Else|State)?\b", text):
        return None

    command_rules = [
        (r"\\(Input|KwIn)\s+(.+)", lambda m: "Input: %s" % m.group(2)),
        (r"\\(Output|KwOut)\s+(.+)", lambda m: "Output: %s" % m.group(2)),
        (r"\\For\s+(.+)", lambda m: "for %s:" % strip_trailing_colon(m.group(1))),
        (r"\\While\s+(.+)", lambda m: "while %s:" % strip_trailing_colon(m.group(1))),
        (r"\\If\s+(.+)", lambda m: "if %s:" % strip_trailing_colon(m.group(1))),
        (r"\\Else(?:\s+.+)?", lambda m: "else:"),
        (r"\\Return\s+(.+)", lambda m: "return %s" % m.group(1)),
        (r"\\State\s+(.+)", lambda m: m.group(1)),
        (r"\\Comment\s+(.+)", lambda m: "// %s" % m.group(1)),
    ]
    for pattern, builder in command_rules:
        match = re.fullmatch(pattern, text)
        if match:
            return builder(match)

    return text


def resolve_algorithm_positions_and_numbers(body, specs: list[AlgorithmSpec]):
    if not specs:
        raise SystemExit("at least one --alg file is required")

    seen_ids: set[str] = set()
    paragraphs = list(body)
    for spec in specs:
        if spec.alg_id in seen_ids:
            raise SystemExit("duplicate algorithm id: %s" % spec.alg_id)
        seen_ids.add(spec.alg_id)

        spec.placeholder_index = find_top_level_paragraph_index(body, spec.placeholder)
        if spec.placeholder_index is None:
            raise SystemExit("placeholder not found: %s" % spec.placeholder)

    ordered = sorted(specs, key=lambda item: item.placeholder_index or 0)
    chapter_counters: dict[str, int] = {}
    bookmark_counter = 1
    for spec in ordered:
        chapter = spec.chapter_token
        if is_auto_token(chapter):
            chapter = detect_chapter_from_context(paragraphs, spec.placeholder_index or 0)

        index = spec.index_token
        if is_auto_token(index):
            next_index = chapter_counters.get(chapter, 0) + 1
            chapter_counters[chapter] = next_index
            index = str(next_index)
        else:
            numeric = safe_int(index)
            if numeric is not None:
                chapter_counters[chapter] = max(chapter_counters.get(chapter, 0), numeric)

        spec.resolved_chapter = str(chapter)
        spec.resolved_index = str(index)
        spec.bookmark_id = bookmark_counter
        bookmark_counter += 1


def replace_algorithm_placeholders(body, specs: list[AlgorithmSpec]):
    ordered = sorted(specs, key=lambda item: item.placeholder_index or 0, reverse=True)
    for spec in ordered:
        body_children = list(body)
        idx = spec.placeholder_index or 0
        target = body_children[idx]
        body.remove(target)
        body.insert(idx, build_algorithm_table(spec))
        body.insert(idx, build_title_paragraph(spec))


def replace_loa_placeholders(body, specs: list[AlgorithmSpec]):
    ordered_specs = sorted(specs, key=lambda item: item.placeholder_index or 0)
    placeholder_indexes = [
        idx
        for idx, child in enumerate(list(body))
        if child.tag == qn("w:p") and paragraph_text(child).strip() == "{{LOA}}"
    ]

    for idx in reversed(placeholder_indexes):
        body_children = list(body)
        target = body_children[idx]
        body.remove(target)
        body.insert(idx, build_loa_table(ordered_specs))
        body.insert(idx, build_section_title_paragraph("算法目录"))


def replace_reference_placeholders(root, specs: list[AlgorithmSpec]):
    label_map = {spec.alg_id: spec.label for spec in specs}
    pattern = re.compile(r"\{\{ALGREF:([^}]+)\}\}")

    for paragraph in root.findall(".//w:p", NS):
        text = paragraph_text(paragraph)
        if "{{ALGREF:" not in text:
            continue

        def repl(match):
            alg_id = match.group(1)
            if alg_id not in label_map:
                raise SystemExit("unknown algorithm reference: %s" % alg_id)
            return label_map[alg_id]

        replace_paragraph_text(paragraph, pattern.sub(repl, text))


def build_title_paragraph(spec: AlgorithmSpec):
    p = elem("w:p")
    p_pr = child(p, "w:pPr")
    jc = child(p_pr, "w:jc")
    jc.set(qn("w:val"), "center")
    spacing = child(p_pr, "w:spacing")
    spacing.set(qn("w:before"), "120")
    spacing.set(qn("w:after"), "140")

    add_bookmark_boundary(p, spec.bookmark_id or 1, spec.bookmark_name, start=True)
    add_text_run(
        p,
        spec.caption,
        ascii_font="Times New Roman",
        east_asia_font="宋体",
        size=TITLE_FONT_SIZE,
        no_proof=True,
    )
    add_bookmark_boundary(p, spec.bookmark_id or 1, spec.bookmark_name, start=False)
    return p


def build_section_title_paragraph(text: str):
    p = elem("w:p")
    p_pr = child(p, "w:pPr")
    jc = child(p_pr, "w:jc")
    jc.set(qn("w:val"), "center")
    spacing = child(p_pr, "w:spacing")
    spacing.set(qn("w:before"), "0")
    spacing.set(qn("w:after"), "220")
    add_text_run(
        p,
        text,
        ascii_font="Times New Roman",
        east_asia_font="宋体",
        size=SECTION_TITLE_FONT_SIZE,
        no_proof=True,
    )
    return p


def build_algorithm_table(spec: AlgorithmSpec):
    tbl = build_table_shell(["560", "8640"], style="algorithm")
    for number, line in enumerate(spec.lines, start=1):
        tr = child(tbl, "w:tr")
        tr_pr = child(tr, "w:trPr")
        tr_height = child(tr_pr, "w:trHeight")
        tr_height.set(qn("w:val"), "360")
        tr_height.set(qn("w:hRule"), "atLeast")

        tr.append(
            build_table_cell(
                str(number),
                width="560",
                align="right",
                role="line_number",
            )
        )
        tr.append(
            build_table_cell(
                line.text,
                width="8640",
                align="left",
                indent=line.indent,
                role="code",
            )
        )
    return tbl


def build_loa_table(specs: list[AlgorithmSpec]):
    tbl = build_table_shell(["1680", "7520"], style="loa")
    for spec in specs:
        tr = child(tbl, "w:tr")
        tr_pr = child(tr, "w:trPr")
        tr_height = child(tr_pr, "w:trHeight")
        tr_height.set(qn("w:val"), "300")
        tr_height.set(qn("w:hRule"), "atLeast")
        tr.append(build_table_cell(spec.label, width="1680", align="left", role="loa_label"))
        tr.append(build_table_cell(spec.title, width="7520", align="left", role="loa_title"))
    return tbl


def build_table_shell(column_widths: list[str], style: str):
    tbl = elem("w:tbl")
    tbl_pr = child(tbl, "w:tblPr")
    tbl_w = child(tbl_pr, "w:tblW")
    tbl_w.set(qn("w:w"), "0")
    tbl_w.set(qn("w:type"), "auto")

    tbl_jc = child(tbl_pr, "w:jc")
    tbl_jc.set(qn("w:val"), "center")

    tbl_layout = child(tbl_pr, "w:tblLayout")
    tbl_layout.set(qn("w:type"), "fixed")
    set_table_margins(
        tbl_pr,
        top="70" if style == "algorithm" else "20",
        bottom="70" if style == "algorithm" else "20",
        left="90" if style == "algorithm" else "0",
        right="110" if style == "algorithm" else "0",
    )
    if style == "algorithm":
        set_table_borders(
            tbl_pr,
            {
                "top": ("single", "8", RULE_COLOR),
                "left": ("nil", "0", RULE_COLOR),
                "bottom": ("single", "8", RULE_COLOR),
                "right": ("nil", "0", RULE_COLOR),
                "insideH": ("nil", "0", RULE_COLOR),
                "insideV": ("single", "6", RULE_COLOR),
            },
        )
    elif style == "loa":
        set_table_borders(
            tbl_pr,
            {
                "top": ("nil", "0", RULE_COLOR),
                "left": ("nil", "0", RULE_COLOR),
                "bottom": ("nil", "0", RULE_COLOR),
                "right": ("nil", "0", RULE_COLOR),
                "insideH": ("nil", "0", RULE_COLOR),
                "insideV": ("nil", "0", RULE_COLOR),
            },
        )
    else:
        raise ValueError("unknown table style: %s" % style)

    tbl_grid = child(tbl, "w:tblGrid")
    for width in column_widths:
        grid_col = child(tbl_grid, "w:gridCol")
        grid_col.set(qn("w:w"), width)
    return tbl


def build_table_cell(
    text: str,
    width: str,
    align: str,
    indent: int = 0,
    role: str = "body",
):
    tc = elem("w:tc")
    tc_pr = child(tc, "w:tcPr")
    tc_w = child(tc_pr, "w:tcW")
    tc_w.set(qn("w:w"), width)
    tc_w.set(qn("w:type"), "dxa")
    v_align = child(tc_pr, "w:vAlign")
    v_align.set(qn("w:val"), "center")

    p = child(tc, "w:p")
    p_pr = child(p, "w:pPr")
    jc = child(p_pr, "w:jc")
    jc.set(qn("w:val"), align)
    spacing = child(p_pr, "w:spacing")
    spacing.set(qn("w:before"), "0")
    spacing.set(qn("w:after"), "0")
    spacing.set(qn("w:line"), "240")
    spacing.set(qn("w:lineRule"), "auto")
    if indent:
        ind = child(p_pr, "w:ind")
        ind.set(qn("w:left"), str(110 + indent * 360))
    elif role in {"code", "loa_title"}:
        ind = child(p_pr, "w:ind")
        ind.set(qn("w:left"), "30")

    if role == "line_number":
        add_text_run(
            p,
            text,
            ascii_font="Times New Roman",
            east_asia_font="Times New Roman",
            size=LINE_NUMBER_FONT_SIZE,
            color=LINE_NUMBER_COLOR,
            no_proof=True,
        )
    elif role == "code":
        add_code_runs(p, text)
    elif role == "loa_label":
        add_text_run(
            p,
            text,
            ascii_font="Times New Roman",
            east_asia_font="宋体",
            size=DEFAULT_FONT_SIZE,
            no_proof=True,
        )
    elif role == "loa_title":
        add_text_run(
            p,
            text,
            ascii_font="Times New Roman",
            east_asia_font="Times New Roman",
            size=DEFAULT_FONT_SIZE,
            no_proof=True,
        )
    else:
        add_text_run(
            p,
            text,
            ascii_font="Times New Roman",
            east_asia_font="宋体",
            size=DEFAULT_FONT_SIZE,
            no_proof=True,
        )
    return tc


def add_bookmark_boundary(paragraph, bookmark_id: int, bookmark_name: str, start: bool):
    tag = "w:bookmarkStart" if start else "w:bookmarkEnd"
    node = child(paragraph, tag)
    node.set(qn("w:id"), str(bookmark_id))
    if start:
        node.set(qn("w:name"), bookmark_name)


def replace_paragraph_text(paragraph, text: str):
    p_pr = paragraph.find("w:pPr", NS)
    for child_node in list(paragraph):
        if p_pr is not None and child_node is p_pr:
            continue
        paragraph.remove(child_node)
    add_text_run(
        paragraph,
        text,
        ascii_font="Times New Roman",
        east_asia_font="宋体",
        size=DEFAULT_FONT_SIZE,
    )


def add_run(paragraph, text: str):
    return child(paragraph, "w:r")


def add_text_run(
    paragraph,
    text: str,
    ascii_font: str,
    east_asia_font: str,
    size: str,
    bold: bool = False,
    italic: bool = False,
    color: str | None = None,
    no_proof: bool = False,
):
    run = add_run(paragraph, text)
    r_pr = child(run, "w:rPr")
    fonts = child(r_pr, "w:rFonts")
    fonts.set(qn("w:ascii"), ascii_font)
    fonts.set(qn("w:hAnsi"), ascii_font)
    fonts.set(qn("w:eastAsia"), east_asia_font)
    fonts.set(qn("w:cs"), ascii_font)
    size_node = child(r_pr, "w:sz")
    size_node.set(qn("w:val"), size)
    size_cs = child(r_pr, "w:szCs")
    size_cs.set(qn("w:val"), size)
    if bold:
        child(r_pr, "w:b")
    if italic:
        child(r_pr, "w:i")
    if color:
        color_node = child(r_pr, "w:color")
        color_node.set(qn("w:val"), color)
    if no_proof:
        child(r_pr, "w:noProof")
    lang = child(r_pr, "w:lang")
    lang.set(qn("w:val"), "en-US")
    lang.set(qn("w:eastAsia"), "zh-CN")
    t = child(run, "w:t")
    if text.startswith(" ") or text.endswith(" "):
        t.set(qn("xml:space"), "preserve")
    t.text = text
    return run


def add_code_runs(paragraph, text: str):
    stripped = text.lstrip()
    if stripped.startswith("// "):
        add_text_run(
            paragraph,
            text,
            ascii_font="Courier New",
            east_asia_font="Courier New",
            size=DEFAULT_FONT_SIZE,
            italic=True,
            color="555555",
            no_proof=True,
        )
        return

    keyword_specs = [
        ("Input:", "Input:"),
        ("Output:", "Output:"),
        ("for ", "for"),
        ("while ", "while"),
        ("if ", "if"),
        ("else:", "else:"),
        ("return ", "return"),
    ]
    for prefix, keyword in keyword_specs:
        if text.startswith(prefix):
            add_text_run(
                paragraph,
                keyword,
                ascii_font="Courier New",
                east_asia_font="Courier New",
                size=DEFAULT_FONT_SIZE,
                bold=True,
                color=TEXT_COLOR,
                no_proof=True,
            )
            remainder = text[len(keyword) :]
            if remainder:
                add_text_run(
                    paragraph,
                    remainder,
                    ascii_font="Courier New",
                    east_asia_font="Courier New",
                    size=DEFAULT_FONT_SIZE,
                    color=TEXT_COLOR,
                    no_proof=True,
                )
            return

    add_text_run(
        paragraph,
        text,
        ascii_font="Courier New",
        east_asia_font="Courier New",
        size=DEFAULT_FONT_SIZE,
        color=TEXT_COLOR,
        no_proof=True,
    )


def set_table_borders(tbl_pr, borders: dict[str, tuple[str, str, str]]):
    tbl_borders = child(tbl_pr, "w:tblBorders")
    for edge in ["top", "left", "bottom", "right", "insideH", "insideV"]:
        border = child(tbl_borders, "w:%s" % edge)
        val, size, color = borders[edge]
        border.set(qn("w:val"), val)
        border.set(qn("w:sz"), size)
        border.set(qn("w:space"), "0")
        border.set(qn("w:color"), color)


def set_table_margins(tbl_pr, top: str, bottom: str, left: str, right: str):
    margins = child(tbl_pr, "w:tblCellMar")
    for edge, value in {
        "top": top,
        "bottom": bottom,
        "left": left,
        "right": right,
    }.items():
        node = child(margins, "w:%s" % edge)
        node.set(qn("w:w"), value)
        node.set(qn("w:type"), "dxa")


def find_top_level_paragraph_index(body, target_text: str) -> int | None:
    for idx, child_node in enumerate(list(body)):
        if child_node.tag != qn("w:p"):
            continue
        if paragraph_text(child_node).strip() == target_text:
            return idx
    return None


def detect_chapter_from_context(body_children, placeholder_index: int) -> str:
    for idx in range(placeholder_index - 1, -1, -1):
        node = body_children[idx]
        if node.tag != qn("w:p"):
            continue
        chapter = extract_chapter_number(paragraph_text(node))
        if chapter is not None:
            return chapter
    raise SystemExit("could not infer chapter number for placeholder at index %s" % placeholder_index)


def extract_chapter_number(text: str) -> str | None:
    patterns = [
        r"^\s*第\s*(\d+)\s*章",
        r"^\s*(\d+)(?:\s+|[.、．])",
    ]
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            return match.group(1)
    return None


def paragraph_text(paragraph) -> str:
    return "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))


def read_docx_entries(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path, "r") as zin:
        return {info.filename: zin.read(info.filename) for info in zin.infolist()}


def parse_document_root(entries: dict[str, bytes]):
    if "word/document.xml" not in entries:
        raise SystemExit("word/document.xml not found in input docx")
    return ET.fromstring(entries["word/document.xml"])


def write_docx_entries(path: Path, entries: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as zout:
        for name, data in entries.items():
            zout.writestr(name, data)


def write_minimal_docx(path: Path, paragraphs: list[str]):
    document_xml = build_document_xml(paragraphs)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
""",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
""",
        )
        zf.writestr("word/document.xml", document_xml)


def build_document_xml(paragraphs: list[str]) -> str:
    body = []
    for text in paragraphs:
        body.append("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % xml_escape(text))
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    %s
    <w:sectPr/>
  </w:body>
</w:document>
""" % "".join(body)


def xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def elem(tag: str):
    return ET.Element(qn(tag))


def child(parent, tag: str):
    return ET.SubElement(parent, qn(tag))


def qn(tag: str) -> str:
    prefix, local = tag.split(":")
    if prefix == "w":
        return "{%s}%s" % (W_NS, local)
    if prefix == "xml":
        return "{%s}%s" % (XML_NS, local)
    raise ValueError("unsupported namespace prefix: %s" % prefix)


def strip_trailing_colon(text: str) -> str:
    return text.rstrip().rstrip(":")


def sanitize_bookmark_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", name)


def is_auto_token(value: str | None) -> bool:
    return value is None or value.lower() == "auto"


def safe_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        raise SystemExit(str(exc))
