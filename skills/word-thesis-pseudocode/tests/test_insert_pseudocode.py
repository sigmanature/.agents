import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "insert_pseudocode.py"
DEMO_SCRIPT = SKILL_DIR / "scripts" / "generate_acceptance_demo.py"


class InsertPseudocodeTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="word-pseudocode-test-"))
        self.input_docx = self.tmpdir / "input.docx"
        self.output_docx = self.tmpdir / "output.docx"
        self.alg_path = self.tmpdir / "sample.alg"

        self._write_minimal_docx(
            self.input_docx,
            ["前文。", "{{ALG:demo}}", "后文。"],
        )

        self.alg_path.write_text(
            "\n".join(
                [
                    "@algorithm id=demo chapter=3 index=2 title=Large Folio Dispatch",
                    "",
                    "Input: folio, state",
                    "Output: dispatch target",
                    "",
                    "for each candidate in dispatch_table:",
                    "    if match(candidate, folio):",
                    "        return candidate.handler",
                    "return default_handler",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_replaces_placeholder_with_algorithm_block(self):
        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--input",
                str(self.input_docx),
                "--alg",
                str(self.alg_path),
                "--output",
                str(self.output_docx),
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue(self.output_docx.exists())

        root = self._read_document_xml(self.output_docx)
        paragraphs = self._paragraph_texts(root)
        self.assertNotIn("{{ALG:demo}}", paragraphs)
        self.assertIn("算法 3-2 Large Folio Dispatch", paragraphs)

        tables = root.findall(".//w:tbl", NS)
        self.assertEqual(len(tables), 1)
        rows = tables[0].findall("./w:tr", NS)
        first_cells = rows[0].findall("./w:tc", NS)
        last_cells = rows[-1].findall("./w:tc", NS)
        self.assertEqual(self._node_text(first_cells[0]).strip(), "1")
        self.assertEqual(self._node_text(first_cells[1]).strip(), "Input: folio, state")
        self.assertEqual(self._node_text(last_cells[1]).strip(), "return default_handler")

    def test_supports_all_phases_in_one_pass(self):
        input_docx = self.tmpdir / "full-input.docx"
        output_docx = self.tmpdir / "full-output.docx"
        auto_alg = self.tmpdir / "auto.alg"
        write_alg = self.tmpdir / "write.alg"
        latex_alg = self.tmpdir / "latex.alg"

        self._write_minimal_docx(
            input_docx,
            [
                "3 系统设计",
                "{{LOA}}",
                "见{{ALGREF:auto-dispatch}}。",
                "{{ALG:auto-dispatch}}",
                "4 详细设计",
                "{{ALG:write-path}}",
                "{{ALG:latex-demo}}",
            ],
        )

        auto_alg.write_text(
            "\n".join(
                [
                    "@algorithm id=auto-dispatch chapter=auto index=auto title=Auto Dispatch",
                    "",
                    "Input: folio, state",
                    "if need_dispatch:",
                    "    return fast_path",
                    "return slow_path",
                ]
            ),
            encoding="utf-8",
        )
        write_alg.write_text(
            "\n".join(
                [
                    '@algorithm id=write-path chapter=auto index=auto title=\"Write Path\"',
                    "",
                    "Input: inode, pos",
                    "Output: status",
                    "for each bio in batch:",
                    "    submit bio",
                    "return success",
                ]
            ),
            encoding="utf-8",
        )
        latex_alg.write_text(
            "\n".join(
                [
                    "@algorithm id=latex-demo chapter=auto index=auto title=Latex Alias Demo",
                    "",
                    "\\Input inode, folio",
                    "\\Output dispatch result",
                    "\\For each candidate in dispatch_table",
                    "    \\If match(candidate, folio)",
                    "        \\Return candidate.handler",
                    "    \\Else",
                    "        scan next candidate",
                    "    \\EndIf",
                    "\\EndFor",
                    "\\Return default_handler",
                ]
            ),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--input",
                str(input_docx),
                "--alg",
                str(auto_alg),
                "--alg",
                str(write_alg),
                "--alg",
                str(latex_alg),
                "--output",
                str(output_docx),
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        root = self._read_document_xml(output_docx)
        paragraphs = self._paragraph_texts(root)
        full_text = "\n".join(paragraphs)

        self.assertIn("算法目录", paragraphs)
        self.assertIn("算法 3-1 Auto Dispatch", full_text)
        self.assertIn("算法 4-1 Write Path", full_text)
        self.assertIn("算法 4-2 Latex Alias Demo", full_text)
        self.assertIn("见算法 3-1。", paragraphs)
        self.assertNotIn("{{LOA}}", full_text)
        self.assertNotIn("{{ALGREF:auto-dispatch}}", full_text)

        tables = root.findall(".//w:tbl", NS)
        self.assertEqual(len(tables), 4)
        loa_borders = self._table_border_values(tables[0])
        self.assertEqual(loa_borders["top"], "nil")
        self.assertEqual(loa_borders["bottom"], "nil")
        self.assertEqual(loa_borders["insideH"], "nil")
        self.assertEqual(loa_borders["insideV"], "nil")

        algo_borders = self._table_border_values(tables[1])
        self.assertEqual(algo_borders["top"], "single")
        self.assertEqual(algo_borders["bottom"], "single")
        self.assertEqual(algo_borders["insideV"], "single")
        self.assertEqual(algo_borders["left"], "nil")
        self.assertEqual(algo_borders["right"], "nil")
        self.assertEqual(algo_borders["insideH"], "nil")

        latex_rows = tables[-1].findall("./w:tr", NS)
        latex_texts = [
            self._node_text(row.findall("./w:tc", NS)[1]).strip()
            for row in latex_rows
        ]
        self.assertEqual(latex_texts[0], "Input: inode, folio")
        self.assertEqual(latex_texts[1], "Output: dispatch result")
        self.assertEqual(latex_texts[2], "for each candidate in dispatch_table:")
        self.assertEqual(latex_texts[3], "if match(candidate, folio):")
        self.assertEqual(latex_texts[4], "return candidate.handler")
        self.assertEqual(latex_texts[5], "else:")
        self.assertEqual(latex_texts[-1], "return default_handler")

        first_code_run = tables[1].find("./w:tr/w:tc[2]/w:p/w:r", NS)
        self.assertIsNotNone(first_code_run)
        self.assertIsNotNone(first_code_run.find("./w:rPr/w:noProof", NS))
        self.assertEqual(
            [child.tag.split("}")[-1] for child in list(first_code_run)[:2]],
            ["rPr", "t"],
        )

    def test_generates_acceptance_directory(self):
        output_dir = self.tmpdir / "acceptance-pack"
        result = subprocess.run(
            [
                "python3",
                str(DEMO_SCRIPT),
                "--output-dir",
                str(output_dir),
            ],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertTrue((output_dir / "01-input.docx").exists())
        self.assertTrue((output_dir / "02-output.docx").exists())
        self.assertTrue((output_dir / "00-README.md").exists())
        self.assertTrue((output_dir / "algorithms" / "auto-dispatch.alg").exists())

    def _write_minimal_docx(self, path: Path, paragraphs: list[str]):
        document_xml = self._build_document_xml(paragraphs)
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

    def _build_document_xml(self, paragraphs: list[str]) -> str:
        body = []
        for text in paragraphs:
            body.append(
                "<w:p><w:r><w:t>{}</w:t></w:r></w:p>".format(
                    self._xml_escape(text)
                )
            )
        return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {}
    <w:sectPr/>
  </w:body>
</w:document>
""".format(
            "".join(body)
        )

    def _read_document_xml(self, path: Path):
        with zipfile.ZipFile(path) as zf:
            data = zf.read("word/document.xml")
        return ET.fromstring(data)

    def _paragraph_texts(self, root):
        return [self._node_text(p) for p in root.findall(".//w:p", NS)]

    def _node_text(self, node) -> str:
        return "".join(t.text or "" for t in node.findall(".//w:t", NS))

    def _table_border_values(self, table):
        border_root = table.find("./w:tblPr/w:tblBorders", NS)
        values = {}
        for edge in ["top", "left", "bottom", "right", "insideH", "insideV"]:
            node = border_root.find(f"./w:{edge}", NS)
            values[edge] = node.get(self._qn("w:val")) if node is not None else None
        return values

    def _xml_escape(self, text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _qn(self, tag: str) -> str:
        prefix, local = tag.split(":")
        if prefix == "w":
            return "{%s}%s" % (NS["w"], local)
        raise ValueError(tag)


NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


if __name__ == "__main__":
    unittest.main()
