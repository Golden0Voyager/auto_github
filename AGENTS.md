# AGENTS.md — auto_github

AI 智能体策展系统：每日抓取 GitHub Trending 与大厂动态，经 6 阶段 LLM 管线
(Crawl → Dedup → Analyze → Summarize → Reflect → Translate → Refine Layout)，
生成多端（Markdown / 飞书卡片 / Slack Block / Discord）策展报告。

本仓库是**单 CLI 入口的工具型项目**，不是库，也没有测试套件。

---

## 命令速查

```bash
# 装依赖（按全局约定使用 uv；如不存在 venv 才创建）
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -r requirements.txt

# 离线沙盒（0 token / 0 网络，用于排版与通知链路验证）—— 唯一安全的本地验证手段
python src/main.py --mock --persona intermediate

# 真实抓取 + LLM 策展
python src/main.py --since daily  --persona intermediate
python src/main.py --since weekly --persona advanced
```

> 不要新增 `pyproject.toml` / 测试框架 / linter —— 仓库刻意保持极简。`--mock`
> 是本仓唯一的"测试"，它会跑完 6 个 stage 走 mock 数据并产出一份 `reports/latest_daily.md`。
>
> ⚠️ `--mock` 会**污染** `reports/repo_history.json`（往状态文件追加 mock 仓库的当日记录），
> 以及覆盖 `reports/latest_daily.md` 和生成 `reports/daily_<时间戳>.md`。每次 `--mock` 后用
> `git checkout -- reports/repo_history.json reports/latest_daily.md` 还原 + `rm reports/daily_<时间戳>.md` 清理，
> 否则下次 Actions 跑会和本地状态打架。

---

## 关键事实（agent 容易踩坑）

### 入口与运行时

- **唯一入口**：`src/main.py`。`sys.path.insert(0, BASE_DIR)` 在 main.py:8 手动注入根目录，
  因此 `python src/main.py` 必须在仓库根运行，不要 `cd src && python main.py`。
- **CLI 选项**：`--since {daily,weekly,monthly}`、`--persona {beginner,intermediate,advanced}`、
  `--mock`、`--feishu/--slack/--discord <webhook>`（覆盖 config/env）。
- **必填环境变量只有 1 个**：`SENSENOVA_API_KEY`（`sk-...`）。
  其余 `FEISHU_WEBHOOK_URL` / `SLACK_WEBHOOK_URL` / `DISCORD_WEBHOOK_URL` / `GITHUB_TOKEN` 都是可选。
- `python-dotenv` 通过 `load_dotenv()`（main.py:56）从 CWD 加载 `.env`。

### LLM 引擎（容易混淆）

- **唯一活跃 provider = sensenova**，模型统一是 `sensenova-6.7-flash-lite`
  （`config.yaml` 里 `model_v3` 和 `model_r1` 是同一字符串）。
- **DeepSeek-R1 已下线** —— `llm.py:46-48` 注释明示：原 R1 的 `reasoning_content`
  思考链不再返回。`call_llm(..., use_reasoning=True)` 仍可调用，但实际拿到的是 flash-lite 的
  普通 `content`，不是真推理链。Stage 3+4 的"反思"效果来自 prompt 约束，不再来自 R1。
- `sensenova-6.7-flash-lite` Token Plan 端点是 `https://token.sensenova.cn/v1`（**不是**
  `api.sensenova.cn`），默认硬编码在 `config.py:70`。
- 429 限流由 `LLMClient.call_llm` 自动指数退避（最多 5 次），`rate_limit_delay: 2.0s`
  在每次成功调用后还会睡 `delay/2`，是节流大头。

### LLM 调用次数（T2 节流后）

管线全程**只调 2 次 LLM**（之前是 1+3N 次）：

| 阶段 | 调用 | 说明 |
| :--- | :---: | :--- |
| Stage 2 Analyze | 1 (batch) | N 个 repo 一次性分类 + 评级 |
| **Stage 3+4 Summarize+Reflect** | **1 (batch)** | 合并为 1 个 LLM 调，要求返回 JSON 数组；旧 per-repo 反思已并入 prompt 约束 |
| **Stage 5 Translate** | **1 (batch)** | 一次性翻译 N 条；旧 per-repo 翻译已并入 batch |
| **合计** | **2 次** | N=9 时从 28 次降到 2 次（~93% ↓） |

- **降级路径**：batch 解析失败时，per-repo 串行回退（最多 1+N 次）。
  per-repo 也失败时，Stage 3+4 走静态 stub，Stage 5 回退到英文原文。
- **代码入口**：`pipeline._stage_summarize_and_reflect` / `pipeline._stage_translate`，
  对应 batch 实现分别是 `_summarize_reflect_batch` / `_translate_batch`，per-repo 回退是 `*_per_repo`。
- **响应格式**：两处 batch 都用 `_parse_json_from_response` 解 JSON 数组
  `[{"full_name": "...", "<field>": "..."}]`，匹配不到某个 full_name 自动降级 per-repo。

