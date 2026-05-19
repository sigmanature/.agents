#!/usr/bin/env python3

import argparse
from pathlib import Path

from insert_pseudocode import transform_docx, write_minimal_docx


DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parents[1] / "acceptance" / "word-visual-review"
)


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    alg_dir = output_dir / "algorithms"
    output_dir.mkdir(parents=True, exist_ok=True)
    alg_dir.mkdir(parents=True, exist_ok=True)

    input_docx = output_dir / "01-input.docx"
    output_docx = output_dir / "02-output.docx"
    readme = output_dir / "00-README.md"
    checklist = output_dir / "03-视觉验收清单.md"

    write_minimal_docx(
        input_docx,
        [
            "3 系统设计",
            "{{LOA}}",
            "正文中第一次引用见{{ALGREF:auto-dispatch}}。",
            "{{ALG:auto-dispatch}}",
            "4 详细设计",
            "{{ALG:write-path}}",
            "{{ALG:latex-demo}}",
            "正文中第二次引用见{{ALGREF:latex-demo}}。",
        ],
    )

    algorithms = {
        "auto-dispatch.alg": "\n".join(
            [
                "@algorithm id=auto-dispatch chapter=auto index=auto title=Auto Dispatch",
                "",
                "Input: folio, state",
                "if need_dispatch:",
                "    return fast_path",
                "return slow_path",
            ]
        ),
        "write-path.alg": "\n".join(
            [
                '@algorithm id=write-path chapter=auto index=auto title="Write Path"',
                "",
                "Input: inode, pos",
                "Output: status",
                "for each bio in batch:",
                "    submit bio",
                "return success",
            ]
        ),
        "latex-demo.alg": "\n".join(
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
    }

    alg_paths = []
    for filename, content in algorithms.items():
        path = alg_dir / filename
        path.write_text(content, encoding="utf-8")
        alg_paths.append(path)

    specs = []
    from insert_pseudocode import parse_algorithm_file  # local import keeps CLI lean

    for path in alg_paths:
        specs.append(parse_algorithm_file(path))
    transform_docx(input_docx, output_docx, specs)

    readme.write_text(
        "\n".join(
            [
                "# Word 视觉验收包",
                "",
                "打开顺序：",
                "",
                "1. 打开 `01-input.docx` 查看占位符基线。",
                "2. 打开 `02-output.docx` 查看算法目录、算法块和交叉引用效果。",
                "3. 对照 `03-视觉验收清单.md` 在 Word 中逐项检查。",
                "",
                "目录内容：",
                "",
                "- `algorithms/`：伪代码 DSL 源文件",
                "- `01-input.docx`：插入前文档",
                "- `02-output.docx`：插入后文档",
                "- `03-视觉验收清单.md`：视觉验收项",
                "",
                "这份样例覆盖：",
                "",
                "- 多算法一次插入",
                "- 自动章号与自动序号",
                "- 算法目录",
                "- `{{ALGREF:id}}` 交叉引用",
                "- 类 LaTeX 命令别名",
                "- 保守论文风格算法样式",
            ]
        ),
        encoding="utf-8",
    )

    checklist.write_text(
        "\n".join(
            [
                "# 视觉验收清单",
                "",
                "- `算法目录` 标题居中，位于第一个算法之前。",
                "- 算法目录中出现 `算法 3-1 Auto Dispatch`、`算法 4-1 Write Path`、`算法 4-2 Latex Alias Demo`，并且目录整体不再是重边框表格感。",
                "- 正文引用显示为 `算法 3-1` 和 `算法 4-2`，不再出现占位符。",
                "- 每个算法标题格式为 `算法 章号-序号 标题`。",
                "- 每个算法正文都是白底黑字两列算法框，但不应像整张 Excel 网格。",
                "- 左列是连续行号，右列是伪代码正文。",
                "- 正文算法块只保留上边线、下边线和行号分隔线，不应出现密集横线。",
                "- 算法正文不应再出现 Word 红色拼写波浪线。",
                "- `Latex Alias Demo` 中的 `\\Input`、`\\For`、`\\If`、`\\Else`、`\\Return` 已经转成普通论文式伪代码文本。",
                "- 算法块文本在 Word 中可以逐行选中和编辑，不是图片。",
            ]
        ),
        encoding="utf-8",
    )

    print(output_dir)
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Generate a Word pseudocode acceptance pack.")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory to write the demo pack into.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
