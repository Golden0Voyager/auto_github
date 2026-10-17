"""Model probe: 用真实请求给候选免费模型定位，不改报告、不写产物。

只在 model-probe.yml 的 workflow_dispatch 里跑。每个模型打四发固定探针，
分别对应管线的四个环节，打印服从度/延迟/token 的对照表。

  T1 classifier  —— 三个仓库的 batch 严格 JSON（我们靠解析成功与否决定降级）
  T2 writer      —— SELF-CHECK 行 + 4 个逐字英文 ### 小节
  T3 reviewer    —— 只准回一个字母 A/B
  T4 translator  —— 英译中并保留 4 个 ### 锚点

用法：uv run python scripts/probe_models.py [--only gemma,super-vl]
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from typing import Any

BASE_URLS = {
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "sensenova": ("SENSENOVA_API_KEY", "https://token.sensenova.cn/v1"),
    "siliconflow": ("SILICONFLOW_API_KEY", "https://api.siliconflow.cn/v1"),
}

CANDIDATES = [
    ("openrouter", "google/gemma-4-31b-it:free"),
    ("openrouter", "google/gemma-4-26b-a4b-it:free"),
    ("openrouter", "nvidia/nemotron-3.5-super-vl:free"),
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
    ("openrouter", "nvidia/nemotron-3.5-lightning:free"),
    ("openrouter", "liquid/lfm-2.5-2.6b:free"),
    ("openrouter", "thinkingmachines/inkling-small:free"),
    ("sensenova", "sensenova-6.8-flash-lite"),
    ("sensenova", "deepseek-flash"),
    ("siliconflow", "tencent/Hunyuan-MT-7B"),
    ("siliconflow", "Qwen/Qwen2.5-7B-Instruct"),
]

REQUEST_GAP_SECONDS = 2.0
MAX_TOKENS = 2048

T1_SYSTEM = (
    "You are a GitHub open-source analyst. Rate each repository in the batch and "
    "return a strictly valid JSON array, one object per repository, no prose. "
    "Each object must have exactly these keys: ['index', 'full_name', 'rating', "
    "'tags', 'tds', 'reason_for_selection']. rating is one of S/A/B/C, tds is one "
    "of T/E/S, tags is 2-3 hashtags, reason_for_selection is at most 2 sentences. "
    "Process ALL repositories provided."
)
T1_BATCH = [
    {"index": 0, "full_name": "vllm-project/vllm-ascend", "description":
     "Enables vLLM on Ascend NPUs with quantized weight loading and PagedAttention kernels.",
     "language": "Python", "stars": 2988, "source": "llm_giant"},
    {"index": 1, "full_name": "gpt4all-io/gpt4all", "description":
     "Desktop app to run local LLMs offline with embeddings and rag support.",
     "language": "Python", "stars": 53000, "source": "trending"},
    {"index": 2, "full_name": "comfyanonymous/ComfyUI_examples", "description":
     "Example workflows and images for ComfyUI node graphs including ControlNet.",
     "language": "HTML", "stars": 4800, "source": "trending"},
]

T2_SYSTEM = (
    "Write a technical analysis of the following GitHub project in English. FIRST line "
    "must be a one-sentence self-check formatted exactly as:\n"
    "SELF-CHECK: <one sentence>\n\nTHEN the analysis in this 4-section structure, keeping "
    "the ### headers exactly as shown, in English:\n\n### Core Pain Point Solved\n"
    "### Design & Architectural Trade-offs\n### Engineering Insights & Transferable Lessons\n"
    "### Ecosystem & Related Projects\n\nGround every claim in the metadata provided. "
    "No marketing fluff, no first person."
)
T2_REPO = (
    "Repository: huggingface/trl\nStars: 14200\nLanguage: Python\n"
    "Description: Transformer Reinforcement Learning — trainer classes for SFT, reward "
    "modelling, PPO and DPO with dataset integration.\nTags: #RLHF, #DPO, #Transformers\n"
    "Rating: S\nTechnical depth: T\n"
    "README excerpt: trl provides trainer classes for transformer models: SFTTrainer, "
    "RewardTrainer, PPOTrainer, DPOTrainer. It handles distributed training via accelerate, "
    "exposes keyword-only hyperparameters, and keeps dataset handling in one layer."
)
T2_HEADERS = (
    "### Core Pain Point Solved",
    "### Design & Architectural Trade-offs",
    "### Engineering Insights & Transferable Lessons",
    "### Ecosystem & Related Projects",
)

T3_PROMPT = (
    "Compare these two Chinese translations of the same English text. Pick the one that "
    "is more accurate, natural, and readable.\nOutput ONLY \"A\" or \"B\".\n\n"
    "--- Translation A ---\n### 要解决的核心痛点\n标准实现，社区驱动的设计决策。本轮分析暂由兜底策略生成。\n\n"
    "--- Translation B ---\n### Core Pain Point Solved\nStandard implementation with "
    "community-driven design decisions. Deeper analysis pending this cycle.\n"
)

T4_SYSTEM = (
    "Translate the following English technical analysis into natural Chinese. Preserve all "
    "paragraph breaks and markdown formatting, and keep every '###' section header line."
)
T4_TEXT = (
    "### Core Pain Point Solved\nLocal RLHF stacks usually glue four separate repos "
    "together: tokenizers, rollout, reward model, and optimiser. Every glue layer re-tokenises "
    "the same batch, so GPU time is spent moving data instead of training.\n\n"
    "### Design & Architectural Trade-offs\nThe library keeps datasets in one layer and "
    "exposes keyword-only hyperparameters. That costs discoverability, but makes a failed run "
    "reproducible from a single config file.\n\n"
    "### Engineering Insights & Transferable Lessons\nWhen a pipeline has four stages, own "
    "the boundaries rather than the stages: one place to attach tokens and one place to detach "
    "them is what removes silent off-by-one mistakes.\n\n"
    "### Ecosystem & Related Projects\nPair it with an inference server that reports KV-cache "
    "hit rates, so training and serving can be compared on the same latency budget."
)


def _client(provider: str) -> Any:
    import os

    from openai import OpenAI

    env_key, url = BASE_URLS[provider]
    key = os.getenv(env_key)
    if not key:
        raise RuntimeError(f"missing {env_key}")
    return OpenAI(api_key=key, base_url=url, max_retries=0)


def _ask(client: Any, model: str, system: str, user: str, temperature: float) -> tuple[str, int, int, float]:
    started = time.monotonic()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=MAX_TOKENS,
        temperature=temperature,
        timeout=120,
    )
    elapsed = time.monotonic() - started
    usage = getattr(resp, "usage", None)
    text = (resp.choices[0].message.content or "") if getattr(resp, "choices", None) else ""
    return text, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0, elapsed


def _extract_json(text: str) -> Any:
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def probe_t1(client: Any, model: str) -> str:
    text, _in, _out, _ms = _ask(client, model, T1_SYSTEM, json.dumps(T1_BATCH, ensure_ascii=False), 0.2)
    data = _extract_json(text)
    if not isinstance(data, list):
        return "JSON✗"
    idx = {int(o["index"]) for o in data if isinstance(o, dict) and str(o.get("index", "")).isdigit()}
    valid = {o for o in data if isinstance(o, dict) and o.get("rating") in {"S", "A", "B", "C"}
             and o.get("tds") in {"T", "E", "S"}}
    return f"JSON✓ {len(idx)}/3齐全 合规{len(valid)}/3"


def probe_t2(client: Any, model: str) -> str:
    text, _in, out, _ms = _ask(client, model, T2_SYSTEM, T2_REPO, 0.3)
    hits = sum(1 for h in T2_HEADERS if h in text)
    check = "SELF-CHECK✓" if re.search(r"^\s*SELF-CHECK:", text, re.M | re.I) else "SELF-CHECK✗"
    return f"{check} 小节{hits}/4 out={out}"


def probe_t3(client: Any, model: str) -> str:
    text, _in, _out, _ms = _ask(client, model, "", T3_PROMPT, 0.1)
    cleaned = text.strip().upper()
    if cleaned.startswith("A"):
        return "严格A/B✓ (A)"
    if cleaned.startswith("B"):
        return "严格A/B✓ (B)"
    return f"严格A/B✗ ({text.strip()[:18]!r})"


def probe_t4(client: Any, model: str) -> str:
    text, _in, out, _ms = _ask(client, model, T4_SYSTEM, T4_TEXT, 0.2)
    headers = re.findall(r"^###\s*(.+?)\s*$", text, re.M)
    cjk = len(re.findall(r"[一-鿿]", text))
    kept_en = sum(1 for h in headers if not re.search(r"[一-鿿]", h))
    return f"锚点{len(headers)}/4 中文{cjk}字 未译标题{kept_en} out={out}"


PROBES = (("T1", probe_t1, 0.2), ("T2", probe_t2, 0.3), ("T3", probe_t3, 0.1), ("T4", probe_t4, 0.2))


def run(model_filter: str | None, probe_filter: str | None) -> int:
    wanted = {p.strip().upper() for p in probe_filter.split(",")} if probe_filter else None
    probes = [p for p in PROBES if wanted is None or p[0] in wanted]
    rows: list[dict[str, Any]] = []
    for provider, model in CANDIDATES:
        if model_filter and model_filter.lower() not in f"{provider}/{model}".lower():
            continue
        try:
            client = _client(provider)
        except Exception as e:
            rows.append({"model": f"{provider}/{model}", "error": str(e)[:48]})
            continue
        record: dict[str, Any] = {"model": f"{provider}/{model}"}
        latencies: list[float] = []
        for label, fn, _temp in probes:
            try:
                started = time.monotonic()
                record[label] = fn(client, model)
                latencies.append(time.monotonic() - started)
            except Exception as e:
                record[label] = f"ERR {str(e)[:44]}"
            time.sleep(REQUEST_GAP_SECONDS)
        record["p50"] = f"{statistics.median(latencies):.1f}s" if latencies else "-"
        rows.append(record)
        print(f"[done] {record['model']} → " + " | ".join(f"{k}={v}" for k, v in record.items() if k not in ("model",)),
              flush=True)

    labels = [p[0] for p in probes]
    print("\n## 探测结果\n")
    print("| 模型 | " + " | ".join(labels) + " | 单次p50 |")
    print("| :--- | " + " | ".join(":---" for _ in labels) + " | :--- |")
    for r in rows:
        if "error" in r:
            print(f"| {r['model']} | 无 key/跳过: {r['error']} | |")
            continue
        cells = " | ".join(str(r.get(label, "-")) for label in labels)
        print(f"| {r['model']} | {cells} | {r.get('p50', '-')} |")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="只测包含该子串的模型", default=None)
    parser.add_argument("--probes", help="只跑这些探针，逗号分隔，如 T3,T4", default=None)
    args = parser.parse_args(argv)
    return run(args.only, args.probes)


if __name__ == "__main__":
    sys.exit(main())
