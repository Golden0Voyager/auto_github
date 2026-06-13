# Content Quality Iteration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite Stage 3+4 and Stage 5 from batch mode to per-repo mode with improved 4-section content structure and vibecoding-oriented writing style.

**Architecture:** `_summarize_reflect_batch()` and `_translate_batch()` removed entirely. `_stage_summarize_and_reflect()` and `_stage_translate()` simplified to directly iterate per-repo. `_summarize_reflect_per_repo()` and `_translate_per_repo()` get completely new prompts. `personas.yaml` prompt_focus updated. `MOCK_TRANSLATIONS` expanded to 4-section.

**Tech Stack:** Python, Jinja2 (templates unaffected), sensenova flash-lite LLM.

---

### Task 1: Update MOCK_TRANSLATIONS to 4-section format

**Files:**
- Modify: `src/pipeline.py:101-171`
- Test: `tests/test_pipeline.py:211-227`

**Background:** `MOCK_TRANSLATIONS` provides pre-authored Chinese mock content for --mock mode. Current version uses 3 sections. Needs 4 sections plus the new vibecoding writing style.

- [ ] **Step 1: Update MOCK_TRANSLATIONS entries**

Convert all 7 entries from the old 3-section structure:
```
### 核心解决的工程痛点
### 底层架构与工程设计
### 极客实战与工作流落地
```

To the new 4-section structure:
```
### 要解决的核心痛点
### 设计巧思与架构取舍
### 工程启示与可迁移经验
### 关联生态与延展阅读
```

Rewrite each entry in the new style: "Founder Park / 42HOW" senior tech blogger voice, by浅入深, with analogies and relatable hooks. Each section 3-5 sentences.

Here is the replacement block for `src/pipeline.py:101-171`:

