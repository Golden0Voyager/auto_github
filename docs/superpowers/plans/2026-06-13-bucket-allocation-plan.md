# Bucket Allocation Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 3-bucket allocation engine (Early Bird / High-Star Hot / Deep Dive) with 3-3-3 quota to replace `_prefilter_top_n()` and improve content diversity.

**Architecture:** A new bucket engine class inserted between Stage 1.5 Dedup and Stage 2 Analyze. Stage 2 prompt gains `technical_depth` field (T/E/S). `filter_active()` returns `first_seen_map`. Config gains `bucket_allocation` section. `_prefilter_top_n()` is removed.

**Tech Stack:** Python, Pydantic, sensenova flash-lite LLM.

---

### Task 1: Add BucketAllocationConfig to config.py + config.yaml

**Files:**
- Modify: `src/config.py:36-48`
- Modify: `config/config.yaml`

**Background:** Current `Stage2PreFilterConfig` will be deprecated by the bucket engine. A new `BucketAllocationConfig` is needed.

**Note:** Because `AppConfig` has `stage2_pre_filter` and the bucket engine replaces its role, keep `stage2_pre_filter` for backward compatibility but set its `enabled: false` in yaml. The bucket engine references the new config.

- [ ] **Step 1: Add BucketAllocationConfig to config.py**

Add after DedupConfig (around line 35):

```python
class BucketAllocationConfig(BaseModel):
    """三桶分配引擎配置：Early Bird / High-Star Hot / Deep Dive 按配额分配。"""
    enabled: bool = True
    total_slots: int = 9
    early_bird: int = 3
    high_star_hot: int = 3
    deep_dive: int = 3
```

Add to AppConfig (around line 60):

```python
class AppConfig(BaseModel):
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    bucket_allocation: BucketAllocationConfig = Field(default_factory=BucketAllocationConfig)
    stage2_pre_filter: Stage2PreFilterConfig = Field(default_factory=Stage2PreFilterConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
```

Also import BucketAllocationConfig in `__init__.py` or directly — since config.py has no `__init__.py`, just add the class.

- [ ] **Step 2: Update config.yaml**

Add at the end of config.yaml:

```yaml
bucket_allocation:
  enabled: true
  total_slots: 9
  early_bird: 3
  high_star_hot: 3
  deep_dive: 3
```

Also set `stage2_pre_filter.enabled: false` since bucket engine replaces it, or keep it enabled as a safety net. We'll keep it enabled=false to avoid confusion.

- [ ] **Step 3: Verify**

```bash
cd /Users/hainingyu/Code/auto_github && uv run python -c "
from src.config import load_config
c = load_config()
print('bucket_allocation:', c.bucket_allocation)
print('enabled:', c.bucket_allocation.enabled)
print('total_slots:', c.bucket_allocation.total_slots)
"
```

Expected: prints the config values correctly.

---

### Task 2: Update filter_active() to return first_seen_map

**Files:**
- Modify: `src/dedup.py:110-125`

**Background:** Early Bird bucket needs to know which repos are appearing for the first time. `filter_active()` currently returns `(active, cooled)`. It needs a third value: `first_seen_map: Dict[str, bool]`.

- [ ] **Step 1: Update filter_active() return type**

Change the signature and implementation:

```python
    def filter_active(self, repos: List[Dict]) -> Tuple[List[Dict], List[Dict], Dict[str, bool]]:
        """从原始抓取结果中过滤掉仍在冷却期内的存档项目。

        Returns:
            (active_repos, cooled_repos, first_seen_map) —— active 用于进入策展管线；
            cooled 仅用于日志/统计；first_seen_map 标记每个 full_name 是否首次出现。
        """
        active: List[Dict] = []
        cooled: List[Dict] = []
        first_seen_map: Dict[str, bool] = {}
        for r in repos:
            name = r.get("full_name", "")
            if self._cooldown_active(name):
                cooled.append(r)
            else:
                active.append(r)
            # 标记首次出现：不在 history 中则 first_seen=True
            first_seen_map[name] = name not in self._history
        return active, cooled, first_seen_map
```

- [ ] **Step 2: Update pipeline.py call site**

Find where `filter_active()` is called in `pipeline.py` (around line 250). Update the unpacking:

```python
active_repos, cooled_repos, first_seen_map = self.dedup.filter_active(raw_repos)
```

And after unpacking, inject `first_seen` into each repo dict for later bucket engine use:

```python
for r in active_repos:
    r["is_first_seen"] = first_seen_map.get(r.get("full_name", ""), False)
```

- [ ] **Step 3: Run tests**

```bash
cd /Users/hainingyu/Code/auto_github && uv run python -m pytest tests/test_dedup.py -v --tb=short 2>&1
```

Expected: All dedup tests PASS. If some fail, update them for the new 3-tuple return type.

---

### Task 3: Build bucket allocation engine

**Files:**
- Modify: `src/pipeline.py`

