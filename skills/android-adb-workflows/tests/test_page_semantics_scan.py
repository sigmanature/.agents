import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path.home() / ".agents" / "skills" / "android-adb-workflows" / "scripts" / "page_semantics_scan.py"


def load_module():
    spec = importlib.util.spec_from_file_location("page_semantics_scan", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module from {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


READELF_SAMPLE = """\
There are 13 section headers, starting at offset 0x3b0a330:

Section Headers:
  [Nr] Name              Type            Address          Off    Size   ES Flg Lk Inf Al
  [ 0]                   NULL            0000000000000000 000000 000000 00      0   0  0
  [ 6] .rodata           PROGBITS        0000000000000680 000680 b07980 00   A  0   0  4
  [ 7] .text             PROGBITS        0000000000b08000 b08000 2e8c0e8 00  AX  0   0 16384
  [ 8] .data.img.rel.ro  PROGBITS        0000000003998000 3998000 008d2c 00   A  0   0 16384
  [ 9] .bss              NOBITS          00000000039a4000 000000 074f6c 00   A  0   0 16384
  [10] .dex              NOBITS          0000000003a1c000 000000 0dd998 00   A  0   0 16384
"""


class PageSemanticsScanTests(unittest.TestCase):
    def test_parse_readelf_sections_and_map_offset(self) -> None:
        mod = load_module()
        sections = mod.parse_readelf_sections(READELF_SAMPLE)
        self.assertEqual(sections[0].name, ".rodata")
        self.assertEqual(sections[1].name, ".text")
        mapped = mod.map_offset_to_section(0x1B64014, sections)
        self.assertIsNotNone(mapped)
        self.assertEqual(mapped.name, ".text")
        self.assertTrue(mapped.is_executable)
        self.assertEqual(mapped.section_offset, 0x105C014)

    def test_cluster_pages_allows_one_clean_page_gap(self) -> None:
        mod = load_module()
        pages = [
            0x1B62000,
            0x1B64000,
            0x1B66000,
            0x1B68000,
            0x1B6A000,
            0x1B6C000,
            0x299F000,
        ]
        clusters = mod.cluster_page_offsets(pages, page_size=4096, max_gap_pages=1)
        self.assertEqual(len(clusters), 2)
        self.assertEqual(clusters[0].start_offset, 0x1B62000)
        self.assertEqual(clusters[0].end_offset, 0x1B6C000)
        self.assertEqual(clusters[0].page_count, 6)
        self.assertEqual(clusters[0].max_gap_pages, 1)
        self.assertEqual(clusters[1].page_count, 1)


if __name__ == "__main__":
    unittest.main()