```python
MOCK_TRANSLATIONS = {
    "deepseek-ai/DeepSeek-V3": """### 要解决的核心痛点

大模型做推理时，显存经常被 KV Cache 吃光——这是所有跑过 MoE 模型的人都踩过的坑。DeepSeek-V3 把 671B 总参数塞进推理管线时，如果按传统的多头注意力来算，KV Cache 会膨胀到几乎存不下任何 batch。这不是渐进式优化能解决的，需要对注意力机制的存储结构做根本性改造。

### 设计巧思与架构取舍

DeepSeek-V3 的选择是 MLA（Multi-head Latent Attention），把 Key 和 Value 压缩到一个低秩潜在空间里再做投影——本质上是拿一个小的计算开销换一个巨大的显存节省。配合无辅助损失的负载均衡策略，它绕过了传统 MoE 训练中"辅助 loss 拖累主任务"的老问题。另外 MTP（Multi-Token Prediction）让模型一次预测多个 token，生成吞吐直接翻倍。

### 工程启示与可迁移经验

MLA 的低秩投影思路是个值得记住的模式：当某个中间结果大到成为瓶颈时，不要直接砍数据量，而是找个好的压缩方式。这个思路不止适用于注意力机制——在任何需要缓存中间状态的系统里都能复用。另外，无辅助损失的负载均衡意味着"别让优化目标的副作用盖过主目标"，这是个在 Agent 系统设计里也常见的教训。

### 关联生态与延展阅读

搭配 **deepseek-ai/DeepSeek-R1** 使用效果最好——V3 负责生成，R1 负责推理验证，形成"快思考 + 慢思考"的双引擎架构。如果想理解 MLA 的底层实现细节，**lucidrains/MLA-pytorch** 提供了一个干净的可复现参考实现。""",

    "deepseek-ai/DeepSeek-R1": """### 要解决的核心痛点

大模型做复杂推理时经常"想一半就编答案"。传统的解法是喂海量人工标注的思维链数据做 SFT——但标注成本极高，而且标注质量直接决定天花板。R1 要证明的是：不需要人类手把手教，模型自己通过强化学习就能学会"怎么思考"。

### 设计巧思与架构取舍

R1 的激进之处在于：它在基座模型上直接跑强化学习（没有前置 SFT），只靠规则奖励信号就让模型自发产生了可读的反思链。它学会的不是具体问题的答案，而是一套"自我纠偏"的通用推理策略。更聪明的是将这种推理能力蒸馏到小模型（1.5B 到 72B）——让小模型也具备接近大模型的推理水平，成本却降了两个数量级。

### 工程启示与可迁移经验

"奖励设计比训练数据更重要"是 R1 带来的核心启示——在 Agent 系统里，设计正确的反馈信号远比准备海量示例样本高效。另外，蒸馏策略的通用性很强：如果某项能力在大模型上已经收敛，把它蒸馏到小模型通常比从小模型开始训练性价比高得多。

### 关联生态与延展阅读

R1 是 **deepseek-ai/DeepSeek-V3** 推理层的互补组件——V3 做生成，R1 做验证。如果跑本地部署，推荐搭配 **meta-llama/llama3** 的 8B 版本做基础嵌入，R1 蒸馏版做推理路由，是一个成本可控的"快慢协同"方案。""",

    "lucidrains/MLA-pytorch": """### 要解决的核心痛点

MLA（Multi-head Latent Attention）是 DeepSeek-V3 论文中最关键的结构创新，但原实现埋在 671B 参数的大工程里，普通开发者很难扒出来单独理解。想在 PyTorch 里做注意力机制的消融实验，需要一个干净、完整、不依赖大框架的参考实现。

### 设计巧思与架构取舍

这个库的核心贡献是把 MLA 中的"低秩解耦投影"分离成一个独立的模块——Query 向量单独投影，Key/Value 共享一个低秩压缩，然后通过 RoPE 做位置编码拼接。这样的解耦让研究者可以单独调投影秩的大小来观测对长文本检索精度的影响，而不需要部署整个大模型。严格复现意味着每一行代码都能对应回论文公式。

### 工程启示与可迁移经验

这个项目的存在本身就是个好习惯：当你读论文发现一个有趣的结构时，写一个干净的可复现实现放进开源社区。这种"解耦复现"策略的价值在于：它让研究者可以在玩具尺度上验证想法的有效性再决定是否投入大工程。在做 Agent 系统时也可以用同样的思路——先 mock 再上生产。

### 关联生态与延展阅读

作为 MLA 的独立参考实现，搭配 **deepseek-ai/DeepSeek-V3** 的论文一起阅读效果最佳。如果想看这个注意力变体在传统 Transformer 里的替代效果，可以跟 **huggingface/transformers** 的标准多头注意力实现做对照实验。""",

    "google-research/sima": """### 要解决的核心痛点

AI Agent 做过多的游戏只能在一个固定的虚拟世界里运行。每换一个 3D 环境，模型就得重新训练或者做大量适配。SIMA 的命题是：能不能训练一个 Agent，不管把它丢进哪个 3D 世界，它都能理解"物理"规则并正确交互？

### 设计巧思与架构取舍

SIMA 把视觉输入和鼠标/键盘动作空间统一映射到一个跨域表征上——不管游戏引擎是 Unity、Unreal 还是自研，底层给的画面都是像素，操作都是动作序列。它没有为每个游戏单独做适配，而是让模型自己学习"在不同物理引擎下的一致性行为"。ViT + GNN 的组合让视觉感知和物理常识推理在一个共同空间里对齐。

### 工程启示与可迁移经验

跨域泛化的思路值得借鉴：当你的 Agent 需要适配多个后端时，不要为每个后端写适配器，而是找到所有后端共有的抽象层（像素 / 动作序列 / API 格式），在那个抽象层上训练模型。这个"找共同抽象层"的思维模式在 MCP 服务设计、多平台工具集成等场景都能复用。

### 关联生态与延展阅读

SIMA 的跨域感知思路跟 **comfyanonymous/ComfyUI** 的 DAG 编排有相通之处——都是把复杂流程拆成通用原子节点。搭配使用可以探索"用 Agent 自动编排 ComfyUI 工作流"的方向。""",

    "meta-llama/llama3": """### 要解决的核心痛点

开源大模型在此前一直处在"能用但不放心用"的阶段——要么协议限制商用，要么性能跟闭源差一大截。LLaMA 3 的定位是给开源社区一个真正工业级的基座模型：性能接近 GPT-4 级别，协议允许商用，部署门槛可控。

### 设计巧思与架构取舍

128K token 的分词器和 GQA（Grouped Query Attention）是两个关键设计。GQA 在多头注意力和显存效率之间找到了一个实用的平衡点——不需要像 MLA 那样做低秩压缩，但比标准的 MHA 省了近一半的 KV Cache。数十万条精修指令的微调数据集则让模型在指令遵循和低幻觉率上有了质的提升。

### 工程启示与可迁移经验

LLaMA 3 展示了"足够好的基线模型比花哨的方案更重要"：它没有用 MoE 或者 MLA 这些激进架构，而是把 GQA、数据质量、指令微调这些基本功做到极致。对做项目的启示是：先确认基线是否已经足够，再考虑要不要上复杂架构。很多时候瓶颈不在架构而在数据质量。

### 关联生态与延展阅读

LLaMA 3 是整个开源 LLM 生态的基础设施。搭配 **huggingface/transformers** 做加载推理，配合本地 RAG 管道构建私有知识库。如果需要蒸馏 R1 级别的推理能力到小模型，LLaMA 3 的 8B 版本是做蒸馏目标的最佳选择之一。""",

    "huggingface/transformers": """### 要解决的核心痛点

在 transformers 出现之前，想用不同框架（PyTorch / TensorFlow / JAX）跑同一个模型，通常要写多份适配代码。社区里每个新模型都要重新实现一套训练和推理接口——重复造轮子成了常态，没有人把精力花在真正的模型创新上。

### 设计巧思与架构取舍

Transformers 库做了一个极其干净的抽象：模型架构和训练框架解耦。同一个模型配置可以一键在 PyTorch、TensorFlow、JAX 之间切换，不需要改模型代码。FlashAttention-2 和 QLoRA 的原生融合意味着在不改用户代码的前提下就能白嫖性能优化——这种"透明升级"的设计思路值得所有基础设施项目学习。

### 工程启示与可迁移经验

"好接口长在复用边界上"是 transformers 教给工程社区最重要的一课。它定义的 `from_pretrained` / `AutoModel` 等 API 模式现在已经是 NLP 行业的事实标准。如果你在做一个生态工具，不妨想想：用户最常做的 3 个操作是什么？把这 3 个操作做成一行能调用的 API，剩下的让用户自己组合。

### 关联生态与延展阅读

Transformers 是一切 LLM 工程的基础设施。搭配 **meta-llama/llama3** 做推理，配合 vLLM 做生产部署，是当前最主流的开源 LLM 工程栈。了解 FlashAttention-2、QLoRA 这些性能优化的底层实现能帮你榨干硬件性能。""",

    "comfyanonymous/ComfyUI": """### 要解决的核心痛点

传统的 Stable Diffusion WebUI 在处理复杂的多模型工作流时，操作界面会迅速变成一团乱麻——节点之间是面条式的连线，想复用某个子流程只能手动复制粘贴。ComfyUI 的解法是：把图像生成流程变成一张有向无环图（DAG），每个算子是一个独立节点，输入输出清晰可见。

### 设计巧思与架构取舍

DAG 拓扑的最大优势是可组合性：你可以把 ControlNet、IP-Adapter、LoRA 等所有组件都拖拽到画布上，自由决定它们的连接顺序，而不是被 WebUI 的固定流程限制。Python API 的支持更进一步——你可以在 Python 脚本里动态构造和修改整个图，实现批量生成时的"权重热插拔"。这种原子化设计让它不仅是一个 UI 工具，更是一个流程引擎。

### 工程启示与可迁移经验

DAG 这种拓扑结构不止适用于图像生成——任何需要编排多个独立步骤的工作流（数据处理、Agent 链、测试管线）都可以用同样的思路来设计。ComfyUI 证明了一个道理：好的流程控制工具应该让用户看得见每一步的状态，并能自由调整顺序而不破坏整体。

### 关联生态与延展阅读

ComfyUI 是 Stable Diffusion 工作流编排的核心工具。如果把 ComfyUI 的 DAG 编排思路跟 Agent 系统结合——用 Agent 动态决定节点连接顺序——就能实现自动化的图像生成管线。相关的 ControlNet、IP-Adapter 等插件进一步扩展了它的能力边界。""",
}
```

