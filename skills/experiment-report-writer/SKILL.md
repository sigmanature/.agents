---
name: experiment-report-writer
description: Write office-style experiment reports and status-update modules in Markdown for different leadership audiences. Use this whenever the user asks for 技术汇报、阶段汇报、领导汇报、办公口吻文档、结论先行总结、技术结果说明、问题与下一步计划，especially when the same data needs different wording for technical leaders versus department leaders, or when the user wants one version for自己阅读 and one version for上报. Always produce a concise conclusion-first module, prefer charts before tables, and consult the matching file under roles/ before drafting.
---

# Experiment Report Writer

Use this skill to turn experiment data and engineering context into a concise report module that is suitable for leadership communication or self-study notes.

## Output contract

Always output Markdown first.

The Markdown must be easy to convert into other document formats, so keep the structure simple:

- short heading hierarchy
- plain Markdown tables
- image links for figures
- no raw sampling dumps in the report body

## Core writing rules

Follow these rules in order:

1. Lead with conclusions, then evidence, then issues and next steps.
2. Keep process detail selective. Include only the level of detail needed for the target audience to understand the result, confidence, and decision points.
3. In the data section, prefer figures first and tables second.
4. Never paste raw collected data into the report body.
5. When explaining a trend, always pair it with a short interpretation:
   - what changed
   - how strong the change is
   - what variable likely caused it
6. For the issue section, avoid absolute wording unless the evidence is definitive.
7. For the next-step section, write in a way that supports discussion and guidance rather than pretending all uncertainty is gone.

## Audience routing

Before writing, choose one role file:

- `roles/technical_leader.md`
- `roles/department_leader.md`
- `roles/self_reader.md`

If the user names a leader type, use the matching file directly.
If not, infer from context and say which one you chose.

If the user asks for "一式两份", produce:

1. a self-reader version that explains metric meanings and interpretation
2. a leadership version that keeps the body concise and conclusion-first

## Recommended module structure

Use this structure unless the user asks for a different one:

```md
# [Module Title]

## 结论概述
- 2-4 bullets, conclusion first

## 数据与趋势
- 1 short paragraph summarizing the trend
- figures
- summary table
- 1 short paragraph on variable attribution

## 当前问题与判断
- 1 short summary sentence
- compact table: problem / current judgment / impact

## 下一步计划与请教点
- 2-4 bullets
```

## Data-module rules

When experiment data is available:

- generate or reuse figures if possible
- include only summary metrics that help the argument
- use a Markdown table for side-by-side comparison
- if there is a baseline and a changed-variable group, make the comparison explicit
- if a run already ended, state the effective observation window
- for self-reader versions, explicitly define any nontrivial metric or term before using it

### Metric explanation rule

For self-reader versions, explain at least:

- what the metric means
- how it is computed
- what one sampling window means in the current experiment
- why cumulative and window-level views can tell different stories

For leadership versions, include metric explanations only when the term is not self-evident or when misunderstanding would distort the conclusion.

For trend language, prefer wording like:

- "整体低于/高于 baseline"
- "后段趋于平稳，但存在偶发尖峰"
- "当前判断与变量变更存在相关性，但仍需更多对照验证"

## Problem and next-step rules

Use a compact table for open issues when it helps:

```md
| 问题 | 当前判断 | 后续动作 |
|---|---|---|
```

When writing next steps:

- separate "planned action" from "want feedback"
- make room for leadership guidance
- for technical leaders, ask sharper technical questions
- for department leaders, tie actions back to delivery and business impact

## Bundled resources

- `roles/technical_leader.md`
- `roles/department_leader.md`
- `roles/self_reader.md`
- `templates/report_module_template.md`
- `scripts/plot_thp_dual_report.py`
