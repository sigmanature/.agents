import type { Plugin } from "@opencode-ai/plugin"

const SEARCH_TOOLS = new Set([
  "grep",
  "glob",
  "ast_grep_search",
  "ast_grep_replace",
  "lsp_find_references",
  "lsp_goto_definition",
  "lsp_symbols",
  "read",
  "bash",
  "task",
  "webfetch",
  "websearch_web_search_exa",
  "grep_app_searchGitHub",
  "context7_query-docs",
])

const FORMAT_RULES = `
## 输出格式规范（强制执行）

当你进行了代码搜索/证据收集操作后向用户汇报时，必须严格遵守以下格式。

### 1. 证据完整性要求
- **穷尽搜索后才汇报**：必须确认已搜索所有相关位置（不同模块、不同调用路径、上游/下游），不得在只找到一个匹配时就说"找到了"
- 如果搜索结果较多，明确告知搜索覆盖范围：搜了哪些文件/模块，覆盖了哪些模式

### 2. 代码证据格式
每个代码证据必须包含：
\`\`\`
文件: /path/to/file.ext:行号
[展示目标行前后各 5-10 行的上下文，用注释标记关键行]

调用栈上下文（如适用）：
  caller_func()            ← 调用者
    → callee_func()        ← 被调用者（重点分析）
      → helper()           ← 进一步调用
\`\`\`

### 3. 代码与分析的交叉对比（必须做）
每给出一段代码证据后，立即紧接分析段落：
- 这段代码的具体逻辑说明了什么
- 与你前面给出的分析推论是否一致
- **如果代码证据与你的初步分析有矛盾，必须指出矛盾并修正分析**
- 用交叉对比来验证你自己的分析是否正确（这能帮助你发现分析错误）

### 4. 分析段落必须以 "原因分析:" 开头
所有原因分析/逻辑推演段落必须以 "原因分析:" 开头。
示例：
  原因分析: f2fs_write_begin() 中在获取 page lock 之前没有检查 inline data 状态，导致...

### 5. 禁止的行为
- 禁止：只说"找到了相关代码"但不贴代码行号和内容
- 禁止：摘一行代码不提供上下文
- 禁止：给出分析结论但不标注对应的代码证据
- 禁止：没搜全就下结论
`

const TOOL_REMINDER =
  "\n\n> [格式提醒] 汇报时请: 贴代码行号+上下文 / 标注调用栈 / 交叉对比代码与分析 / 以\"原因分析:\"开头"

export const SummaryFormatterPlugin: Plugin = async (_ctx) => {
  return {
    "experimental.chat.system.transform": async (_input, output) => {
      output.system.push(FORMAT_RULES)
    },

    "tool.execute.after": async (input, output) => {
      if (SEARCH_TOOLS.has(input.tool) && output.output && output.output.length > 200) {
        output.output += TOOL_REMINDER
      }
    },
  }
}