- [ ] **Step 2: Update the section assertion test**

In `tests/test_pipeline.py:222-226`, the test `test_translation_has_required_sections` checks for the old 3-section headers. Update to check for all 4 new headers:

```python
def test_translation_has_required_sections(self):
    for name, translation in MOCK_TRANSLATIONS.items():
        assert "### 要解决的核心痛点" in translation
        assert "### 设计巧思与架构取舍" in translation
        assert "### 工程启示与可迁移经验" in translation
        assert "### 关联生态与延展阅读" in translation
```

- [ ] **Step 3: Run tests to verify**

```bash
cd /Users/hainingyu/Code/auto_github && uv run python -m pytest tests/test_pipeline.py::TestMockTranslations -v
```

Expected: All 3 tests PASS (test_known_repos_have_translations, test_translation_has_required_sections).

---

### Task 2: Update personas.yaml with 4-section style guidance

**Files:**
- Modify: `config/personas.yaml:1-28`

**Background:** Current `prompt_focus` entries describe general interests but don't instruct the LLM on the 4-section structure or writing style. Each persona needs explicit 4-section guidance and the vibecoding tone.

- [ ] **Step 1: Replace personas.yaml content**

```yaml
# Target Personas & Prompts for AI Curation
# Each persona defines a writing style and content focus for the 4-section analysis.

beginner:
  name: "初阶入门者"
  description: "面向新手开发者、技术PM及AI爱好者。语言通俗易懂，重点放在「这个项目能为我提供什么现成工具或开箱即用的能力」，解释技术名词，避免复杂的数学公式或深奥架构。"
  prompt_focus: |
    Writing style: 像科技媒体资深博主由浅入深，用场景和问题开头勾起共鸣。
    4-section structure for each repo:
      - Core Pain Point Solved：用大白话讲"为什么你需要关心这个问题"，用生活化的类比降低理解门槛
      - Design & Architectural Trade-offs：讲设计决策就行，不需要深挖底层原理；重点放在"这比之前的方案好在哪"
      - Engineering Insights & Transferable Lessons：提炼一个"哦原来可以这样"的通用小技巧，让读者感觉学到了东西
      - Ecosystem & Related Projects：推荐 2-3 个可以一起用的项目，讲清楚"组合起来能做什么"
    Content rules:
      - 解释所有技术术语（如 Agent = 能自主执行任务的 AI 程序）
      - 强调 WebUI、一键安装、简单 API 调用等即插即用特性
      - 语言亲切、易懂、鼓舞人心
      - 【通用普适性规范】严禁提及个人姓名、个人背景或第一人称

intermediate:
  name: "中阶实践者"
  description: "面向系统化 LLM 学习者、提示词工程师与 Vibecoding 极客。热衷于 Agent 工作流、ComfyUI 精细干预、本地 RAG 降噪、MCP 插件开发、以及日常 Telemetry 遥测。关注实用工程落地、API 性能以及如何通过微交互和低认知负荷设计人机协同系统。"
  prompt_focus: |
    Writing style: 像 Founder Park / 42HOW 的资深作者，开篇用具体问题切入，中间自然过渡到技术内核，不掉书袋但把原理讲透。
    4-section structure for each repo:
      - Core Pain Point Solved：揭示读者也踩过的坑——"现有的方案为什么不够好"，建立认知共振
      - Design & Architectural Trade-offs：聚焦关键设计决策——为什么选 A 不选 B，权衡了什么。用"如果选了另一个方案会怎样"来展现深度
      - Engineering Insights & Transferable Lessons：提炼跨项目可复用的模式/原则/教训——"这个思路不止适用于这个项目，你在设计 Agent 系统时也能用上"
      - Ecosystem & Related Projects：推荐 2-3 个关联的高星项目（>5000 stars），说清楚"为什么这几个放一起用效果更好"
    Content rules:
      - 具体术语优先于抽象描述（"GQA attention with KV cache rotation" 而不是 "advanced attention mechanism"）
      - 用比喻降低理解门槛但不幼稚
      - 关注 Token 经济学、性能与成本平衡、死循环排查
      - 禁止营销词汇：revolutionary, game-changing, cutting-edge 等
      - 【通用普适性规范】严禁提及任何具体的个人姓名（如海宁、于海宁、Haining）、特定的个人简历背景（如当代艺术、MFA、某某美术馆等）或第一人称指代

advanced:
  name: "高阶大神"
  description: "面向资深架构师、算法研究员和底层性能极客。专注大模型底层原理、MoE 架构优化、MLA (Multi-head Latent Attention) 技术、强化学习 (RLHF/DPO/PRO)、推理期树搜索 (MCTS/RMaxTS)、以及 CUDA 算力与系统吞吐优化。追求极致的学术硬核度。"
  prompt_focus: |
    Writing style: 学术论文级别的严谨表达，用最小篇幅承载最大信息密度。直接进主题，不需要场景化铺垫。
    4-section structure for each repo:
      - Core Pain Point Solved：精确描述问题空间的数学/工程边界——在什么条件下这个问题会出现，传统方法的渐进复杂度是什么
      - Design & Architectural Trade-offs：以公式/复杂度分析/算力消耗数据支撑设计选择。对比的基线是什么？在什么 trade-off 曲线上选择了这个点
      - Engineering Insights & Transferable Lessons：提炼可迁移的底层优化范式——这个方案在更通用的意义上揭示了什么系统设计原则
      - Ecosystem & Related Projects：推荐同领域的 2-3 个关键项目，说清楚它们在技术栈中的位置和互补关系
    Content rules:
      - 使用规范的算法词汇
      - 深入探讨算力消耗与吞吐效率
      - 严谨、高密度、学术性强
      - 【通用普适性规范】严禁提及个人姓名、个人背景或第一人称
```

