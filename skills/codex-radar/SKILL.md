# Codex Radar Skill

实时检测 Codex 各模型当前智力水平，帮助决策是否使用 Codex。

数据来源：[Codex Reset Radar](https://codex-reset-radar.pages.dev/)，基于每日固定 DeepSWE 12 题评测集。

---

## 触发条件

当用户提到以下任意关键词时使用本 skill：
- "codex 雷达" / "codex IQ" / "codex 智力" / "codex 智商"
- "codex 现在怎么样" / "codex 能用吗" / "codex 好用吗"
- "check codex" / "codex status" / "codex intelligence"
- 用户考虑是否使用 Codex 时

---

## 工作流程

### Main Workflow

1. **获取数据**：`webfetch` `https://codex-reset-radar.pages.dev/current.json`
2. **解析关键信息**：
   - 最新 IQ 分数（主模型 GPT-5.5 xhigh）
   - 状态颜色（green/yellow/red）
   - 通过率（x/12）
   - 其他 reasoning effort 对比（high, medium, GPT-5.4 xhigh）
   - 近 10 天 IQ 趋势
   - 额度雷达（20x Pro 5h/7d）
   - 重置窗口预测概率
   - Tibo 活跃度（官方动态数）
3. **输出决策建议**：根据评分给出一句话建议
4. **格式化输出**：按下方输出模板显示

### 决策表

| IQ 分数 | 状态 | 建议 |
|---------|------|------|
| ≥100 | 🟢 绿色 | 推荐使用，智力正常偏高 |
| 75–87.5 | 🟡 黄色 | 可用，但会有较多返工 |
| ≤62.5 | 🔴 红色 | 建议避免复杂任务，或等窗口重置 |

---

## 输出模板

```
═══════════════════════════════════
  Codex 雷达 — {日期} {am/pm}
═══════════════════════════════════

🧠 GPT-5.5 xhigh IQ: {score} {status_emoji} ({passed}/12)
   high: {high_score} | medium: {med_score}
   GPT-5.4 xhigh: {gpt54_score}

📊 近 7 天趋势:
   {trend_chart}

💰 额度 (20x Pro): 5h=${quota_5h} | 7d=${quota_7d}

🔮 重置窗口预测: {probability_24h*100}% (24h) / {probability_48h*100}% (48h)
   等级: {level} → {summary_short}

🏠 Tibo: {official_updates}条官方动态 (24h), incidents={incidents}

💡 建议: {recommendation}

🔗 https://codex-reset-radar.pages.dev/
```

### 输出示例

```
═══════════════════════════════════
  Codex 雷达 — 2026-06-24 pm
═══════════════════════════════════

🧠 GPT-5.5 xhigh IQ: 125.0 🟢 (10/12)
   high: 87.5 🟡 | medium: 75.0 🔴
   GPT-5.4 xhigh: 87.5 🟡

📊 近 7 天趋势:
   6.18:125 | 6.19:100 | 6.20:75 | 6.21:87.5 | 6.22am:100 | 6.22pm:50 🔴 | 6.23:125 | 6.24am:87.5 | 6.24pm:125 🟢

💰 额度 (20x Pro): 5h=$284.57 | 7d=$1,707.42

🔮 重置窗口预测: 18% (24h) / 34% (48h)
   等级: medium_low → "维持中低概率"

🏠 Tibo: 18条官方动态 (24h), incidents=0

💡 建议: 🟢 IQ 125 — 推荐使用！复杂度高的任务放心交给 Codex。

🔗 https://codex-reset-radar.pages.dev/
```

---

## 实现细节

### 数据解析

从 `current.json` 取以下字段：

```
model_iq.latest                    → 主模型最新 IQ
model_iq.latest.score              → IQ 分数
model_iq.latest.status             → green/yellow/red
model_iq.latest.passed             → 通过数
model_iq.latest.tasks              → 总任务数 (固定 12)
model_iq.comparisons.*.latest      → 各对比模型的 latest
model_iq.recent_days               → 近 10 天趋势
model_iq.quota_radar.rows[0]       → 20x Pro 额度
model_iq.quota_radar.trend         → 额度趋势
prediction.probability_24h         → 24h 窗口概率
prediction.probability_48h         → 48h 窗口概率
prediction.level                   → 预测等级
codex_environment.status_incidents_24h  → 事故数
codex_environment.official_updates_24h → 官方动态数
```

### 状态 emoji 映射

- `green` → 🟢
- `yellow` → 🟡
- `red` → 🔴

### 建议生成规则

| 状态 | IQ范围 | 建议文案 |
|------|--------|---------|
| green, ≥125 | | "🟢 IQ {score} — 推荐使用！复杂度高的任务放心交给 Codex。" |
| green, 100-124 | | "🟢 IQ {score} — 可用。常规任务没问题。" |
| yellow, 75-99 | | "🟡 IQ {score} — 勉强可用。预计有返工，简单任务可以，复杂任务考虑推迟。" |
| red, ≤62.5 | | "🔴 IQ {score} — 不推荐！降智严重，建议等窗口重置或用其他工具替代。" |

### 趋势图格式

取 `recent_days` 最近 10 条，格式：`MM.DD:score`，当日加状态 emoji。

---

## 注意事项

- `current.json` 每天更新两次（am/pm），北京时间约上午和下午各一次
- IQ 基于固定 12 题 DeepSWE 评测集，通过率 12 分为满分
- 额度数据来自 20x Pro 美区账号实测
- 窗口预测仅供参考，概率来自 Tibo 推文和社区信号分析
- 如果 `webfetch` 失败，直接告诉用户无法获取数据并建议访问网页
