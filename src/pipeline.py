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


MOCK_TRANSLATIONS = {
    "deepseek-ai/DeepSeek-V3": """### 核心解决的工程痛点
解决了超大规模混合专家模型 (MoE, 671B总参数 / 37B激活) 在极高并发吞吐下的显存挤压瓶颈，重点攻克了多智能体 (Multi-Agent) 协同场景下频繁并发产生的 KV Cache 内存崩溃压力。

### 底层架构与工程设计
- **MLA (Multi-head Latent Attention)**: 通过对 Key 和 Value 进行潜在投影压缩（Low-rank Latent Projection），将 KV Cache 的显存开销缩减为原本的几分之一，极大释放了推理期硬件承载力。
- **DeepSeekMoE & MTP**: 设计无辅助损失负载均衡机制，消除了训练稳定性难题；结合多 Token 预测目标 (Multi-Token Prediction)，以并行化流程极大拉升了 token 生成吞吐。

### 极客实战与工作流落地
对于系统化 LLM 学习者，MLA 的底秩投影压缩是设计并发 Agent 工作流时规避高昂网关延迟的利器。推荐使用 `rtk` (Rust Token Killer) 在本地配置透明代理，拦截冗余的交互回显，从而在多 Agent 动态辩论中削减多达 80% 的 API Token 消耗。""",

    "deepseek-ai/DeepSeek-R1": """### 核心解决的工程痛点
突破了传统大模型依赖海量人工对齐 (SFT) 数据才能掌握复杂逻辑推理的限制，实现了大模型在无人工标注背景下自主进行"反思-纠偏-定理证明"的深度泛化能力。

### 底层架构与工程设计
- **大规模强化学习 (RL)**: 在无 SFT 前置的基座模型上直接通过强化学习注入规则，模型自发学会生成可读的 Native Reasoning Trace (思考链条)。
- **开源蒸馏生态**: 将强大的推理本领蒸馏并开源至轻量级框架（如 Qwen-1.4B/14B/32B/72B 等），使得普通边缘计算设备也具备高阶思考能力。

### 极客实战与工作流落地
中阶开发者在进行软件回归测试与自动 Debug 等 Agent 系统设计时，可引入 R1 的 `reasoning_content` 作为天然的思维诊断数据。配合 custom MCP 工具精准拦截并过滤中间推理链降噪，从而规避 Agent 陷入死循环决策。""",

    "lucidrains/MLA-pytorch": """### 核心解决的工程痛点
填补了开源社区缺乏高度清晰、易读、且纯粹的 Multi-Head Latent Attention (MLA) 复现库这一空白，方便开发者不依赖庞大的大模型框架，即可单独在 PyTorch 中调试潜在投影压缩机制。

### 底层架构与工程设计
- **底秩解耦投影 (Decoupled Projection)**: 严格复现了 DeepSeek V2/V3 论文中针对 Query 向量与 Key/Value 向量进行的低秩压缩降维算法。
- **旋转位置编码 (RoPE)**: 巧妙打通了低秩投影解耦与旋转位置编码的融合细节，保留了极佳的长文本检索精度。

### 极客实战与工作流落地
这是进行底层注意力优化与自研多模态 Transformer 时的必读代码。建议克隆到本地，通过 mock 测试集观测其 KV Cache 随 batch 增长的曲线，体会"展位控制"的物理空间局限。""",

    "google-research/sima": """### 核心解决的工程痛点
打破了传统 AI Agent 只能执行单一特定游戏指令的物理局限，探索了跨 3D 虚拟世界的通用多模态自主感知与操纵能力（Generalist Game Agent）。

### 底层架构与工程设计
- **3D 跨域多世界感知 (Multiworld Perception)**: 统一底层视觉与键盘/鼠标映射动作空间，使得智能体在完全不同的 3D 游戏引擎中表现出一致性物理常识。
- **图像到指令对齐 (ViT + GNN)**: 精细化提炼视觉世界模型表征，将神经网络对于物理世界动力学的认知投射到动作空间序列。

### 极客实战与工作流落地
非常适合作为研究"具身智能"与"世界生成模型"的中阶教材。可将 SIMA 的物理常识投射概念应用于 ComfyUI 批量图像控制链中，借助 ControlNet 物理干预，生成视觉连续性极高的一致性 3D 虚拟展位。""",

    "meta-llama/llama3": """### 核心解决的工程痛点
为整个开源社区提供了一个高度工业级对齐、易于本地部署并可商业化使用的超高质量基础语言模型。

### 底层架构与工程设计
- **128K 分词器与 Grouped Query Attention (GQA)**: 实现了超长上下文窗口的高效计算，GQA 机制完美平衡了多头注意力与显存效率。
- **大规模指令微调**: 采用数十万高质量精修指令数据集，保证了优异的人类意图遵循与低幻觉率。

### 极客实战与工作流落地
这是中阶极客搭建离线 RAG 语义索引或本地知识库的"黄金底座"。配合本地离线工具链，可极速构建高性能私有知识检索系统。""",

    "huggingface/transformers": """### 核心解决的工程痛点
抹平了不同机器学习底座（PyTorch, TensorFlow, JAX）的物理界限，建立了全球统一、最高频使用的 Transformer 架构标准调用接口。

### 底层架构与工程设计
- **高度模块化封装**: 支持一键加载全网数十万公开预训练模型、快速进行本地微调与推理。
- **硬件异构加速**: 完美融合 FlashAttention-2 与 QLoRA，自动优化多卡与异构计算集群吞吐。

### 极客实战与工作流落地
极客开发管线的基础设施。通过将此包与自定义 Python MCP 服务结合，可实现精准的模型性能观测与延迟 Telemetry 上报。""",

    "comfyanonymous/ComfyUI": """### 核心解决的工程痛点
彻底解决了传统的 WebUI 图像生成界面在大批量、复杂图生图与多模型融合干预流程中"面条式代码"和难以复用的问题。

### 底层架构与工程设计
- **流式有向无环图 (DAG)**: 将 ComfyUI 的生成算子高度原子化，设计为输入-输出插槽的图拓扑节点，支持无限分叉与并行判定。
- **程序化 Python API**: 支持将整个复杂图形面板无缝转换为 JSON 配置，在 Python 底层进行权重热插拔与节点动态生成。

### 极客实战与工作流落地
对于关注系统设计与美学表现的技术极客而言，这正是流程控制与工程美学完美结合的代表。您可以将 ComfyUI Python API 深度融入多端生成流，通过 Agent 智能体动态编排 ComfyUI 流程，实现对 Flux/SDXL 节点的高精度干预。""",
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
        cfg = self.config.stage2_pre_filter
        if not cfg.enabled or len(repos) <= cfg.max_repos:
            return repos
        sorted_repos = sorted(repos, key=lambda r: r.get("stars", 0), reverse=True)
        kept = sorted_repos[:cfg.max_repos]
        dropped = len(repos) - len(kept)
        print(
            f"[Pre-filter] Stage 2 输入预筛: {len(repos)} → {len(kept)} repo "
            f"(按 stars 降序砍掉 {dropped} 个长尾，max_repos={cfg.max_repos})"
        )
        return kept

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
        active_repos, cooled_repos = self.dedup.filter_active(raw_repos)
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

        # --- Stage 1.75: Pre-filter (按 stars 砍长尾，零 token 成本避免 Stage 2 撞 max_tokens) ---
        active_repos = self._prefilter_top_n(active_repos)
        if not active_repos:
            print("[Pipeline Info] All repos filtered out by pre-filter. Aborting.")
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
            "Your task is to analyze a batch of GitHub repositories and select the ones that are highly relevant to the target user profile.\n\n"
            f"Target User Profile: {self.current_persona['name']}\n"
            f"Profile Focus: {self.current_persona['prompt_focus']}\n\n"
            "Criteria for selection:\n"
            "1. Relevance: LLM application engineering, multi-agent frameworks, RAG index/chunking optimization, ComfyUI node flows/ControlNet, local productivity/vibecoding helper tools (like Claude Code, RTK, CLI proxies), or core AI infra.\n"
            "2. Value: Exclude trivial repositories, basic lists/collections, or standard tutorials unless they are exceptional. Prioritize high-quality, practical repositories.\n"
            "3. Rating: Rate selected repositories as:\n"
            "   - 'S': Absolute must-know, state-of-the-art breakthrough or crucial paradigm shift.\n"
            "   - 'A': Highly practical, robust technical value, very relevant to the profile.\n"
            "   - 'B': Interesting utility, good developer ergonomics, solid experiment.\n"
            "   - 'C': Moderate interest but marginally relevant.\n"
            "4. Tags: Add 2-3 specific technical hashtags (e.g. #Agent, #RAG, #MoE, #MLA, #ComfyUI, #Vibecoding, #Telemetry).\n\n"
            "Return a strictly valid JSON array of selected objects containing exactly the following keys: "
            "['index', 'full_name', 'rating', 'tags', 'reason_for_selection']. Do not wrap with text outside the JSON block."
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
                    orig_repo["selection_reason"] = item.get("reason_for_selection", "")
                    analyzed_repos.append(orig_repo)
                    
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
                fallback_repos.append(rc)
            return fallback_repos

    def _stage_summarize_and_reflect(self, repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stage 3+4 merged: One batch LLM call writes the final English refined_summary for all repos.

        Happy path = 1 LLM call. On batch failure, falls back to per-repo iteration,
        then to static stub on per-repo failure.
        """
        print("\n=== Stage 3+4: Summarize & Reflect (合并批处理) ===")

        if self.use_mock or not self.llm.client:
            print("[Stage 3+4 Fallback] Bypassing LLM. Generating mock refined summaries offline.")
            result = []
            for r in repos:
                rc = r.copy()
                rc["refined_summary"] = f"Refined technical analysis of {r['full_name']} removing all AI marketing fluff. Structured for {self.current_persona['name']}."
                rc["reflection_trace"] = ""
                result.append(rc)
            return result

        try:
            result = self._summarize_reflect_batch(repos)
            print(f"[Stage 3+4 Batch Done] Processed {len(result)} repositories in one LLM call.")
            return result
        except Exception as e:
            print(f"[Stage 3+4 Batch Error] {e}, falling back to per-repo iteration.")

        return [self._summarize_reflect_per_repo(r) for r in repos]

    def _summarize_reflect_batch(self, repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        system_prompt = (
            "You are a senior principal architect and AI review expert writing a curated technical digest.\n"
            "Write the final English summary for each repo below in ONE shot. No drafting, no follow-up.\n\n"
            "Strict rules:\n"
            "1. **No marketing fluff**: Ban sales adjectives and cliches ('revolutionary', 'game-changing', 'transformative', 'rocket', 'exciting', 'powerhouse', 'cutting-edge'). The language must be clinical, concrete, and informative — facts and mechanisms, not enthusiasm.\n"
            "2. **Universality**: Output MUST be universally applicable. ABSOLUTELY FORBID mentioning any specific individual names (e.g. Haining, 于海宁, Golden0Voyager) or unique personal backgrounds (e.g. MFA, Contemporary Art, Art Museums). The report must be a professional, stand-alone industry technical report.\n"
            "3. **No first-person**: No 'I', 'we', or personal pronouns.\n\n"
            "Per-section writing rules (this is where quality most often breaks):\n"
            "a. **Length**: each section is 2-4 sentences, 100-180 English characters. ONE-sentence sections are FORBIDDEN — they read as a list of clauses with no opening, no closing, no flow.\n"
            "b. **Structure**: open with a topic sentence that names the core insight or mechanism, then expand with 1-2 sentences of why-it-matters, trade-off, or concrete application. End with a clear point, not a trailing clause.\n"
            "c. **Anti-comma-stacking**: do NOT write 'X is A, B, C, and D' in a single sentence. Use periods to separate distinct claims. A sentence with 4+ comma-separated clauses is a quality failure.\n"
            "d. **Concrete over abstract**: prefer real terms (e.g., 'KV Cache memory', 'LSP protocol', 'piece-table buffer') over hand-waves like 'efficient', 'scalable', 'high-performance'.\n"
            "e. **Optional bold lead-in** for a key term: '- **MLA (Multi-head Latent Attention)**: ...' style is welcome when there is a named mechanism to highlight.\n\n"
            "Format per repo (exact markdown headers, in English):\n"
            "### Core Technical Problem\n[2-4 sentences]\n\n"
            "### Implementation & Engineering Depth\n[2-4 sentences]\n\n"
            "### Vibecoding & Engineering Application\n[2-4 sentences]\n\n"
            "Return a JSON array of objects: "
            '[{"full_name": "<owner/repo>", "refined_summary": "### Core Technical Problem\\n...\\n### Implementation & Engineering Depth\\n...\\n### Vibecoding & Engineering Application\\n..."}].\n'
            "Do not wrap with any text outside the JSON block. Keep the response strictly to the JSON array."
        )

        repos_data = [
            {
                "full_name": r["full_name"],
                "description": r.get("description", ""),
                "language": r.get("language", ""),
                "tags": r.get("tags", []),
                "rating": r.get("rating", "B"),
                "selection_reason": r.get("selection_reason", ""),
            }
            for r in repos
        ]
        user_content = json.dumps(repos_data, ensure_ascii=False, indent=2)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        res = self.llm.call_llm(messages, use_reasoning=False, temperature=0.3)
        items = self._parse_json_from_response(res["content"])

        by_name: Dict[str, str] = {}
        for item in items:
            name = item.get("full_name")
            summary = item.get("refined_summary", "")
            if name and summary:
                by_name[name] = summary

        result = []
        for r in repos:
            rc = r.copy()
            name = r["full_name"]
            if name in by_name:
                rc["refined_summary"] = by_name[name]
                rc["reflection_trace"] = ""
            else:
                print(f"[Stage 3+4 Warning] Missing in batch response: {name}, falling back to per-repo")
                rc = self._summarize_reflect_per_repo(rc)
            result.append(rc)
        return result

    def _summarize_reflect_per_repo(self, r: Dict[str, Any]) -> Dict[str, Any]:
        rc = r.copy()
        system_prompt = (
            "You are a senior principal architect writing a curated technical digest.\n"
            "Write the final English summary in ONE shot. No drafting, no follow-up.\n\n"
            "Strict rules:\n"
            "1. **No marketing fluff**: ban 'revolutionary', 'game-changing', 'cutting-edge', etc. Clinical, concrete, informative prose only.\n"
            "2. **Universality**: no specific individual names or personal backgrounds.\n"
            "3. **No first-person**.\n\n"
            "Per-section rules (quality-critical):\n"
            "a. Each section: 2-4 sentences, 100-180 characters. ONE-sentence sections are FORBIDDEN.\n"
            "b. Topic-then-detail: lead with the core insight, then expand with mechanism, trade-off, or application.\n"
            "c. No comma-stacking ('X is A, B, C, and D'). Use periods to separate claims.\n"
            "d. Concrete terms over abstract hand-waves.\n\n"
            "Format (exact markdown headers):\n"
            "### Core Technical Problem\n### Implementation & Engineering Depth\n### Vibecoding & Engineering Application"
        )
        user_content = (
            f"Repository: {r['full_name']}\n"
            f"Description: {r.get('description', '')}\n"
            f"Language: {r.get('language', '')}\n"
            f"Tags: {', '.join(r.get('tags', []))}\n"
            f"Rating: {r.get('rating', 'B')}\n"
            f"Selection reason: {r.get('selection_reason', '')}"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        try:
            res = self.llm.call_llm(messages, use_reasoning=False, temperature=0.3)
            rc["refined_summary"] = res["content"]
        except Exception as e:
            print(f"[Stage 3+4 Warning] Per-repo also failed for {r['full_name']}: {e}")
            rc["refined_summary"] = (
                f"### Core Technical Problem\n{r.get('description', 'Engineering challenge.')}\n\n"
                f"### Implementation & Engineering Depth\nStandard implementation; deeper review pending.\n\n"
                f"### Vibecoding & Engineering Application\nIntegrate via standard practices."
            )
        rc["reflection_trace"] = ""
        return rc

    def _stage_translate(self, repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Stage 5: One batch LLM call translates all refined_summary to Chinese.

        Happy path = 1 LLM call. On batch failure, falls back to per-repo iteration,
        then to English text on per-repo failure.
        """
        print("\n=== Stage 5: Translate (高保真学术级中文翻译 - 批处理) ===")

        if self.use_mock or not self.llm.client:
            print("[Stage 5 Fallback] Bypassing LLM. Rendering pre-authored mock translations.")
            result = []
            for r in repos:
                rc = r.copy()
                rc["chinese_summary"] = MOCK_TRANSLATIONS.get(
                    r["full_name"],
                    f"### 核心解决的工程痛点\n解决开源项目 {r['full_name']} 的工程挑战。\n\n"
                    f"### 底层架构与工程设计\n高度模块化设计。\n\n"
                    f"### 极客实战与工作流落地\n可集成入现有 Multi-Agent 工作流。",
                )
                result.append(rc)
            return result

        try:
            result = self._translate_batch(repos)
            print(f"[Stage 5 Batch Done] Translated {len(result)} repositories in one LLM call.")
            return result
        except Exception as e:
            print(f"[Stage 5 Batch Error] {e}, falling back to per-repo iteration.")

        return [self._translate_per_repo(r) for r in repos]

    def _translate_batch(self, repos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        system_prompt = (
            "You are an expert bilingual technical editor.\n"
            "Translate each English summary into elegant, natural, professional Chinese.\n\n"
            "Rules:\n"
            "- Keep industry-standard English acronyms untranslated: RAG, Agent, MoE, MLA, KV Cache, ComfyUI, ControlNet, Telemetry, Token, Prompt, CLI, Webhook, MCP.\n"
            "- Maintain the exact 3-section markdown header structure in Chinese:\n"
            "  ### 核心解决的工程痛点\n"
            "  ### 底层架构与工程设计\n"
            "  ### 极客实战与工作流落地\n"
            "- Avoid literal translation; use fluent professional Chinese developer vocabulary.\n\n"
            "Return a JSON array of objects: "
            '[{"full_name": "<owner/repo>", "chinese_summary": "### 核心解决的工程痛点\\n...\\n### 底层架构与工程设计\\n...\\n### 极客实战与工作流落地\\n..."}].\n'
            "Do not wrap with any text outside the JSON block. Keep the response strictly to the JSON array."
        )

        repos_data = [
            {"full_name": r["full_name"], "refined_summary": r.get("refined_summary", "")}
            for r in repos
        ]
        user_content = json.dumps(repos_data, ensure_ascii=False, indent=2)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        res = self.llm.call_llm(messages, use_reasoning=False, temperature=0.2)
        items = self._parse_json_from_response(res["content"])

        by_name: Dict[str, str] = {}
        for item in items:
            name = item.get("full_name")
            summary = item.get("chinese_summary", "")
            if name and summary:
                by_name[name] = summary

        result = []
        for r in repos:
            rc = r.copy()
            name = r["full_name"]
            if name in by_name:
                rc["chinese_summary"] = by_name[name]
            else:
                print(f"[Stage 5 Warning] Missing in batch response: {name}, falling back to per-repo")
                rc = self._translate_per_repo(rc)
            result.append(rc)
        return result

    def _translate_per_repo(self, r: Dict[str, Any]) -> Dict[str, Any]:
        rc = r.copy()
        system_prompt = (
            "You are an expert bilingual technical editor.\n"
            "Translate the provided English technical analysis into elegant, natural, professional Chinese.\n\n"
            "Rules:\n"
            "- Keep industry-standard English acronyms untranslated.\n"
            "- Maintain the exact 3-section markdown header structure in Chinese:\n"
            "  ### 核心解决的工程痛点\n"
            "  ### 底层架构与工程设计\n"
            "  ### 极客实战与工作流落地"
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
            print(f"[Stage 5 Warning] Per-repo also failed for {r['full_name']}: {e}")
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