- [ ] **Step 2: Run tests to verify**

```bash
cd /Users/hainingyu/Code/auto_github && uv run python -m pytest tests/test_pipeline.py -v -k "init" 2>&1 | head -20
```

Expected: Persona loading tests PASS.

---

### Task 3: Rewrite Stage 3+4 to per-repo with new 4-section prompt

**Files:**
- Modify: `src/pipeline.py:491-622`

**Background:** `_stage_summarize_and_reflect()` currently tries batch first (`_summarize_reflect_batch`) then falls back to per-repo (`_summarize_reflect_per_repo`). After the change, it goes directly to per-repo iteration. The per-repo prompt needs the new 4-section structure and vibecoding style.

- [ ] **Step 1: Rewrite `_stage_summarize_and_reflect()`**

Replace the entire method (lines 491-516) and remove `_summarize_reflect_batch()` (lines 518-580):

```python
def _stage_summarize_and_reflect(self, repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stage 3+4: Per-repo summarization with the new 4-section + vibecoding style.

        One LLM call per repo. Falls back to static stub on failure.
        """
        print("\n=== Stage 3+4: Summarize & Reflect (逐仓库深度分析) ===")

        if self.use_mock or not self.llm.client:
            print("[Stage 3+4 Fallback] Bypassing LLM. Generating mock summaries offline.")
            result = []
            for r in repos:
                rc = r.copy()
                rc["refined_summary"] = MOCK_TRANSLATIONS.get(
                    r["full_name"],
                    self._summarize_reflect_stub(r)
                )
                rc["reflection_trace"] = ""
                result.append(rc)
            return result

        result = []
        for r in repos:
            print(f"  [{r['full_name']}] Generating analysis...")
            rc = self._summarize_reflect_per_repo(r)
            result.append(rc)

        print(f"[Stage 3+4 Done] Analyzed {len(result)} repositories.")
        return result
```

