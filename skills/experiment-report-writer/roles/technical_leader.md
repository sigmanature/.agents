# Technical Leader Style

Use this style for leaders who care about both outcome and technical reasoning.

## Tone

- office-style and concise
- technically precise
- willing to surface uncertainty
- appropriate to ask for guidance on mechanism or experiment design

## Emphasis

Prioritize in this order:

1. conclusion and data signal
2. experiment variable and attribution logic
3. mechanism-level judgment
4. concrete next actions and technical questions

## Detail level

Include:

- key experiment setup needed to interpret the result
- comparison against baseline
- important stability or tooling caveats
- selective process detail when it changes confidence in the result

Do not include:

- full trial-and-error history
- long raw logs
- exhaustive debugging chronology
- internal correction language such as "不再使用", "不是而是", "不能再看", or "口径已经修正"; write the final external-facing conclusion directly
- repeated-test/process wording such as "复测", "baseline", "smoke", or "50 轮专项" unless the audience specifically needs experiment provenance
- raw counter or tracepoint enum names in main tables when a short human-readable cause explains the same signal
- agent-internal analysis labels such as "叶子栈", "strict", or "no coarse"; use audience-facing terms such as "调用栈分布" and "按子原因统计"

## Presentation rules for technical reports

- Treat charts and tables as decision aids: emphasize the dominant 1-3 causes in bold and keep secondary rows compact.
- When showing cause ratios, make the denominator explicit in plain language, for example "在 VMA 边界失败样本内占比".
- Use code snippets only at mechanism pivots where the reader must understand why the data means what it means.
- If a sampled trace distribution is paired with an absolute counter, state that once near the first chart and avoid repeating tooling caveats.
- Prefer direct mechanism wording over debug counter names; for example use "VMA 边界不覆盖完整 16KB folio" instead of internal reason identifiers.

## Suggested phrasing

Prefer phrases such as:

- "当前结论是"
- "从本轮数据看"
- "结合变量变更，当前判断"
- "这里仍有两个需要进一步确认的点"
- "这部分希望再请教您的判断"

## Technical-leader closing pattern

End with 1-3 concrete consultative prompts, for example:

- whether the current attribution is technically reasonable
- whether the next experiment dimension is chosen correctly
- whether to prioritize stability isolation or workload expansion next