### 抓取行为

- 即便 `--since weekly`，Stage 1 仍会**同时**抓 daily/weekly/monthly 三榜各 top 10，
  然后按 `full_name` 去重（见 `pipeline.py:144-166`）。`--since` 只影响后续分析的时间窗标识。
- `monitored_orgs` / `monitored_users` 来自 `config/config.yaml` —— 修改时注意
  GitHub API 速率（每用户匿名 60/h，配 `GITHUB_TOKEN` 后 5000/h）。

### 高🌟项目去重（重要状态文件）

- `reports/repo_history.json` —— 每个 full_name 出现过的日期列表（同日去重）。
- `reports/high_star_archive.json` —— 进入 30 天冷却期的项目。
- 触发条件（`config.yaml:dedup` 段）：`star ≥ 10000` **且** 累计出现 `≥ 3` 次。
- 冷却期满后由 `purge_expired_cooldowns()`（dedup.py:179）自动清理并允许重新进入策展。
- 这两个文件**不能 gitignore**：它们是系统的"记忆"，丢失会导致项目被反复推送并破坏去重不变量。
  workflow 也会随报告一并 commit 回去。

### 画像（personas）

- `intermediate` 是默认画像，也是配置最严格的 —— `config/personas.yaml:20` 有**通用普适性硬性约束**：
  LLM 输出**严禁**提到任何具体个人姓名（包括"海宁"/"于海宁"/"Haining"/"Golden0Voyager"）、
  个人背景（MFA / 当代艺术 / 美术馆等）、第一人称。所有措辞必须面向"广大中阶 LLM 实践者"。
  修改 intermediate 的 prompt 时这条不能动。
- `beginner` 强调"能帮我干什么"、WebUI/即插即用；`advanced` 走 MoE/MLA/RLHF/MCTS 学术语系。

### 多端通知的坑

- **Discord**：`notifier.py:115` 在 markdown 长度 > 1950 字符时硬截断到 1900
  并追加截断提示，**没有分页**。长报告会丢尾部。
- **Feishu** 走 `templates/feishu_card.json.j2`（互动卡片），**Slack** 走 `slack_blocks.json.j2`
  （Block Kit），两者结构不同 —— 改模板时不要跨通道互借字段。
- 推送失败不会中断主流程，会在 main.py:121-123 打印 ❌ 后继续退出码 0。

### GitHub Actions（不是普通的 CI）

- 定时 `0 2 * * *` UTC = 北京时间 10:00；也支持 `workflow_dispatch` 手触发，
  可选 `since` / `persona` 入参。
- workflow 跑完后会**自动 git commit** `reports/` 下所有变更并 `push` 回 main，
  commit 信息带 `[skip ci]` 防递归（`.github/workflows/daily_trending.yml:81`）。
- push 阶段有 3 次重试 + `--rebase -X theirs` 处理并发竞态。

---

## 项目结构

```
src/
  main.py         CLI 入口（参数解析 + 装配 4 个组件）
  config.py       Pydantic 配置模型 + load_config()（YAML + env 覆盖）
  crawler.py      BeautifulSoup 抓 GitHub Trending HTML + REST API 拉 org/user repos
  dedup.py        RepoHistoryTracker：30 天冷却 + 原子写 history/archive
  llm.py          LLMClient：OpenAI 兼容 + 429 指数退避
  pipeline.py     6 阶段编排 + JSON 解析容错（_parse_json_from_response）
  formatter.py    Jinja2 渲染 Markdown / 飞书 / Slack 三种格式
  notifier.py     4 通道分发：local(必) + feishu/slack/discord(可选)
config/
  config.yaml     系统配置（监控列表、AI 模型、去重阈值、通知 webhook）
  personas.yaml   3 画像的 system prompt_focus
templates/        *.j2 Jinja2 模板
reports/
  daily_YYYY-MM-DD_HHMM.md   每日产物（Actions 会自动 commit）
  latest_daily.md            最近一次 daily 报告的副本
  repo_history.json          出现日期历史（**状态文件**）
  high_star_archive.json     冷却中项目（**状态文件**）
```

---

## 改代码时的自查清单

- 改了 persona prompt → 跑 `python src/main.py --mock --persona <key>` 看输出风格未走样。
- 改了 `config/config.yaml` 阈值 → 注意 `repo_history.json` 不会自动迁移，
  改 `archive_threshold` 不会让已记录的项目"补触发"晋升；新阈值只对**之后**的抓取生效。
- 改了 `formatter.py` 输出的 markdown 结构 → 反推 Stage 5 翻译的 system prompt
  （pipeline.py:502-512）里的 `###` 锚点必须保持一致，否则翻译对齐会断。
- 改了通知渠道 → webhook URL 不要硬编码，env / config 注入即可；CI secret 已就绪。
- 不要把 LLM 调用换成 vLLM/本地推理 —— 整套 rate-limit / 退避 / mock fallback 是按
  SenseNova Token Plan 配额（5h/1500 次）设计的。