- [ ] **Step 2: Remove `_summarize_reflect_batch()`**

Delete the entire `_summarize_reflect_batch` method (lines 518-580).

- [ ] **Step 3: Rewrite `_summarize_reflect_per_repo()`**

Replace lines 582-622 with the new implementation:

```python
def _summarize_reflect_per_repo(self, r: Dict[str, Any]) -> Dict[str, Any]:
        rc = r.copy()
        system_prompt = (
            "You are a senior technical writer and open-source project analyst.\n\n"
            "Your task: Based on the given GitHub repository information, write a deep technical analysis in English.\n"
            "Target reader: Vibecoding practitioners \u2014 technically literate but not necessarily CS-trained.\n"
            "  They care about: 'Can I run this? Can I tune it? How does this make my Agent hallucinate less?'\n"
            "  They dislike: abstract theory without practical connection, jargon without explanation.\n\n"
            "Output format: 4 markdown sections, 3-5 sentences each, fully developed.\n\n"
            "### Core Pain Point Solved\n"
            "[Not a description rehash. Show the reader why they should care:\n"
            "What common pain point exists in this scenario?\n"
            "Why are existing solutions inadequate?\n"
            "What key contradiction or tension does this project address?]\n\n"
            "### Design & Architectural Trade-offs\n"
            "[Not a feature list. Reveal the reasoning behind key decisions:\n"
            "Why did the authors choose A over B? What trade-off did they make?\n"
            "What's interesting about the architecture worth learning from?\n"
            "Open with a relatable observation, then layer in technical depth.]\n\n"
            "### Engineering Insights & Transferable Lessons\n"
            "[The most valuable section. Extract patterns the reader can apply elsewhere:\n"
            "e.g., its error-handling strategy, module decomposition philosophy,\n"
            "performance optimization path, or a 'I never thought of doing it that way' insight.]\n\n"
            "### Ecosystem & Related Projects\n"
            "[Recommend 2-3 related high-star projects (>5000 stars).\n"
            "Explain: why are they related? What can you build by chaining them together?\n"
            "Only recommend projects you are confident exist in your training data.\n"
            "Better to recommend fewer than to hallucinate.]\n\n"
            "---\n"
            "Content rules (MANDATORY):\n"
            "- NO marketing fluff: revolutionary, game-changing, transformative, cutting-edge, state-of-the-art, powerful\n"
            "- NO personal names, personal background references, first-person pronouns\n"
            "- Language must be objective, concrete, information-dense\n"
            "- Prefer specific terminology over abstract description\n"
            "  ('GQA attention with KV cache rotation' NOT 'advanced attention mechanism')\n"
            "- NO one-sentence paragraphs. Each section: 3-5 sentences\n"
            "- Section 4: only recommend verified famous projects (>5000 stars). Better to skip than hallucinate.\n"
            "- Open each section with a relatable hook question or scenario, then build up to technical depth"
        )
        user_content = (
            f"Repository: {r['full_name']}\n"
            f"Stars: {r.get('stars', 0)}\n"
            f"Period Stars: {r.get('period_stars', '')}\n"
            f"Language: {r.get('language', '')}\n"
            f"Description: {r.get('description', '')}\n"
            f"Tags: {', '.join(r.get('tags', []))}\n"
            f"Rating: {r.get('rating', 'B')}\n"
            f"Selection Reason: {r.get('selection_reason', '')}"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            res = self.llm.call_llm(messages, use_reasoning=False, temperature=0.3)
            rc["refined_summary"] = res["content"]
        except Exception as e:
            print(f"[Stage 3+4 Warning] Failed for {r['full_name']}: {e}")
            rc["refined_summary"] = self._summarize_reflect_stub(r)
        rc["reflection_trace"] = ""
        return rc
```

