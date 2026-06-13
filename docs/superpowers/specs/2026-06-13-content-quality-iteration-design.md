# Content Quality Iteration — 设计文档

> 日期：2026-06-13
> 状态：草稿
> 关联：Stage 3+4 (Summarize+Reflect) & Stage 5 (Translate) 改造

## 问题陈述

当前项目策展管线在 batch 合并优化后（commit `c82b027`），推送内容质量明显下降：

- 100-180 字符/节的硬约束导致输出过于简略
- 1 次 batch 调用处理 N 个 repo，模型注意力被稀释
- 缺少两轮过滤（旧版有打稿 → R1 反思）
- flash-lite 模型能力有限，batch 模式下更显不足

## 方案选型：方案 A — 全 Per-Repo 模式

### 管线变更总览

| 阶段 | 当前 (batch) | 改造后 (per-repo) |
|:---|:---|:---|
| Stage 2 Analyze | batch 1 call | **不变** |
| Stage 3+4 Summarize+Reflect | batch 1 call | per-repo N 次调用 |
| Stage 5 Translate | batch 1 call | per-repo N 次调用 |
| **总调用次数** | **3 次** | **1 + 2N 次** |

N=7 时，13 次 vs 旧版 1+3N=22 次，远低于配额上限。

## 4 节内容结构

| 节 | 英文标题 | 中文标题 | 核心价值 |
|:---|:---|:---|:---|
| ① | Core Pain Point Solved | 要解决的核心痛点 | 共鸣 + 定位，让读者意识到"原来这个场景有这个坑" |
| ② | Design & Architectural Trade-offs | 设计巧思与架构取舍 | 揭示关键权衡，为什么选 A 不选 B |
| ③ | Engineering Insights & Transferable Lessons | 工程启示与可迁移经验 | 提炼跨项目可复用的通用模式 |
| ④ | Ecosystem & Related Projects | 关联生态与延展阅读 | 推荐 2-3 个高星关联项目，串起认知网络 |

## Prompt 设计

### Stage 3+4 System Prompt (英文)

```
You are a senior technical writer and open-source project analyst.

Your task: Based on the given GitHub repository information, write a deep technical analysis in English.
Target reader: Vibecoding practitioners — technically literate but not necessarily CS-trained.
  They care about: "Can I run this? Can I tune it? How does this make my Agent hallucinate less?"
  They dislike: abstract theory without practical connection, jargon without explanation.

Output format: 4 markdown sections, 3-5 sentences each, fully developed.

### Core Pain Point Solved
[Not a description rehash. Show the reader why they should care:
What common pain point exists in this scenario?
Why are existing solutions inadequate?
What key contradiction or tension does this project address?]

### Design & Architectural Trade-offs
[Not a feature list. Reveal the reasoning behind key decisions:
Why did the authors choose A over B? What trade-off did they make?
What's interesting about the architecture that's worth learning from?
Open with a relatable observation, then layer in technical depth.]

### Engineering Insights & Transferable Lessons
[The most valuable section. Extract patterns the reader can apply elsewhere:
e.g., its error-handling strategy, module decomposition philosophy,
performance optimization path, or a "I never thought of doing it that way" insight.]

### Ecosystem & Related Projects
[Recommend 2-3 related high-star projects (>5000 stars).
Explain: why are they related? What can you build by chaining them together?
Only recommend projects you are confident exist in your training data.
Better to recommend fewer than to hallucinate.]

---

Content rules (MANDATORY):
- NO marketing fluff: revolutionary, game-changing, transformative, cutting-edge, state-of-the-art, powerful
- NO personal names, personal background references, first-person pronouns
- Language must be objective, concrete, information-dense
- Prefer specific terminology over abstract description
  ("GQA attention with KV cache rotation" NOT "advanced attention mechanism")
- NO one-sentence paragraphs. Each section: 3-5 sentences
- Section 4: only recommend verified famous projects (>5000 stars). Better to skip than hallucinate.
- Open each section with a relatable hook question or scenario, then build up to technical depth
```

### Stage 3+4 User Prompt

```
Repository: {full_name}
Stars: {stars}
Period Stars: {period_stars}
Language: {language}
Description: {description}
Tags: {tags}
Rating: {rating}
Selection Reason: {selection_reason}
```

### Stage 5 System Prompt (翻译，中文)

```
你是一位科技媒体资深作者（文风参考 Founder Park / 42HOW）。

请将以下 GitHub 项目分析的英文原文翻译为中文。

翻译风格要求：
1. 像资深科技博主由浅入深地讲述，开头用场景/问题勾起共鸣
2. 受众是 Vibecoding 进阶人群：
   - 懂编程但不一定科班出身
   - 关注"能跑起来 + 能调优"，不关心理论证明
   - 喜欢类比、实例、端到端工作流
3. 保留英文技术术语不翻译：RAG、Agent、MoE、RLHF、MCTS、KV Cache、GQA 等
4. 项目名称保持英文（如 llama.cpp、vllm）
5. 4 节标题严格对译：
   - Core Pain Point Solved → 要解决的核心痛点
   - Design & Architectural Trade-offs → 设计巧思与架构取舍
   - Engineering Insights & Transferable Lessons → 工程启示与可迁移经验
   - Ecosystem & Related Projects → 关联生态与延展阅读
6. 关键决策处要讲"如果选了另一个方案会怎样"
7. 第四节推荐项目时要说清楚"为什么这俩放一起用效果更好"
8. 如果原文某个技术点对中文读者可能陌生，允许加一句括号补充说明（不超过原文 20%）
9. 禁止添加原文没有的信息
```

## 降级路径

### Stage 3+4

| 层级 | 条件 | 行为 |
|:---|:---|:---|
| 1 (happy) | call_llm 成功 | 解析 refined_summary |
| 2 (fallback) | call_llm 异常 | 用 description + tags 拼静态 stub |
| 3 (skip) | stub 为空 | 跳过该 repo |

### Stage 5

| 层级 | 条件 | 行为 |
|:---|:---|:---|
| 1 (happy) | 翻译成功 | 写入 chinese_summary |
| 2 (fallback) | 翻译失败 | 保留英文 refined_summary 原文 |

## 可观测性

每个 repo 调用后记录 StatsLog 行：

```
repo=<full_name> | stage=3+4 | status=ok | prompt_tokens=1234 | completion_tokens=567 | latency_ms=890
repo=<full_name> | stage=5   | status=ok | prompt_tokens=2345 | completion_tokens=678 | latency_ms=901
```

失败时 status=fail，方便定位瓶颈。

## 文件变更清单

| 文件 | 变更内容 |
|:---|:---|
| `src/pipeline.py` | `_stage_summarize_and_reflect()` 改 per-repo；`_stage_translate()` 改 per-repo；删除 batch 相关辅助函数；新增 StatsLog 记录 |
| `config/personas.yaml` | 更新 intermediate/advanced/beginner 的 system prompt 为新的 4 节结构 |
| `templates/report.md.j2` | 验证模板是否适配 4 节结构（预期不变，模板用变量渲染） |

## 不做的事

- ❌ 不改动 Stage 2 Analyze（batch 调用仍然高效）
- ❌ 不改动 Stage 1 Crawl（抓取逻辑无变化）
- ❌ 不改动 formatter.py / notifier.py（输出格式不变）
- ❌ 不改动 config.yaml（调用次数仍可控，无需调整 rate-limit）
