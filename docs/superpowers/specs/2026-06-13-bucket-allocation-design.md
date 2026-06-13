# Bucket Allocation & Freshness Prioritization — 设计文档

> 日期：2026-06-13
> 状态：草稿
> 关联：Stage 1.5 Dedup → Stage 2 Analyze → Stage 3+4 改造

## 问题陈述

当前日报中新兴/早期项目被高星老项目压制的根本原因：

1. **去重门槛高**：star ≥ 10,000 且出现 ≥ 3 次才进冷却，5k-9k stars 的中频常客永远不触发
2. **Trending 马太效应**：高星项目越🔥越容易继续上榜
3. **Stage 2 LLM 偏好**：LLM 更倾向给大项目打高 rating
4. **冷却期短**：30 天后老项目原地复活

## 方案：Bucket 分配引擎

### 配额策略

单期 9 个 repo，按 3-3-3 三等分：

| Bucket | 席位数 | 定位 |
|:---|:---:|:---|
| Early Bird | 3 | < 3k stars 或首次出现的中等仓库（< 5k） |
| High-Star Hot | 3 | ≥ 10k stars 或 period_stars ≥ 500 |
| Deep Dive | 3 | 按技术深度评分 TDS 选取的优质项目 |

### 匹配逻辑（优先序）

```
repo 先去 Early Bird 池→ 命中 3 个后剩余进全量池
repo 再去 High-Star Hot 池 → 命中 3 个后剩余进全量池
全量池按 TDS 排序补满 9 席
  - < 9 个 → 降额输出
  - > 9 个 → TDS 末尾淘汰到 9
```

### Pipeline 插入点

```
Stage 1 Crawl → Stage 1.5 Dedup
  ↓
[BUCKET ENGINE] ← 新增，在 Stage 2 之前
  ├─ 标记每个 repo: is_early_bird, is_high_star, technical_depth
  └─ 按 3-3-3 产出 9 个 curated repo
  ↓
Stage 2 Analyze (LLM batch)
  ↓
Stage 3+4 Per-repo
  ↓
...
```

## 新鲜度元数据

### `filter_active()` 新增输出

当前：`(active_repos, cooled_repos)`
改为：`(active_repos, cooled_repos, first_seen_map)`

`first_seen_map: Dict[str, bool]` 标记每个 full_name 是否首次出现在日报中（`repo_history.json` 无记录）。

### Early Bird 条件

```python
repo["is_early_bird"] = (
    stars < 3000
    OR (stars < 5000 AND first_seen_map.get(full_name, True))
)
```

## Technical Depth Score (TDS)

### 三档制（适配 flash-lite 能力）

| 档位 | 标签 | 含义 |
|:---:|:---|:---|
| **T** | 技术硬核 | 底层架构创新、性能突破、系统编程、自研引擎、Apple Silicon 底层优化（Metal 着色器、Core ML 内核、编译器扩展） |
| **E** | 工程优质 | 开发效率工具、工作流编排、Agent 框架、RAG 管道、苹果生态（Raycast 插件、SwiftUI 组件、MLX 工具、快捷指令 Workflow、IPA 分析、macOS 窗口管理） |
| **S** | 标准项目 | 配置/文档/封装类、标准教程、简单小工具 |

### LLM Prompt 改动（Stage 2）

在 system prompt 的 Criteria 段新增：

```
5. Technical Depth (T/E/S): Classify each selected repo's engineering depth:
   - T (Technical): Core architecture innovation, system-level breakthrough, custom CUDA/Metal, novel algorithm
   - E (Engineering): Solid tooling, well-crafted framework, practical workflow orchestration
   - S (Standard): Configuration, documentation, wrapper, basic tutorial
   If unsure, default to E.
```

### 规则引擎覆盖（校验 LLM 输出的合理性）

规则引擎作为 **sanity check**：LLM 返回的 TDS 之后，规则引擎验证其合理性。若 LLM 给了明显不合理的评分（如简单 CLI 工具给了 T），则规则值覆盖 LLM 值。

```python
T_KEYWORDS = ["mla", "moe", "attention", "cuda kernel", "kv cache",
              "compiler", "runtime", "metal", "custom shader",
              "new language", "database engine", "protocol"]
E_KEYWORDS = ["agent", "rag", "mcp", "inference", "optimiz",
              "cli", "raycast", "swiftui", "core ml", "mlx",
              "comfyui", "workflow", "automation", "xcode",
              "mach-o", "ipa", "window manager"]
S_FALLBACK = True  # 关键词匹配后还不中的走 S
```

规则优先级高于 LLM：若 LLM 给 T 但规则判定明显不合理时，以规则为准。

## 配置化

```yaml
# config.yaml 新增
bucket_allocation:
  enabled: true
  total_slots: 9
  early_bird: 3
  high_star_hot: 3
  deep_dive: 3
```

后续可调比例、总席位数。

## 文件变更清单

| 文件 | 变更内容 |
|:---|:---|
| `src/dedup.py` | `filter_active()` 新增 `first_seen_map` 返回值；增加 `is_first_seen()` 方法 |
| `src/pipeline.py` | 新增 Bucket 分配引擎类/方法；Stage 2 prompt 加 `technical_depth` 字段；移除旧 `_prefilter_top_n()` 或改造为 bucket 引擎 |
| `src/config.py` | 新增 `BucketAllocationConfig` Pydantic 模型 |
| `config/config.yaml` | 新增 `bucket_allocation` 配置段 |
| `config/personas.yaml` | intermediate 画像新增 bucket 分配策略的写作指引 |

## 不做的事

- ❌ 不改 `formatter.py` / `notifier.py`
- ❌ 不改 `crawler.py`
- ❌ 不改 `templates/`
- ❌ 不改去重阈值（`high_star_threshold` / `archive_threshold` / `archive_cooldown_days`）