- [ ] **Step 4: Add stub helper method**

After `_summarize_reflect_per_repo`, add the static stub method:

```python
def _summarize_reflect_stub(self, r: Dict[str, Any]) -> str:
        """Static fallback stub when LLM is unavailable for summarization."""
        desc = r.get("description", "Open-source engineering project") or "Open-source engineering project"
        return (
            f"### Core Pain Point Solved\n{desc}\n\n"
            f"### Design & Architectural Trade-offs\n"
            f"Standard implementation with community-driven design decisions. "
            f"Deeper LLM analysis pending for this cycle.\n\n"
            f"### Engineering Insights & Transferable Lessons\n"
            f"Refer to the project README and issue tracker for engineering discussions.\n\n"
            f"### Ecosystem & Related Projects\n"
            f"Explore related repositories via the project's dependency graph and GitHub Topics page."
        )
```

- [ ] **Step 5: Run tests to verify**

```bash
cd /Users/hainingyu/Code/auto_github && uv run python -m pytest tests/test_pipeline_batch.py::TestSummarizeReflectBatch -v 2>&1
```

Expected: These tests reference `_summarize_reflect_batch` which was deleted. Move to Task 5 (test updates).

---

### Task 4: Rewrite Stage 5 Translate to per-repo with new prompt

**Files:**
- Modify: `src/pipeline.py:624-727`

