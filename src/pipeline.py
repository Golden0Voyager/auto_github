import os
import re
import json
import yaml
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from src.config import AppConfig
from src.crawler import GitHubCrawler
from src.llm import LLMClient
from src.dedup import RepoHistoryTracker

# Base Directory
BASE_DIR = Path(__file__).resolve().parent.parent


TAG_KEYWORDS = {
    "#LLM":        ["llm", "language model", "gpt", "inference", "transformer", "tokenizer", "embedding"],
    "#Agent":      ["agent", "multi-agent", "autonomous", "tool-use", "function call", "mcp", "orchestrat"],
    "#RAG":        ["rag", "retrieval", "vector", "chroma", "qdrant"],
    "#MoE":        ["moe", "mixture-of-experts", "mixture of experts", "sparse expert"],
    "#MLA":        ["mla", "latent attention", "multi-head latent"],
    "#Inference":  ["vllm", "tgi", "tensorrt", "sgl"],
    "#ComfyUI":    ["comfyui", "stable diffusion", "sdxl", "controlnet", "lora"],
    "#Vision":     ["vision", "detection", "segmentation", "yolo", "ocr", "image recognition"],
    "#Speech":     ["speech", "audio", "whisper", "tts", "asr", "voice", "transcrib"],
    "#Security":   ["security", "vulnerab", "scanner", "trivy", "sast", "dast", "pentest"],
    "#Generative": ["diffusion", "image generation", "video generation", "text-to-image", "text-to-video"],
    "#Framework":  ["framework", "library", "sdk", "toolkit"],
    "#WebUI":      ["webui", "dashboard", "frontend"],
    "#CLI":        ["cli", "command-line", "terminal", "tui"],
    "#DevTools":   ["ide", "editor", "vscode", "code completion", "copilot", "lint"],
    "#Database":   ["database", "sql", "vector db", "orm", "postgres", "clickhouse"],
}

LANGUAGE_TAGS = {
    "Python": "#Python", "Go": "#Go", "TypeScript": "#TypeScript",
    "JavaScript": "#JavaScript", "Rust": "#Rust", "C++": "#C++",
    "C": "#C", "Java": "#Java",
}


def _infer_tags_fallback(repo: Dict[str, Any]) -> List[str]:
    desc_lower = (repo.get("description", "") or "").lower()
    name_lower = repo.get("full_name", "").lower()
    text = f"{desc_lower} {name_lower}"
    tags: List[str] = []
    for tag, keywords in TAG_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            tags.append(tag)
            if len(tags) >= 3:
                break
    lang = repo.get("language", "")
    if lang in LANGUAGE_TAGS and len(tags) < 3:
        lang_tag = LANGUAGE_TAGS[lang]
        if lang_tag not in tags:
            tags.append(lang_tag)
    if not tags:
        tags = ["#OpenSource"]
    return tags[:3]


def _infer_selection_reason_fallback(repo: Dict[str, Any]) -> str:
    desc = (repo.get("description", "") or "").strip()
    lang = repo.get("language", "") or "未知"
    stars = repo.get("stars", 0) or 0
    period = repo.get("period_stars", "")
    parts: List[str] = []
    if period:
        parts.append(f"今日 {period}")
    if lang != "未知":
        parts.append(f"语言: {lang}")
    if stars >= 1000:
        parts.append(f"总星标 ⭐️ {stars:,}")
    if desc:
        truncated = desc[:80] + ("..." if len(desc) > 80 else "")
        parts.append(f"核心定位: {truncated}")
    if not parts:
        return "基于 trending 榜单的客观收录，等待 LLM 复审补充深度分析。"
    return " · ".join(parts)


def _infer_rating_fallback(repo: Dict[str, Any]) -> str:
    stars = repo.get("stars", 0) or 0
    period = repo.get("period_stars", "")
    period_num = 0
    if period:
        m = re.search(r"(\d[\d,]*)", period)
        if m:
            try:
                period_num = int(m.group(1).replace(",", ""))
            except ValueError:
                pass
    if stars >= 100000 or period_num >= 3000:
        return "S"
    if stars >= 20000 or period_num >= 500:
        return "A"
    return "B"