**Background:** Insert the bucket engine as a new method `_bucket_allocate()` that runs after Stage 1.5 dedup and replaces `_prefilter_top_n()`. The engine classifies repos into 3 buckets and selects 3 from each.

#### TDS Keywords

```python
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
```

- [ ] **Step 1: Replace `_prefilter_top_n()` with the bucket engine**

Find `_prefilter_top_n()` method (around line 215). Replace it with:

```python
    def _bucket_allocate(self, repos: List[Dict[str, Any]], first_seen_map: Dict[str, bool]) -> List[Dict[str, Any]]:
        """三桶分配引擎：按 Early Bird / High-Star Hot / Deep Dive 配额选出 9 个 repo。

        取代旧的 _prefilter_top_n()，在 Stage 2 之前运行。
        """
        cfg = self.config.bucket_allocation
        if not cfg.enabled or len(repos) <= cfg.total_slots:
            # 如果 bucket 引擎关闭或 repo 数量不足，直接返回
            if len(repos) <= cfg.total_slots:
                return repos
            # 关闭时按 star 降序截断
            sorted_repos = sorted(repos, key=lambda r: r.get("stars", 0), reverse=True)
            return sorted_repos[:cfg.total_slots]

        # 1. 计算每个 repo 的 TDS 和 bucket 标签
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
            is_first_seen = first_seen_map.get(name, False)
            desc = (r.get("description", "") or "").lower()

            # 计算 TDS（规则引擎）
            tds = self._infer_tds(desc)

            # 分配 bucket
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

        # 2. 从每个池中取 cfg 个
        def take(pool, n):
            pool.sort(key=lambda x: -x.get("stars", 0))
            return pool[:n], pool[n:]

        result = []
        eb_taken, eb_rest = take(early_bird_pool, cfg.early_bird)
        hs_taken, hs_rest = take(high_star_pool, cfg.high_star_hot)
        # 剩余未满额的，被淘汰的 repo 回到全量池
        leftover = eb_rest + hs_rest + all_remaining
        # Deep Dive 按 TDS 排序，E > T > S，同档按 star 降序
        tds_order = {"T": 0, "E": 1, "S": 2}
        leftover.sort(key=lambda x: (tds_order.get(x.get("tds", "S"), 3), -x.get("stars", 0)))
        dd_taken = leftover[:cfg.deep_dive]

        result = eb_taken + hs_taken + dd_taken

        # 3. 如果不足 9 个，补满（按 star 降序从 leftover 补）
        if len(result) < cfg.total_slots:
            remaining_more = leftover[cfg.deep_dive:]
            remaining_more.sort(key=lambda r: -r.get("stars", 0))
            needed = cfg.total_slots - len(result)
            result.extend(remaining_more[:needed])

        # 4. 如果超出 9 个，末尾淘汰
        if len(result) > cfg.total_slots:
            # 先保持 bucket 内排序，再整体按 bucket 优先级排序
            result.sort(
                key=lambda x: (
                    0 if x.get("_bucket") == "early_bird"
                    else 1 if x.get("_bucket") == "high_star"
                    else 2,
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
        """规则引擎判定 Technical Depth Score (T/E/S)。

        优先级: T > E > S。匹配任意 T 关键词则返回 T，否则匹配 E，否则 S。
        """
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
        desc_lower = desc.lower()
        for kw in T_KEYWORDS:
            if kw in desc_lower:
                return "T"
        for kw in E_KEYWORDS:
            if kw in desc_lower:
                return "E"
        return "S"
```

- [ ] **Step 2: Remove `_prefilter_top_n()` call and insert `_bucket_allocate()`**

In `pipeline.py:run()`, find where `_prefilter_top_n()` is called (around line 272-276). Replace with:

```python
        # --- Stage 1.75: Bucket Allocation (取代旧的 Pre-filter) ---
        if self.config.bucket_allocation.enabled:
            active_repos = self._bucket_allocate(active_repos, {r["full_name"]: r.get("is_first_seen", False) for r in active_repos})
        else:
            active_repos = self._prefilter_top_n(active_repos)
        if not active_repos:
            print("[Pipeline Info] All repos filtered out by bucket allocation. Aborting.")
            return { ... }  # same early return as before
```

Also add import for re at the top of pipeline.py if not already imported (it already is).

- [ ] **Step 3: Run mock pipeline**

```bash
cd /Users/hainingyu/Code/auto_github && uv run python src/main.py --mock --persona intermediate
```

Expected: Pipeline runs, output shows `[Bucket Alloc] N → 9 repo (早鸟:X 高星:Y 深潜:Z)`.

Then clean up:
```bash
git checkout -- reports/repo_history.json reports/latest_daily.md
rm -f reports/daily_*.md
```

---

### Task 4: Update Stage 2 Analyze prompt for technical_depth

**Files:**
- Modify: `src/pipeline.py` (Stage 2 system prompt)

**Background:** The current Stage 2 batch prompt returns `[index, full_name, rating, tags, reason_for_selection]`. Add `technical_depth: "T" | "E" | "S"` field.

- [ ] **Step 1: Update system prompt**