**Background:** Same pattern as Task 3 — `_stage_translate()` tries batch first, then per-repo. After change, goes directly to per-repo. Per-repo prompt gets the new vibecoding style.

- [ ] **Step 1: Rewrite `_stage_translate()`**

Replace lines 624-653:

```python
def _stage_translate(self, repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stage 5: Per-repo high-fidelity translation to Chinese.

        One LLM call per repo. Falls back to English text on failure.
        """
        print("\n=== Stage 5: Translate (逐仓库专业中文润色) ===")

        if self.use_mock or not self.llm.client:
            print("[Stage 5 Fallback] Bypassing LLM. Using mock translations.")
            result = []
            for r in repos:
                rc = r.copy()
                rc["chinese_summary"] = MOCK_TRANSLATIONS.get(
                    r["full_name"],
                    r.get("refined_summary", ""),
                )
                result.append(rc)
            return result

        result = []
        for r in repos:
            print(f"  [{r['full_name']}] Translating...")
            rc = self._translate_per_repo(r)
            result.append(rc)

        print(f"[Stage 5 Done] Translated {len(result)} repositories.")
        return result
```

- [ ] **Step 2: Remove `_translate_batch()`**

Delete `_translate_batch()` (lines 655-702).

- [ ] **Step 3: Rewrite `_translate_per_repo()`**

Replace lines 704-727:

```python
def _translate_per_repo(self, r: Dict[str, Any]) -> Dict[str, Any]:
        rc = r.copy()
        system_prompt = (
            "You are a senior tech media writer (style: Founder Park / 42HOW).\n"
            "Translate the following GitHub project English analysis into professional Chinese.\n\n"
            "Style requirements:\n"
            "1. Write like a seasoned tech blogger: start with a relatable hook or question, "
            "then build up to technical depth naturally\n"
            "2. Target audience: Vibecoding practitioners \u2014 "
            "they can code but may not have CS degrees. They care about 'does it work and can I tune it,' "
            "not theoretical proofs. Use analogies and end-to-end workflow examples.\n"
            "3. Keep industry-standard English terms untranslated: RAG, Agent, MoE, RLHF, MCTS, KV Cache, GQA, MLA\n"
            "4. Keep project names in English (e.g., llama.cpp, vllm)\n"
            "5. Strict Chinese section header mapping:\n"
            "   - Core Pain Point Solved \u2192 要解决的核心痛点\n"
            "   - Design & Architectural Trade-offs \u2192 设计巧思与架构取舍\n"
            "   - Engineering Insights & Transferable Lessons \u2192 工程启示与可迁移经验\n"
            "   - Ecosystem & Related Projects \u2192 关联生态与延展阅读\n"
            "6. For key design decisions, add 'what if they chose the other path' perspective\n"
            "7. For Section 4, explain why these projects work better together\n"
            "8. If a technical concept may be unfamiliar to Chinese readers, "
            "add a parenthetical note (max 20% of original length)\n"
            "9. Do NOT add information not present in the original text"
        )
        user_content = f"English Technical Analysis:\n\n{r.get('refined_summary', '')}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            res = self.llm.call_llm(messages, use_reasoning=False, temperature=0.2)
            rc["chinese_summary"] = res["content"]
        except Exception as e:
            print(f"[Stage 5 Warning] Translation failed for {r['full_name']}: {e}")
            rc["chinese_summary"] = r.get("refined_summary", "")
        return rc
```

- [ ] **Step 4: Run mock pipeline to verify**

```bash
cd /Users/hainingyu/Code/auto_github && uv run python src/main.py --mock --persona intermediate
```

Expected: Pipeline runs successfully. Check `reports/latest_daily.md` — all 7 mock repos should have 4-section Chinese content.