def _infer_tds_fallback(desc: str) -> str:
    """规则引擎判定 Technical Depth Score (T/E/S)。"""
    desc_lower = desc.lower()
    T_KEYWORDS = [
        "mla", "moe", "attention", "cuda kernel", "kv cache",
        "compiler", "runtime", "metal", "custom shader",
        "new language", "database engine", "protocol",
    ]
    E_KEYWORDS = [
        "agent", "rag", "mcp", "inference", "optimiz",
        "cli", "raycast", "swiftui", "core ml", "mlx",
        "comfyui", "workflow", "automation", "xcode",
        "mach-o", "ipa", "window manager",
    ]
    for kw in T_KEYWORDS:
        if kw in desc_lower:
            return "T"
    for kw in E_KEYWORDS:
        if kw in desc_lower:
            return "E"
    return "S"


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


class CurationPipeline:
    """Manages the 6-stage GitHub Trend & LLM Giant Curation Pipeline."""
    
    def __init__(self, config: AppConfig, llm_client: LLMClient, persona_key: str = "intermediate"):
        self.config = config
        self.llm = llm_client
        self.crawler = GitHubCrawler(config)
        self.persona_key = persona_key
        # 高星项目去重追踪器（节省 LLM 算力 + 给新兴项目留展位）
        self.dedup = RepoHistoryTracker(config)
        
        # Load Personas Prompt
        personas_path = BASE_DIR / "config" / "personas.yaml"
        if personas_path.exists():
            with open(personas_path, "r", encoding="utf-8") as f:
                self.personas = yaml.safe_load(f) or {}
        else:
            self.personas = {}
            
        self.current_persona = self.personas.get(persona_key, {
            "name": "中阶实践者",
            "description": "默认开发者画像",
            "prompt_focus": "关注 LLM 系统学习、Vibecoding、Agent 架构和工程落地。"
        })
        # 初始化 mock 标志，在 run() 中覆盖
        self.use_mock = False

    def _log_llm_stage(self, stage_name: str, before: Dict[str, int]) -> None:
        after = self.llm.get_stats()
        d_calls = after["call_count"] - before["call_count"]
        d_fail = after["failed_attempt_count"] - before["failed_attempt_count"]
        d_p = after["total_prompt_tokens"] - before["total_prompt_tokens"]
        d_c = after["total_completion_tokens"] - before["total_completion_tokens"]
        total_tokens = after["total_prompt_tokens"] + after["total_completion_tokens"]
        fail_note = f" (+{d_fail} 重试失败)" if d_fail > 0 else ""
        print(
            f"[LLM] {stage_name}: +{d_calls} 成功调用{fail_note} | "
            f"tokens +{d_p:,} in / +{d_c:,} out | "
            f"累计: {after['call_count']} 次 / {total_tokens:,} tokens"
        )

    def _prefilter_top_n(self, repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按 stars 降序截断长尾（零 token 成本，作为 bucket 引擎关闭时的回退）。"""
        cfg = self.config.stage2_pre_filter
        if not cfg.enabled or len(repos) <= cfg.max_repos:
            return repos
        sorted_repos = sorted(repos, key=lambda r: r.get("stars", 0), reverse=True)
        kept = sorted_repos[:cfg.max_repos]
        print(
            f"[Pre-filter] {len(repos)} → {len(kept)} repo "
            f"(bucket engine 关闭时的星标回退)"
        )
        return kept

    def _bucket_allocate(self, repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """三桶分配引擎：按 Early Bird / High-Star Hot / Deep Dive 配额选出 repo。

        取代旧的 _prefilter_top_n()，在 Stage 2 之前运行。
        注意：repos 中每个 dict 应已有 'is_first_seen' 字段（由 Stage 1.5 注入）。
        """
        cfg = self.config.bucket_allocation
        if not cfg.enabled:
            return self._prefilter_top_n(repos)

        if len(repos) <= cfg.total_slots:
            return repos

        early_bird_pool: List[Dict] = []
        high_star_pool: List[Dict] = []
        all_remaining: List[Dict] = []

        for r in repos:
            name = r.get("full_name", "")
            stars = r.get("stars", 0) or 0
            period_stars = r.get("period_stars", "")
            period_num = 0
            if period_stars:
                m = re.search(r"(\d[\d,]*)", period_stars)
                if m:
                    try:
                        period_num = int(m.group(1).replace(",", ""))
                    except ValueError:
                        pass
            is_first_seen = r.get("is_first_seen", False)

            desc = (r.get("description", "") or "").lower()
            tds = self._infer_tds(desc)

            is_early = stars < 3000 or (stars < 5000 and is_first_seen)
            is_high = stars >= 10000 or period_num >= 500

            if is_early:
                early_bird_pool.append(r)
            elif is_high:
                high_star_pool.append(r)
            else:
                all_remaining.append(r)

            r["tds"] = tds
            r["_bucket"] = "early_bird" if is_early else ("high_star" if is_high else "deep_dive")

        def take_from(pool, n):
            pool.sort(key=lambda x: -x.get("stars", 0))
            return pool[:n], pool[n:]

        result = []
        eb_taken, eb_rest = take_from(early_bird_pool, cfg.early_bird)
        hs_taken, hs_rest = take_from(high_star_pool, cfg.high_star_hot)

        leftover = eb_rest + hs_rest + all_remaining
        tds_order = {"T": 0, "E": 1, "S": 2}
        leftover.sort(key=lambda x: (tds_order.get(x.get("tds", "S"), 3), -x.get("stars", 0)))
        dd_taken = leftover[:cfg.deep_dive]

        result = eb_taken + hs_taken + dd_taken

        if len(result) < cfg.total_slots:
            remaining = leftover[cfg.deep_dive:]
            remaining.sort(key=lambda x: -x.get("stars", 0))
            needed = cfg.total_slots - len(result)
            result.extend(remaining[:needed])

        if len(result) > cfg.total_slots:
            result.sort(
                key=lambda x: (
                    0 if x.get("_bucket") == "early_bird" else
                    1 if x.get("_bucket") == "high_star" else 2,
                    tds_order.get(x.get("tds", "S"), 3),
                    -x.get("stars", 0),
                )
            )
            result = result[:cfg.total_slots]

        dropped = len(repos) - len(result)
        print(
            f"[Bucket Alloc] {len(repos)} → {len(result)} repo "
            f"(早鸟:{len(eb_taken)} 高星:{len(hs_taken)} 深潜:{len(dd_taken)}"
            f" 淘汰:{dropped})"
        )
        return result

    @staticmethod
    def _infer_tds(desc: str) -> str:
        """规则引擎判定 Technical Depth Score (T/E/S)。"""
        desc_lower = desc.lower()
        T_KEYWORDS = [
            "mla", "moe", "attention", "cuda kernel", "kv cache",
            "compiler", "runtime", "metal", "custom shader",
            "new language", "database engine", "protocol",
        ]
        E_KEYWORDS = [
            "agent", "rag", "mcp", "inference", "optimiz",
            "cli", "raycast", "swiftui", "core ml", "mlx",
            "comfyui", "workflow", "automation", "xcode",
            "mach-o", "ipa", "window manager",
        ]
        for kw in T_KEYWORDS:
            if kw in desc_lower:
                return "T"
        for kw in E_KEYWORDS:
            if kw in desc_lower:
                return "E"
        return "S"

    def run(self, since: str = "daily", use_mock: bool = False) -> Dict[str, Any]:
        """Runs the entire 6-stage pipeline.
        
        Args:
            since: 'daily', 'weekly', or 'monthly'
            use_mock: If True, uses realistic offline mock data to avoid network/API limits.
        """
        self.use_mock = use_mock
        print(f"\n[Pipeline] Starting 6-Stage Pipeline (Timeframe: {since}, Persona: {self.current_persona['name']}, Mock: {use_mock})")

        # 预清理过期的存档项目（cooldown 已过的项目重新允许进入策展）
        purged = self.dedup.purge_expired_cooldowns()
        if purged:
            print(f"[Dedup] 已清理 {purged} 项过期存档项目，重新允许进入策展")

        # --- Stage 1: Crawl (抓取) ---
        raw_repos = self._stage_crawl(since, use_mock)
        if not raw_repos:
            print("[Pipeline Error] Stage 1 failed to acquire repositories. Aborting.")
            return {}

        # --- Stage 1.5: Dedup (高星项目存档过滤) ---
        active_repos, cooled_repos, first_seen_map = self.dedup.filter_active(raw_repos)
        for r in active_repos:
            r["is_first_seen"] = first_seen_map.get(r.get("full_name", ""), False)
        if cooled_repos:
            print(
                f"[Dedup] 过滤掉 {len(cooled_repos)} 个处于 30 天冷却期的高🌟项目:"
                f" {', '.join(r.get('full_name', '?') for r in cooled_repos[:5])}"
                + (" ..." if len(cooled_repos) > 5 else "")
            )
        if not active_repos:
            print("[Pipeline Info] All fetched repos are in archive cooldown. Nothing to curate today.")
            return {
                "meta": {
                    "timeframe": since,
                    "persona": self.current_persona["name"],
                    "total_input_repos": len(raw_repos),
                    "total_curated_repos": 0,
                    "cooled_repos": [r["full_name"] for r in cooled_repos],
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                "repos": [],
                "reports": {},
            }

        # --- Stage 1.75: Bucket Allocation（取代旧的 Pre-filter）---
        if self.config.bucket_allocation.enabled:
            active_repos = self._bucket_allocate(active_repos)
        else:
            active_repos = self._prefilter_top_n(active_repos)
        if not active_repos:
            print("[Pipeline Info] All repos filtered out by bucket allocation. Aborting.")
            return {
                "meta": {
                    "timeframe": since,
                    "persona": self.current_persona["name"],
                    "total_input_repos": len(raw_repos),
                    "total_curated_repos": 0,
                    "cooled_repos": [r["full_name"] for r in cooled_repos],
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                "repos": [],
                "reports": {},
            }

        # --- Stage 2: Analyze (分析 & 过滤) ---
        before = self.llm.get_stats()
        analyzed_repos = self._stage_analyze(active_repos)
        self._log_llm_stage("Stage 2 Analyze", before)
        if not analyzed_repos:
            print("[Pipeline Info] Stage 2: No repos passed analysis threshold. Aborting.")
            return {}

        # --- Stage 3+4: Summarize & Reflect (合并批处理，1 次 LLM 调用) ---
        before = self.llm.get_stats()
        refined_repos = self._stage_summarize_and_reflect(analyzed_repos)
        self._log_llm_stage("Stage 3+4 Summarize+Reflect", before)

        # --- Stage 5: Translate (批处理，1 次 LLM 调用) ---
        before = self.llm.get_stats()
        translated_repos = self._stage_translate(refined_repos)
        self._log_llm_stage("Stage 5 Translate", before)

        # --- Stage 6: Refine Layout (精修排版与多端打包) ---
        reports = self._stage_refine_layout(
            translated_repos,
            since,
            cooled_repos=cooled_repos,
            archive_total=self.dedup.archive_count,
        )

        # --- 写回: 记录本次出现的项目；满足条件的晋升到存档 ---
        newly_archived = self.dedup.record_occurrences(active_repos)
        if newly_archived:
            print(
                f"[Dedup] 新增 {len(newly_archived)} 个项目进入「高🌟项目存档」:"
                f" {', '.join(newly_archived)}"
            )

        print("[Pipeline] All 6 Stages completed successfully!")
        return {
            "meta": {
                "timeframe": since,
                "persona": self.current_persona["name"],
                "total_input_repos": len(raw_repos),
                "total_curated_repos": len(translated_repos),
                "cooled_repos": [r["full_name"] for r in cooled_repos],
                "newly_archived": newly_archived,
                "archive_total": self.dedup.archive_count,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "repos": translated_repos,
            "reports": reports,
        }

    def _stage_crawl(self, since: str, use_mock: bool) -> List[Dict[str, Any]]:
        """Stage 1: Fetch raw repository data from GitHub Trending and Orgs."""
        print("\n=== Stage 1: Crawl (数据抓取) ===")
        if use_mock:
            repos = self.crawler.get_mock_data()
        else:
            # Crawl daily, weekly, and monthly trending lists to construct a complete board
            # Limit to top 10 for each to keep Stage 2 LLM analysis batch optimized
            orig_max = self.config.github.max_trending_repos
            self.config.github.max_trending_repos = 10
            
            print("[Crawl] Pulling all three timeframes (daily, weekly, monthly) to build a unified trend board...")
            t_daily = self.crawler.crawl_trending("daily")
            t_weekly = self.crawler.crawl_trending("weekly")
            t_monthly = self.crawler.crawl_trending("monthly")
            
            # Reset config
            self.config.github.max_trending_repos = orig_max
            
            trending = t_daily + t_weekly + t_monthly
            giants = self.crawler.fetch_giant_repos()
            
            # Deduplicate by full_name, prioritizing trending data if available
            dedup = {}
            for repo in trending + giants:
                name = repo["full_name"]
                if name not in dedup:
                    dedup[name] = repo
                else:
                    # Merge information: keep period_stars if from trending
                    if repo.get("period_stars") and not dedup[name].get("period_stars"):
                        dedup[name]["period_stars"] = repo["period_stars"]
                        dedup[name]["source"] = "trending & llm_giant"
            
            repos = list(dedup.values())
            
        print(f"[Stage 1 Done] Retrieved {len(repos)} unique repositories.")
        return repos

    def _stage_analyze(self, repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stage 2: Filter and classify repositories using LLM."""
        print("\n=== Stage 2: Analyze (项目分析与智能筛选) ===")
        
        if self.use_mock or not self.llm.client:
            print("[Stage 2 Fallback] Bypassing LLM API. Selecting and rating all crawled repos offline.")
            analyzed_repos = []
            ratings = ["S", "S", "A", "B", "A", "B", "C"]
            tags_list = [
                ["#MoE", "#MLA", "#Infra"],
                ["#Reasoning", "#RL", "#Trace"],
                ["#Attention", "#MLA", "#PyTorch"],
                ["#Agent", "#3D", "#Vision"],
                ["#LLM", "#Inference", "#Weights"],
                ["#Transformers", "#ML", "#Framework"],
                ["#ComfyUI", "#StableDiffusion", "#Graph"]
            ]
            reasons = [
                "DeepSeek's flagship model featuring innovative MLA and Multi-Token Prediction (MTP) architectures.",
                "SOTA open reasoning model establishing native reasoning traces as an industry standard.",
                "Highly requested PyTorch implementation of MLA by popular developer lucidrains.",
                "DeepMind's agent framework exploring virtual 3D environment control and physical world simulation.",
                "Meta's highly popular baseline models defining open-source weights standard.",
                "The core baseline library for almost all modern LLM implementations.",
                "Extremely popular UI interface for stable diffusion and generative image pipeline engineering."
            ]
            for i, r in enumerate(repos):
                rc = r.copy()
                rc["rating"] = ratings[i % len(ratings)]
                rc["tags"] = tags_list[i % len(tags_list)]
                rc["selection_reason"] = reasons[i % len(reasons)]
                rc["technical_depth"] = _infer_tds_fallback(rc.get("description", "") or "")
                rc["tds"] = "E"
                rc["_bucket"] = "deep_dive"
                analyzed_repos.append(rc)
            # Sort curated repos primarily by rating (S > A > B > C) and secondarily by star count descending (Plan C)
            rating_order = {"S": 0, "A": 1, "B": 2, "C": 3}
            analyzed_repos.sort(key=lambda x: (rating_order.get(x.get("rating", "B"), 4), -x.get("stars", 0)))
            return analyzed_repos

        # Prepare list of repos for LLM to review in batch to save tokens and avoid 429
        repos_summary = []
        for i, r in enumerate(repos):
            repos_summary.append({
                "index": i,
                "full_name": r["full_name"],
                "description": r["description"],
                "language": r["language"],
                "stars": r["stars"],
                "source": r["source"]
            })
            
        system_prompt = (
            "You are an expert GitHub Open-Source Analyst and Research Assistant. "
            "Your task is to analyze a batch of GitHub repositories, rate each one, and assign tags. "
            "Process ALL provided repositories — do not drop any.\n\n"
            f"Target User Profile: {self.current_persona['name']}\n"
            f"Profile Focus: {self.current_persona['prompt_focus']}\n\n"
            "Criteria for rating:\n"
            "1. Relevance: LLM application engineering, multi-agent frameworks, RAG index/chunking optimization, ComfyUI node flows/ControlNet, local productivity/vibecoding helper tools (like Claude Code, RTK, CLI proxies), or core AI infra.\n"
            "2. Value: Exclude trivial repositories, basic lists/collections, or standard tutorials unless they are exceptional. Prioritize high-quality, practical repositories.\n"
            "3. Rating: Rate each repository as:\n"
            "   - 'S': Absolute must-know, state-of-the-art breakthrough or crucial paradigm shift.\n"
            "   - 'A': Highly practical, robust technical value, very relevant to the profile.\n"
            "   - 'B': Interesting utility, good developer ergonomics, solid experiment.\n"
            "   - 'C': Moderate interest but marginally relevant.\n"
            "4. Tags: Add 2-3 specific technical hashtags (e.g. #Agent, #RAG, #MoE, #MLA, #ComfyUI, #Vibecoding, #Telemetry).\n"
            "5. Technical Depth (T/E/S): Classify each selected repo's engineering depth:\n"
            "   - T (Technical): Core architecture innovation, system-level breakthrough, custom CUDA/Metal, novel algorithm, compiler/runtime engineering\n"
            "   - E (Engineering): Solid tooling, well-crafted framework, practical workflow orchestration, Apple ecosystem tools (Raycast, SwiftUI, MLX, CoreML), dev productivity\n"
            "   - S (Standard): Configuration, documentation, wrapper, basic tutorial\n"
            "   If unsure, default to E.\n\n"
            "6. reason_for_selection: Keep this VERY SHORT — 1-2 sentences maximum. "
            "A brief explanation of why this repo matters. Do NOT repeat the full analysis here.\n\n"
            "Return a strictly valid JSON array containing an entry for EVERY provided repository. "
            "Each object must have exactly these keys: "
            "['index', 'full_name', 'rating', 'tags', 'reason_for_selection', 'technical_depth']. "
            "Do not wrap with text outside the JSON block."
        )
        
        user_content = f"Here is the batch of repositories to analyze:\n\n{json.dumps(repos_summary, ensure_ascii=False, indent=2)}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
        
        try:
            # Stage 2 uses DeepSeek-V3-1 (non-reasoning) for batch classification
            response = self.llm.call_llm(messages, use_reasoning=False, temperature=0.2)
            raw_content = response["content"]
            
            # Robust JSON extraction
            selected_items = self._parse_json_from_response(raw_content)
            
            analyzed_repos = []
            for item in selected_items:
                idx = item.get("index")
                if idx is not None and 0 <= idx < len(repos):
                    orig_repo = repos[idx].copy()
                    orig_repo["rating"] = item.get("rating", "B")
                    orig_repo["tags"] = item.get("tags", [])
                    orig_repo["selection_reason"] = (item.get("reason_for_selection", "") or "")[:200]
                    orig_repo["technical_depth"] = item.get("technical_depth", "E")
                    analyzed_repos.append(orig_repo)

            # TDS 规则引擎覆盖（sanity check on LLM output）
            for r in analyzed_repos:
                desc = (r.get("description", "") or "").lower()
                rule_tds = _infer_tds_fallback(desc)
                llm_tds = r.get("technical_depth", "E")
                if rule_tds != llm_tds:
                    print(f"  [TDS Override] {r['full_name']}: LLM={llm_tds} → Rule={rule_tds}")
                    r["technical_depth"] = rule_tds

            # Sort curated repos primarily by rating (S > A > B > C) and secondarily by star count descending (Plan C)
            rating_order = {"S": 0, "A": 1, "B": 2, "C": 3}
            analyzed_repos.sort(key=lambda x: (rating_order.get(x.get("rating", "B"), 4), -x.get("stars", 0)))
            
            print(f"[Stage 2 Done] Selected {len(analyzed_repos)}/{len(repos)} repositories based on persona filtering.")
            for r in analyzed_repos:
                print(f" - [{r['rating']}] {r['full_name']} | Tags: {r['tags']}")
            return analyzed_repos
            
        except Exception as e:
            print(f"[Stage 2 Error] Failed to analyze repositories: {e}")
            print("[Stage 2 Fallback] Retaining top repositories with rule-based tag inference.")
            fallback_repos = []
            for r in repos[:6]:
                rc = r.copy()
                rc["rating"] = _infer_rating_fallback(r)
                rc["tags"] = _infer_tags_fallback(r)
                rc["selection_reason"] = _infer_selection_reason_fallback(r)
                rc["technical_depth"] = _infer_tds_fallback(r.get("description", "") or "")
                fallback_repos.append(rc)
            return fallback_repos

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

    def _translate_per_repo(self, r: Dict[str, Any]) -> Dict[str, Any]:
        rc = r.copy()
        summary = r.get("refined_summary", "") or ""
        if len(summary.strip()) < 50:
            print(f"  [Stage 5 Skip] {r['full_name']}: refined_summary too short ({len(summary.strip())} chars), keeping English")
            rc["chinese_summary"] = summary
            return rc
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

    def _stage_refine_layout(self, repos: List[Dict[str, Any]], since: str, cooled_repos: Optional[List[Dict[str, Any]]] = None, archive_total: int = 0) -> Dict[str, Any]:
        """Stage 6: Refine layout to construct aesthetic Markdown and Feishu interactive card payloads."""
        print("\n=== Stage 6: Refine Layout (精修排版与多端打包) ===")
        from src.formatter import ReportFormatter
        
        formatter = ReportFormatter(self.config, self.current_persona, since)
        reports = formatter.generate_all(
            repos,
            cooled_repos=cooled_repos or [],
            archive_total=archive_total,
        )
        
        print("[Stage 6 Done] Generated reports in multiple formats.")
        return reports

    def _parse_json_from_response(self, text: str) -> List[Dict[str, Any]]:
        """Helper to parse a JSON list from LLM response content (supports markdown block wrapper)."""
        # Try to find JSON block in ```json ... ```
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if match:
            json_str = match.group(1).strip()
        else:
            json_str = text.strip()
            
        try:
            data = json.loads(json_str)
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                # If LLM returned an object containing the list, try to extract it
                for val in data.values():
                    if isinstance(val, list):
                        return val
            return []
        except Exception as e:
            print(f"[Parsing Error] Failed to parse JSON: {e}. Raw content: {text[:200]}")
            # Try a looser regex parser if needed, or raise
            raise e