Find the `_stage_analyze` system prompt (around line 427). After the tags/rating criteria, add:

```
5. Technical Depth (T/E/S): Classify each selected repo's engineering depth:
   - T (Technical): Core architecture innovation, system-level breakthrough, custom CUDA/Metal, novel algorithm, compiler/runtime engineering
   - E (Engineering): Solid tooling, well-crafted framework, practical workflow orchestration, Apple ecosystem tools (Raycast, SwiftUI, MLX, CoreML), dev productivity
   - S (Standard): Configuration, documentation, wrapper, basic tutorial
   If unsure, default to E.
```

And update the return JSON format instruction:

```
Return a strictly valid JSON array of selected objects containing exactly the following keys:
['index', 'full_name', 'rating', 'tags', 'reason_for_selection', 'technical_depth'].
```

- [ ] **Step 2: Update Stage 2 output parsing**

After `item.get("reason_for_selection", "")`, add:

```python
orig_repo["technical_depth"] = item.get("technical_depth", "E")
```

- [ ] **Step 3: Add rule engine overlay (sanity check)**

After the parsed items loop, add the rule engine overlay:

```python
# TDS 规则引擎覆盖（sanity check on LLM output）
T_KEYWORDS = ["mla", "moe", "attention", "cuda kernel", "kv cache",
              "compiler", "runtime", "metal", "custom shader",
              "new language", "database engine", "protocol"]
E_KEYWORDS = ["agent", "rag", "mcp", "inference", "optimiz",
              "cli", "raycast", "swiftui", "core ml", "mlx",
              "comfyui", "workflow", "automation", "xcode",
              "mach-o", "ipa", "window manager"]

for r in analyzed_repos:
    desc = (r.get("description", "") or "").lower()
    rule_tds = _infer_tds_fallback(desc)
    llm_tds = r.get("technical_depth", "E")
    # 如果 LLM 给了明显不合理的评分，以规则为准
    # 例如 LLM 给 T 但规则判 S → 降级
    if rule_tds != llm_tds:
        print(f"  [TDS Override] {r['full_name']}: LLM={llm_tds} → Rule={rule_tds}")
        r["technical_depth"] = rule_tds
```

And add `_infer_tds_fallback` as a module-level function (alongside the other `_infer_*_fallback` functions):

```python
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
```

Also update mock fallback in `_stage_analyze` to include technical_depth.

- [ ] **Step 4: Run mock pipeline**

```bash
cd /Users/hainingyu/Code/auto_github && uv run python src/main.py --mock --persona intermediate
```

Expected: Pipeline runs, bucket allocation prints bucket counts.

---

### Task 5: Update personas.yaml with bucket-awareness

**Files:**
- Modify: `config/personas.yaml`

**Background:** The intermediate persona should be aware that the report now has 3 buckets so the LLM can adjust its writing accordingly.

- [ ] **Step 1: Update intermediate prompt_focus**

In `config/personas.yaml` intermediate section, add a note after the 4-section structure:

```
    Bucket awareness:
      - The report is divided into 3 buckets: Early Bird (emerging projects < 3k stars),
        High-Star Hot (> 10k stars), and Deep Dive (technical depth focus).
      - For Early Bird repos, emphasize "why this matters even though it's small"
      - For High-Star Hot repos, focus on "what's new or what's the key insight"
      - For Deep Dive repos, maximize technical depth and transferable lessons
```

---

### Task 6: Update tests

**Files:**
- Modify: `tests/test_pipeline.py` (mock stage analyze test)
- Modify: `tests/test_pipeline_batch.py` (stage 2 analyze test)

**Background:** The mock stages and stage 2 tests need technical_depth field.

- [ ] **Step 1: Update mock test data**

in `tests/test_pipeline.py::TestStageAnalyzeMock`, the mock ratings/tags_list/reasons lists use `repos[:7]` mock data. Add `technical_depth: "E"` default to mock output:

In the mock data dict:

```python
rc["technical_depth"] = "E"  # default for all mock repos
```

- [ ] **Step 2: Update stage 2 LLM mock test**

In `tests/test_pipeline_batch.py::TestStage2AnalyzeLLMPath`, update the mock LLM return to include technical_depth:

```python
client.call_llm.return_value = {
    "content": json.dumps([
        {
            "index": 0,
            "full_name": "deepseek-ai/DeepSeek-V3",
            "rating": "S",
            "tags": ["#MoE"],
            "reason_for_selection": "Top pick.",
            "technical_depth": "T",
        }
    ])
}
```

- [ ] **Step 3: Add bucket allocation test**

Add a test to verify the bucket allocation engine:

```python
def test_bucket_allocation_selects_9(self, batch_config):
    """Bucket allocation should select 9 repos from a larger pool."""
    ...
```

- [ ] **Step 4: Run full test suite**

```bash
cd /Users/hainingyu/Code/auto_github && uv run python -m pytest tests/ -v --tb=short 2>&1
```

Expected: All tests PASS (except pre-existing auto_hub failures).

---