```bash
git checkout -- reports/repo_history.json reports/latest_daily.md
```

---

### Task 5: Update tests for removed batch methods

**Files:**
- Modify: `tests/test_pipeline_batch.py:73-161`

**Background:** Tests for `_summarize_reflect_batch` and `_translate_batch` need to be updated since those methods are removed. Some tests can be adapted to test per-repo paths instead. The per-repo success and failure tests already exist in `test_pipeline_batch.py:215-236`.

- [ ] **Step 1: Replace batch tests with direct per-repo tests**

Replace `test_pipeline_batch.py:73-161` (the `TestSummarizeReflectBatch` and `TestTranslateBatch` classes):

```python
class TestSummarizeReflectStage:
    """Test the per-repo Stage 3+4."""

    def test_stage_uses_per_repo_for_each(self, batch_config, batch_repos):
        """Stage 3+4 should call per-repo for each repo."""
        client = MagicMock()
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = True
        result = pipeline._stage_summarize_and_reflect(batch_repos)
        assert len(result) == len(batch_repos)
        for r in result:
            assert "refined_summary" in r


class TestTranslateStage:
    """Test the per-repo Stage 5."""

    def test_stage_translate_each_repo(self, batch_config, batch_repos):
        """Stage 5 should translate each repo."""
        client = MagicMock()
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = True
        result = pipeline._stage_translate(batch_repos)
        assert len(result) == len(batch_repos)
        for r in result:
            assert "chinese_summary" in r

    def test_translate_per_repo_success(self, batch_config):
        """Per-repo translation should work."""
        client = MagicMock()
        client.call_llm.return_value = {
            "content": "### 要解决的核心痛点\nTest translation."
        }
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = False

        repo = {"full_name": "test/repo", "refined_summary": "English.", "stars": 1000}
        result = pipeline._translate_per_repo(repo)
        assert "chinese_summary" in result
        assert "要解决的核心痛点" in result["chinese_summary"]

    def test_translate_per_repo_failure_keeps_english(self, batch_config):
        """When per-repo translation fails, should keep English summary."""
        client = MagicMock()
        client.call_llm.side_effect = RuntimeError("API Error")
        pipeline = CurationPipeline(batch_config, client)
        pipeline.use_mock = False

        repo = {"full_name": "test/repo", "refined_summary": "Original English.", "stars": 1000}
        result = pipeline._translate_per_repo(repo)
        assert result["chinese_summary"] == "Original English."
```

- [ ] **Step 2: Update `TestSummarizeReflectPerRepo` tests**

In `test_pipeline_batch.py:215-236`, update the success test to check for new 4-section headers in the stub fallback:

```python
class TestSummarizeReflectPerRepo:
    """Test per-repo summarization."""

    def test_per_repo_success(self, batch_config):
        """Per-repo should return repo with refined_summary."""
        client = MagicMock()
        client.call_llm.return_value = {"content": "### Core Pain Point Solved\\nTest."}
        pipeline = CurationPipeline(batch_config, client)
        repo = {"full_name": "test/repo", "description": "Test.", "tags": ["#Test"], "stars": 100}
        result = pipeline._summarize_reflect_per_repo(repo)
        assert result["refined_summary"] == "### Core Pain Point Solved\\nTest."
        assert result["reflection_trace"] == ""

    def test_per_repo_failure_uses_static_stub(self, batch_config):
        """When per-repo fails, should use static stub summary."""
        client = MagicMock()
        client.call_llm.side_effect = RuntimeError("Failed")
        pipeline = CurationPipeline(batch_config, client)
        repo = {"full_name": "test/repo", "description": "Test.", "tags": ["#Test"], "stars": 100}
        result = pipeline._summarize_reflect_per_repo(repo)
        assert "Core Pain Point Solved" in result["refined_summary"]
        assert "Design" in result["refined_summary"]
        assert "Engineering Insights" in result["refined_summary"]
```

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/hainingyu/Code/auto_github && uv run python -m pytest tests/ -v --tb=short 2>&1
```

Expected: All relevant tests PASS (227 tests). If some fail, fix test assertions to match new code.

---
