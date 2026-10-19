# AGENTS.md — auto_github

AI 智能体策展系统:每日抓取 GitHub Trending 与大厂动态,经多阶段 LLM 管线
(Crawl → Dedup → Bucket Allocate → Analyze → Scrape → Write&Reflect → Translate A/B → Review → Layout),
生成多端(Markdown / 飞书卡片 / Slack Block / Discord)策展报告。

本仓库是**单 CLI 入口的工具型项目**,不是库。有完整测试套件
(`tests/`,~310 个用例,coverage 门槛 85%),CI 强制执行。

---

## 设计哲学:LLM Token 经济性

LLM 调用的真实成本由**四笔账**叠加,不是单次 token 总和:

1. **请求税**:每次 API 请求都重发一遍 system prompt(`max_tokens` / 模型名 / 安全约束等固定开销不可省),并被 provider 计 1 个 request。
2. **轮次税**:每次 round-trip 是一次完整的 request/response 闭环;串行的 N 个 stage = N 次往返。
3. **token 税**:prompt 的 input + completion 的 output,按字符计费。
4. **失败税**:429 限流下,调用越多越容易撞墙;每次重试都消耗预算。

**五条原则**(按优先级排序,改代码时自上而下检验):

| # | 原则 | 含义 |
| :--- | :--- | :--- |
| 1 | **每个 token 必须为决策服务** | 出现在 prompt/completion 里的每段文本,都应该回答"它在为哪个决策贡献信息"。闲聊、复述、套话一律砍。 |
| 2 | **批处理摊销请求税** | N 个独立决策合并到 1 次 LLM 调用,让 system prompt / JSON schema / few-shot 这些固定开销被 N 摊薄。Stage 2 分类至今保持 batch;Write/Translate 的 batch 化已回退(见"调用次数")。 |
| 3 | **减少轮次 > 减少单次 token** | 固定成本比可变成本大。Stage 3+4 合并为一次 per-repo 调用(Write&Reflect)就是此原则的产物。 |
| 4 | **prompt 约束替代思考链** | 反思 / 多步推理靠 prompt 设计完成,**不靠**让模型自己思考(DeepSeek-R1 风格)。`reasoning_content` 算 token 但不产生结构化收益。 |
| 5 | **降级路径必须存在** | 批处理失败 → per-repo 回退 → 静态 stub。三层兜底保证主流程不中断;降级不是 bug,是契约的一部分。 |

**配套实践**:
- **可观测先行**:`LLMClient.get_stats()` 的 `call_count` / `failed_attempt_count` /
  `total_prompt_tokens` / `total_completion_tokens` 四件套是底线。
- **per-stage snapshot**:`pipeline._log_llm_stage()` 在 stage 边界 diff `get_stats()`,
  把成本归因到具体 stage(每次 Actions 日志里都能按 stage 看到成本)。
- **多 provider 混合节流**:节流参数是 **per-provider** 的(见 `llm.py` 的
  `PROVIDER_TIMEOUTS` / `PROVIDER_MIN_INTERVAL` 两张表),
  换 provider 或加 secret 前先重算配额。

**反模式(看到就改)**:
- ❌ system prompt 每次重新拼接历史对话
- ❌ 失败时无脑重试到 429 撞墙
- ❌ 把"反思"塞进 thinking token 而不是塞进 prompt 指令
- ❌ 没有降级路径,主流程被 batch JSON 解析失败带崩
- ❌ 测试里硬编码绝对日期(见"测试规范")

---

## 命令速查

```bash
# 装依赖(本地开发走 uv + pyproject + uv.lock)
uv sync

# 跑测试(等价 CI;coverage 门槛 85% 在 pyproject 的 [tool.coverage.report] fail_under)
uv run pytest tests/ --cov
uv run ruff check src/ tests/

# 离线沙盒(0 token / 0 网络,用于排版与通知链路验证)
python src/main.py --mock --persona intermediate

# 真实抓取 + LLM 策展
python src/main.py --since daily  --persona intermediate
python src/main.py --since weekly --persona advanced
```

> **依赖文件两件套**:`pyproject.toml`(依赖声明 + ruff/mypy/pytest/coverage/bandit 配置)、
> `uv.lock`(锁定版本,**Dependabot 与 CI 的 pip-audit 都扫它**)。测试与生产都走
> `uv sync --frozen`,同一份 lockfile —— 曾经的 `requirements.txt` 已删除,
> 它让"测的"和"跑的"是两套依赖。升级安全版本后必须 `uv lock` 重新生成 lockfile,
> 否则 GitHub 上的漏洞告警不会关闭;`uv lock --check` 在 CI 的 supply-chain job 里守住 drift。
>
> ✅ `--mock` 现在是真离线沙盒:不抓 trending、不抓 README、不发 LLM 请求,
> 状态文件与报告都写到 **`reports_mock/`**(已 gitignore),不会污染 `reports/` 下
> 线上管线依赖的 repo_history / high_star_archive / repo_cycles。
>
> ✅ 测试也不再污染真实状态:`tests/conftest.py` 的 `isolated_dedup_config(tmp_path)`
> 负责隔离路径,session 级 `reports_guard` fixture 会在跑完后逐字节比对 `reports/` 三个
> 状态文件,**被改动就直接 fail**。新增用例请用 tmp_path 路径,不要图省事用 `AppConfig()` 默认值。

---

## 关键事实(agent 容易踩坑)

### 入口与运行时

- **唯一入口**:`src/main.py`。`sys.path.insert` 在 main.py:10 手动注入根目录,
  因此 `python src/main.py` 必须在仓库根运行,不要 `cd src && python main.py`。
- **CLI 选项**:`--since {daily,weekly,monthly}`、`--persona {beginner,intermediate,advanced}`、
  `--mock`、`--feishu/--slack/--discord <webhook>`(覆盖 config/env)。
- **真实运行需要 5 个 API key**(缺一即对应通道静默降级,**不会中断管线**):
  - `OPENROUTER_API_KEY` — 各链 L2/L3 的公共降级腿(`super-vl` / `ultra`)
  - `SENSENOVA_API_KEY` — 分类/翻译/写作链的末位限免腿(Token Plan 端点)
  - `SILICONFLOW_API_KEY` — `translator_a` 的首腿
  - `AGNES_API_KEY` — `writer` / `classifier` / `translator_b` 的首腿
  - `ZENMUX_API_KEY` — 四条链上的中段兜底(`ling-3.1-flash`)
  第六个是**可选**的 `TYPESAFE_API_KEY`(比稿 Jev 判断通道,见下),没有就退回字母裁判。
  其余可选:`FEISHU_WEBHOOK_URL` / `SLACK_WEBHOOK_URL` / `DISCORD_WEBHOOK_URL` / `GITHUB_TOKEN`。
- `python-dotenv` 通过 `load_dotenv()`(main.py:58)从 CWD 加载 `.env`。

### LLM 引擎:五角色多供应商

模型路由:`config.yaml` 的 `ai.roles` 给每个角色一条**有序降级链**
(`chain: [{model, provider, max_tokens?}, ...]`),代码级默认在 `src/config.py` 的
`AIConfig.roles`(两处要同步,有 drift 用例守着)。
provider → (env key, base_url env, 默认 base_url) 的映射在 `src/config.py:_PROVIDER_ENV`。

**链的语义:逐腿尝试,第一条给出非空正文的腿胜出**;整条链都失败才降到 stub。

| 角色 | 管线阶段 | L1 → L2 → L3 → L4 |
| :--- | :--- | :--- |
| `classifier` | Stage 2 批量分类评级 | agnes `agnes-3.0-flash` → or `nemotron-3.5-super-vl:free` → sn `glm-5.2` → sn `sensenova-6.8-flash-lite` |
| `writer` | Stage 3+4 逐仓库写作+反思 | agnes `agnes-3.0-flash` → or `nemotron-3-ultra-550b-a55b:free` → zm `inclusionai/ling-3.1-flash` → sn `sensenova-6.8-flash-lite` → 英文 stub |
| `translator_a` | Stage 5 翻译 A 通道 | sf `tencent/Hunyuan-MT-7B` → or `nemotron-3.5-super-vl:free` → zm `ling-3.1-flash` → sn `sensenova-6.8-flash-lite` → 采用 A 通道 |
| `translator_b` | Stage 5 翻译 B 通道 | agnes `agnes-2.5-flash` → or `nemotron-3-ultra-550b-a55b:free` → zm `ling-3.1-flash` → sn `sensenova-6.8-flash-lite` → 采用 A 通道 |
| `reviewer` | Stage 5.5 二选一比稿 | **Jev 前置**(TypeSafe,不在这条链里) → agnes `agnes-3.0-flash` → sn `glm-5.2` → zm `ling-3.1-flash` |

- **排序原则**:越靠前的腿越可靠,相邻腿尽量跨 provider。三条硬约束(都有回归用例守着):
  ① 两条翻译通道的 L1 必须**既不同模型也不同 provider**,否则 A/B 同源,比稿失去意义;
  ② `reviewer` **不得复用任一翻译通道 L1 的模型** —— 裁判不能评自己的译文,实测
  `Hunyuan-MT-7B` 在"A 漏 / B 好"的对照里仍选 A(A 偏见);
  ③ `classifier` 的 L1 **不放 sensenova**:它一轮只发一次(RPM 需求≈0),却决定全部仓库的
  评级与分桶,失败代价最高,而 sensenova 的限免额度是按低频小请求设计的 ——
  实测连打 3 轮 first-attempt 成功率 **0/3**(恒 `RateLimitExceeded.EndpointTPMExceeded`)。
- **翻译两通道必须跨 provider**:`translator_a`=siliconflow(Hunyuan-MT-7B),
  `translator_b`=agnes(`agnes-2.5-flash`)。两条都挂在同一账户时,A/B 竞稿毫无独立性 ——
  10/12 那次欠费 402 一把带走两个通道 + Review,日报连续四天只有英文/中文 stub。
  换模型可以,把两条搬回同一家不行。
  openrouter 免费档限额是 **20 请求/分钟 + 1000 请求/天**(累计购额 ≥$10;<$10 只有 50/天),
  一轮完整管线约 37 次请求,三班/天也才 ~110 次,余量足够 —— 但**别把天数的重试堆出来**。
  两条通道的 L3/L4 都指同一条 sensenova 腿是可接受的:那时 L1 仍分属两家,独立性已无从保证,
  优先"有译文"(缺一边 → 用另一边;两边全空 → 见下方降级路径表)。
- **writer 的 A/B 开关**:`ai.roles.writer` 就是 A 案(现役);`ai.writer_variants.b` 只放替代方案,
  `ai.writer_variant: a|b` 一处切换,或设环境变量 `WRITER_VARIANT=b` 临时覆盖。
  解析在 `AIConfig.apply_writer_variant()` —— 选中 b 时直接覆写 `roles["writer"]`,下游读取路径不变,
  所以 **b 案的 L2 就是 A 案的 L1**,两案互为兜底(b = `agnes-2.5-flash` → `agnes-3.0-flash` →
  ultra → ling → sensenova-lite)。非法变体会打印 `[Config Warning]` 并回退 a。
  **`apply_writer_variant` 必须 `model_copy(deep=True)`**:chain 是 `list[ModelLeg]`,浅拷贝会让
  `_writer_base` 与 `roles["writer"]` 共享同一批 ModelLeg 对象,切回 a 时拿到的是被 b 那条链
  改过的型号(有专门的回归用例守着)。
  **workflow 的下拉框默认是 `config`(= 不覆盖)**,schedule 触发时 `WRITER_VARIANT` 为空,
  所以定时跑走 `config.yaml` 的值;env 空值与 `config` 一律视为"不覆盖"(`load_config()`)。
  日志里的 `[Init] writer: … [变体 b]` 用来确认当前跑的是哪一案 —— 10/11 把 YAML 改成 b 后
  连续三天日志仍是 `[变体 a]`,原因就是你当时在这行加了 `|| 'a'` 兜底。
- **Review 的 `max_tokens=256`**(`pipeline.REVIEW_MAX_TOKENS`):输出只要一个字母,但推理型模型把
  `reasoning_content` 算进同一预算 —— 实测 10 个候选模型里有 **6 个**在 64 token 下返回**空正文**。
  而旧逻辑只看首字符:`**B**`(星号)与空正文都会被静默读成"选 A",**裁判的沉默等于替 A 投票**。
  现在预算给到 256(免费模型多给预算没有代价),判词先过 `pipeline._normalize_verdict`
  (剥掉首尾 Markdown 与标点,只认归一化后的首字符 A/B),其余一律打印
  `[Review No Verdict]` 并采用 A 通道 —— **不猜**。
- **比稿先问 Jev(TypeSafe System One)**,这是本项目**第一个付费依赖**($42/B input、output
  免费,一轮 Review 约 2.5 万 input ≈ $0.001)。`pipeline._jev_verdict` 在**一次** `system_one`
  请求里并行问两个判断:`Choice(better_draft)` 选 A/B(带 `confidence` 与 `probabilities`),
  `Noul(draft_incomplete)` 专抓"丢小节/整段漏译" —— 只看文笔的字母裁判抓不到这个。
  **退回字母裁判的条件**(任一命中即复判):通道未配置或调用失败、答案不是 A/B、
  `confidence < ai.jev.confidence_threshold`(默认 0.6)、`noul > incomplete_threshold`(默认 0.5)。
  它**不走 `_PROVIDER_ENV`**(不是 OpenAI 兼容协议),凭证是 `TYPESAFE_API_KEY`,
  client 在 `LLMClient._get_jev_client` 里懒建;`call_judgment` 一旦失败就把通道停用
  到本次运行结束(`has_judgment()` 转 False),绝不逐仓库继续敲门烧钱。

- 五家 provider(openrouter / sensenova / siliconflow / agnes / zenmux)全部走 OpenAI SDK
  兼容协议(`src/llm.py`),**Jev 判断通道除外**(独立 client)。`_PROVIDER_ENV` 另**预注册**了
  tokenrhythm / intern 两家(当前无 role 使用,为将来切换预留 —— 只改 config.yaml 就能用)。
- **节流**:超时与最小重试间隔是两张 **module-level 表**(`llm.PROVIDER_TIMEOUTS` /
  `llm.PROVIDER_MIN_INTERVAL`),数字都来自实测;未知 provider 落
  `DEFAULT_TIMEOUT = 30` / `DEFAULT_MIN_INTERVAL = 2.0`:

  | provider | 超时 | 最小间隔 | 依据 |
  | :--- | :--- | :--- | :--- |
  | sensenova | 60s | **6.0s** | `glm-5.2` 写作实测 66.0s;3s 内发第二发必 429 |
  | openrouter | 60s | 2.0s | `super-vl` 翻译实测 39.2s,已逼近旧的 45s |
  | siliconflow | 90s | 2.0s | Hunyuan 长输出 >30s:模型慢,不是通道死 |
  | agnes | 60s | 2.0s | 写作实测最大 40.6s |
  | zenmux | 60s | 2.0s | 写作实测 53.3s |
  | tokenrhythm / intern | 90s / 120s | 2.0s | 预留,未实测 |

  `call_llm` 默认 `retries=3, backoff_factor=2.0`,429 时指数退避,退避有下限
  `MIN_RETRY_DELAY = 2.0` —— 免费档 429 在毫秒级返回,零下限等于原地猛重。
  **两个 SDK 的内置重试都必须显式归零**:`OpenAI(..., max_retries=0)`、
  `TypeSafeClient(retry=RetryPolicy(max_retries=0))`。默认值都是 2,会把我们的 `retries=3`
  乘成 9 次 HTTP —— 10/16 那班 openrouter 逐仓库超时,单模型耗掉 ~135s(3×45s),
  整条 curate 撞上 `timeout-minutes: 45` 被杀,当天报告直接没出。
- **错误契约**:`call_llm` 拿不到内容时 **raise `LLMError`**,不再返回
  `{"content": "Error: no client..."}`。旧实现会把错误字符串当正文一路写进报告
  (历史上真发生过)。**空正文(`content.strip()` 为空)和"没有 choices"一样记一次失败**,
  绝不当成答案 —— 推理型模型烧光预算后回 200 + 空串,那既不是译文也不是判词。
  上层用 `llm.has_role(role)` 预判通道可用性(它现在是 `bool(llm.legs(role))`)→
  不可用就**跳过、不发请求**;逐仓库阶段(analyze/write/translate/review)`except Exception`
  落到 stub,保证单仓库失败不拖垮整份报告。
- **熔断是两级,而且粒度到腿**(`LLMClient._exhausted_legs: set[(role, "provider/model")]`):
  一条腿把重试耗尽 → 本次运行剩余仓库不再试它(`legs()` 会把它滤掉),**链上后面的腿照常走**;
  整条链都耗尽 → 该 role 进 `_unavailable_roles`,`has_role` 之后一律 False。
  先例:siliconflow 的 `tencent/Hunyuan-MT-7B` 每次 30s 超时,9 个仓库各烧 3 次重试 ≈ 14 分钟,
  整条 curate job 撞上 `timeout-minutes` 被取消,一份报告都没出 —— 现在 L1 死掉只意味着
  后面 9 个仓库直接从 L2 开始。
- **没配 key ≠ 重试耗尽**:两者的异常文案必须能区分(`no client for provider(s) [...]` vs
  `failed after N attempt(s) across M leg(s)`),否则会把"缺 secret"误诊成"模型挂了"。
  没配 key 的腿只是被 `legs()` 滤掉,**不会**把 provider 拉黑。
- **不可重试的状态码**(`llm.NON_RETRYABLE_STATUS`):401/402/403 是**账户级**闸门
  (欠费/密钥失效/权限),第一次就把整个 provider 拉黑 —— `has_provider`/`has_role` 之后一律
  False,同 provider 的其它 role 也不再发请求;404 是**模型级**,只把那一**条腿**判死
  (`_exhausted_legs`),provider 留给别的腿和别的 role,并照走链上后面的腿。
  先例:10/12 起 siliconflow 欠费 402,响应 0.2s 就返回(根本没进推理),
  却仍按 3 次重试 × 2 个 role 白烧 6 次请求。
- **改通道配置后必须验证**:CI 日志里 `[LLM Init] Providers with keys:` 包含该 provider,
  对应 stage 的 `+N 成功调用` > 0,且逐仓库日志里能看到 `role=… → leg i/N` 走的是哪条腿。
  free tier 模型随时可能下架(classifier 的 6.7→404 就是先例)。
  `tests/test_config.py::TestProviderRegistry::
  test_every_provider_used_by_a_leg_has_a_workflow_env_line` 守着"①GitHub Secrets
  ②workflow env ③`_PROVIDER_ENV`"三处同步里的后两处。
- **CI probe 实测结论(2026-10-19,run 39211785785,手动触发 `model-probe.yml`)**:9 组候选各跑
  真实 prompt 的四类探针(T1 判别 / T2 写作 / T3 翻译 / T4 中文长文),上面那张链路表就是按
  它的结果定的。**三条"看着红但无害"的记录,别再当 bug 去"修"**:
  - `or nemotron-3.5-super-vl:free` 的 **T2 只拿到 3/4 小节**(out=2048 截断)—— 它不当 writer,
    只当 classifier 的 L2 与 translator_a 的 L2;
  - `sn glm-5.2` 的 **T4 锚点 0/4、中文 0 字** —— 它不当翻译腿,只当 classifier 的 L3 与
    reviewer 的字母裁判;
  - `sn deepseek-flash` 在 CI 上 **T1/T2/T4 全 429、T3 空串** —— sensenova 的 TPM 天花板撑不住
    大请求,所以 classifier 的 L4 用的是 `sensenova-6.8-flash-lite`(T1 合规 3/3);
  - 另外 `sf tencent/Hunyuan-MT-7B` 的 T1 合规 **0/3**:它永远不碰 classifier,只做
    translator_a 的 L1(它免费,但只会在翻译这一件事上)。
  裁判准确率对照("A 漏 / B 好" vs "A 好 / B 漏"):`sn glm-5.2`、`sn sensenova-6.8-flash-lite`、
  `agnes-3.0-flash`、`agnes-2.5-flash`、`zm ling-3.1-flash` 均 2/2;`sf Hunyuan-MT-7B` 1/2
  (A 偏见,所以被排除出 reviewer);`or ultra-550b` / `or super-vl` 各 1/2。

### LLM 调用次数(现状,以 CI 日志为准)

一次完整真实运行(N = 入选策展的仓库数,通常 6~9)≈ **1 + 4N 次调用**:

| 阶段 | 调用 | 说明 |
| :--- | :---: | :--- |
| Stage 2 Analyze | 1 (batch) | N 个 repo 一次性分类 + 评级 |
| Stage 3+4 Write&Reflect | N (per-repo) | 每仓库 1 次;batch 化曾尝试后**回退**(batch JSON 解析失败率过高) |
| Stage 5 Translate | 2N (per-repo) | A/B 双模型各 1 次 |
| Stage 5.5 Review | ≤2N (per-repo) | 先 1 次 Jev(同一请求问 Choice + Noul);拿不准才走 reviewer 链的字母裁判(再 1 次)。Jev 没配 = 全部走字母裁判,仍是 N |

- **降级路径**:classifier batch 失败 → 静态 `infer_tags/rating` **保留全部候选**(不再截 top 6);
  Write 失败或产物缺 4 个 `###` 小节 → **英文** stub(再由翻译层出中文,并打 `analysis_is_stub=True`);
  Translate 通道不可用 → 该通道留空且**一次请求都不发**;Review 缺一边 → 用另一边;
  **两边都缺 → 若是真产物(完整性门过 + `analysis_is_stub` 为假)则保留 writer 英文原文**
  (加一行 `UNTRANSLATED_NOTICE`),否则仍落中文 stub —— 翻译层死了不该把 Write 阶段那上万
  output token 一起扔进 stub,但英文 stub 齐 4 小节也只是占位符,不能冒充"原文"骗读者。
- **代码入口**:`pipeline._stage_analyze`(batch)/ `_stage_summarize_and_reflect`(per-repo)/
  `_stage_translate` / `_stage_review`(后两者已从 `run()` 内联提取为可测方法)。
- **Write&Reflect 的 reflect 是真的**:writer 在同一调用内先输出 `SELF-CHECK:` 一行
  (判断先行,不增加轮次),再输出 4 段分析;`_split_reflection` 把它剥到 `reflection_trace`,
  CI 日志逐仓库打印。缺小节的产物会被 `_analysis_complete` 门挡下。

### 测试规范(血泪教训)

- **禁止硬编码绝对日期**。CI 曾因 `tests/test_dedup.py` 写死 `2026-06-01` 而连续 10 天失败
  (日期滑出 `first_seen_window_days=90` 窗口 → 断言反转 → test job 红 → curate 被
  `needs: [test]` 级联阻塞,日报停摆)。所有"窗口内/外"语义用
  `tests/test_dedup.py:_days_ago(n)` 这类相对今天的动态日期。
- **禁止用默认路径构造 `AppConfig` 跑会写状态文件的用例**。用
  `tests.conftest.isolated_dedup_config(tmp_path, ...)`,它会把 history/archive/**cycles** 三个路径一起隔离
  (早期只隔离了前两个,`repo_cycles.json` 被真实记忆覆盖过)。session 级 `reports_guard`
  fixture 会在跑完比对 `reports/` 字节变化并直接 fail。
- 本地 shell 常带各种 `*_API_KEY` 环境变量,会污染"无 key 应降级"类测试
  (`tests/test_llm.py` 等)。新增 provider 时**不要**写死要清理的 key 名单,用
  `tests/test_llm.py:_clear_all_provider_keys(monkeypatch)`(它从 `_PROVIDER_ENV` 派生)。
  复现 CI 用干净环境:`env -i PATH="$PATH" HOME="$HOME" uv run pytest tests/ -q`。
  同理,本机 shell 的 `SENSENOVA_BASE_URL` 之类指向别家端点的变量会被
  `resolve_base_url()` 的 host 白名单拦下(响一声 `[Config Warning]` 并回落到代码默认值),
  别把那条警告当成 bug。
- coverage 由 pyproject 强制 85% 门槛;test job 还跑 `ruff check`、`mypy src/`、`bandit -r src/`,
  三者都必须零告警(**不允许再加 `|| true`** —— 曾经 mypy 因 src/ 缺 `__init__.py` 直接报错退出,
  一个类型都没检查,却被 `|| true` 掩盖成"绿灯")。

### 抓取行为

- 即便 `--since weekly`,Stage 1 仍会**同时**抓 daily/weekly/monthly 三榜各 top 10
  + monitored orgs/users,然后按 `full_name` 去重(`pipeline._stage_crawl`)。
  `--since` 只影响报告的时间窗标识与标题。
- 抓取后经 **bucket allocation**(配额分桶,`config.yaml` 的 `bucket_allocation` 段)压缩到
  `total_slots` 个再进 LLM。三桶配额之和必须等于 `total_slots`,否则 pydantic 校验直接报错。
  分桶引擎关闭时才退回 `_prefilter_top_n`(`stage2_pre_filter.max_repos`)。
- **Scrape 阶段的 README 会真的喂给 writer**(`_readme_excerpt`,预算
  `README_CONTEXT_CHARS = 1200`,先滤掉徽章/图片/HTML 注释行)。改这个预算要按
  "每个 token 为决策服务" 算账:它换来的是分析有据可依,不是凭描述标题编造。
- `monitored_orgs` / `monitored_users` 来自 `config/config.yaml` —— 修改时注意
  GitHub API 速率(匿名 60/h,配 `GITHUB_TOKEN` 后 5000/h)。

### 画像(personas)

- **画像现在真的影响正文**:`prompt_focus` 同时进入 classifier(评级/标签)与 writer
  (语气、词汇、深度)。writer 的 system prompt 明确"小节标题固定英文 4 段,画像只管风格",
  避免中文小节名打坏 `_ensure_markdown_spacing` 与翻译层。
- `intermediate` 是默认画像,也是配置最严格的 —— `config/personas.yaml` 有**通用普适性硬性约束**:
  LLM 输出**严禁**提到任何具体个人姓名(包括"海宁"/"于海宁"/"Haining"/"Golden0Voyager")、
  个人背景(MFA / 当代艺术 / 美术馆等)、第一人称。所有措辞必须面向"广大中阶 LLM 实践者"。
  修改 intermediate 的 prompt 时这条不能动。模板层同样遵守:`templates/report.md.j2`
  不得出现个人署名(曾有 "Designed with ❤️ by Haining Yu")。
- `beginner` 强调"能帮我干什么"、WebUI/即插即用;`advanced` 走 MoE/MLA/RLHF/MCTS 学术语系。

### 高🌟项目去重(重要状态文件)

- `reports/repo_history.json` —— 每个 full_name 出现过的日期列表(同日去重,TTL 365 天)。
- `reports/high_star_archive.json` —— 冷却期中的项目。
- `reports/repo_cycles.json` —— 每个项目的第 N 次归档计数(**阶梯冷却**用)。
- 触发条件(`config.yaml:dedup` 段):`star ≥ 10000` **且** 累计出现 `≥ 3` 次。
- **阶梯冷却**:`steps_cooldown: true` 时,第 k 次归档冷却 `30 × max(1, 1+0.5(k-1))` 天,
  封顶 `max_cooldown_days: 90`。
- `first_seen_window_days: 90` —— 90 天内没出现过才算"首次出现"(Early Bird 分桶依据)。
- 冷却期满后由 `purge_expired_cooldowns()`(dedup.py)清理 —— **只删 archive 里的"仍在冷却中"标记**,
  `repo_history.json` 与 `repo_cycles.json` 的记忆必须保留。早期版本 purge 时把三个文件一起抹掉,
  cycle 永远回到 1 → 冷却恒为 30 天,阶梯冷却从未真正生效(有回归用例守住这条)。
- 因此被冷却过的高星项目重新入库后,只需**再出现 1 次**就会被重新归档,且关得更久(30→45→60→75→90 封顶)。
- 这三个文件**不能 gitignore**:它们是系统的"记忆",丢失会破坏去重不变量。
- CI 的 curate job 跑完会把它们推到 `auto-docs`,并在下次跑之前**从 `auto-docs` 合并读回**——
  main 上那份只是初值,不是运行时的最新记忆。合并语义(见 workflow 的 "Restore curation memory
  from auto-docs" 步骤):`repo_history` 取日期并集、`repo_cycles` 取 max(直接覆盖会把归档轮次
  抹掉,阶梯冷却又退回第 1 轮)、`high_star_archive` 以 `auto-docs` 为唯一权威(**包括它为空的情况**,
  否则 main 上陈旧的"仍在冷却中"标记会被复活)。

### 多端通知的坑

- **Discord**:`notifier.py` 在 markdown 长度 > 1950 字符时硬截断到 1900
  并追加截断提示,**没有分页**。长报告会丢尾部。
- **Feishu** 卡片由 `formatter._build_feishu_collapsible_payload` **在 Python 里构造**
  (collapsible_panel + JSON 2.0 schema),不走 Jinja;**Slack** 走 `templates/slack_blocks.json.j2`
  (Block Kit,文本一律过 `tojson` 转义)。两者结构不同 —— 改模板时不要跨通道互借字段。
  曾有 `templates/feishu_card.json.j2` 挂在仓库里但从未被引用,已删除。
- `send_feishu/send_slack/send_discord` 在未配置 webhook 时直接返回 False(notify_all 会跳过),
  目录只在 `save_locally` 里按需创建 —— 构造函数里 mkdir 曾让 mock config 在仓库根拉出 `MagicMock/` 目录树。
- 推送失败不会中断主流程,打印 ❌ 后继续退出码 0。

### GitHub Actions(不是普通的 CI)

- **只有一个 workflow 文件** `.github/workflows/daily_trending.yml`(原 `ci.yml` 与它重复跑 test,已合并)。
  三个 job:`test`(ruff + mypy + bandit + pytest/coverage,** gating**)、
  `supply-chain`(`uv lock --check` + `pip-audit`,**不 gating**,红叉可见但不拦日报)、
  `curate-and-notify`(`needs: [test]`)。**test 红 = 日报停摆**,这是历史上最长的一次事故(10 天)。
- 定时 `0 2 * * *` UTC = 北京时间 10:00;也支持 `workflow_dispatch` 手触发,
  可选 `since` / `persona` 入参;push 到 main 也会跑完整管线(可用于补发报告)。
- `concurrency` 按 ref 串行,避免两次 curate 争抢 `auto-docs` 上的去重记忆。
- 测试与生产都用 `uv sync --frozen` + 同一份 `uv.lock`(`requirements.txt` 已删除)。
- **test job 里不允许出现 `|| true`**。历史上 mypy/bandit/`uv audit`(uv 根本没这个子命令)
  全被 `|| true` 吞掉,mypy 因 src/ 缺 `__init__.py` 一个文件都没检查,却显示绿灯。
- LLM keys 通过 "Run Curator Pipeline" 步骤的 `env:` 显式注入。
  **给 config.yaml 加新 provider 时,必须同步:① GitHub Secrets 加 key;② workflow env
  加一行 `${{ secrets.XXX }}` 映射;③ `src/config.py:_PROVIDER_ENV` 有条目**
  (`LLMClient._init_clients` 现在从 `_PROVIDER_ENV` 派生 provider 列表,不再硬编码元组)。
  只做其一等于没配。
- 报告产物 push 到 **`auto-docs` orphan 分支**(`reports/latest_daily.md` 与三个状态文件在那条分支上),
  main 只保留历史报告;commit 信息带 `[skip ci]` 防递归;跑完还会 `upload-artifact` 兜一份,
  push 失败不至于整份报告丢失。
- push 阶段 3 次重试 + `git rebase -X theirs origin/auto-docs`(本次新生成的文件必须胜出)。

---

## 项目结构

```
src/
  __init__.py     让 mypy 把 src 当一个包(缺它时 mypy 会报 "found twice" 并零检查)
  main.py         CLI 入口(参数解析 + 装配组件 + --mock 状态隔离)
  config.py       Pydantic 配置模型(ModelLeg/RoleConfig.chain)+ _PROVIDER_ENV 映射
                  + base_url host 白名单 + load_config()(YAML + env 覆盖)
  crawler.py      BeautifulSoup 抓 GitHub Trending HTML + REST API 拉 org/user repos
  dedup.py        RepoHistoryTracker:阶梯冷却 + 原子写 history/archive/cycles
  llm.py          LLMClient:链式逐腿降级 + per-(role,leg) 熔断 + LLMError 契约
                  + has_role 预判 + per-provider 超时/间隔表 + get_stats()
  pipeline.py     多阶段编排(_stage_analyze/_stage_summarize_and_reflect/_stage_translate/_stage_review)
  formatter.py    报告渲染:Markdown/Slack 走 Jinja,飞书卡片在 Python 里构造
  notifier.py     4 通道分发:local(必) + feishu/slack/discord(可选)
config/
  config.yaml     系统配置(监控列表、AI roles、去重阈值、分桶、通知 webhook)
  personas.yaml   3 画像的 prompt_focus(classifier 与 writer 都吃它)
templates/
  report.md.j2    Markdown 主报告(分桶看板 + 深度解析)
  slack_blocks.json.j2  Slack Block Kit(文本一律 |tojson,保证 json.loads 过)
reports/          线上产物 + **状态文件**(不能被 gitignore)
  daily_YYYY-MM-DD_HHMM.md   每日产物(Actions 会 push 到 auto-docs 分支)
  latest_daily.md            最近一次 daily 报告的副本
  repo_history.json          出现日期历史(**状态文件**)
  high_star_archive.json     冷却中项目(**状态文件**)
  repo_cycles.json           归档轮次计数(**状态文件**)
reports_mock/     --mock 的沙盒(gitignore):状态与报告都写这里,不碰 reports/
tests/            pytest 套件(~340 用例)
  conftest.py     isolated_dedup_config(tmp_path) + session 级 reports_guard
pyproject.toml    依赖声明 + ruff/mypy/pytest/coverage/bandit 配置(Dependabot 生态入口)
uv.lock           uv 锁定文件(**Dependabot 与 pip-audit 扫描对象**)
```

---

## 改代码时的自查清单

- 改了 persona prompt → 跑 `uv run python src/main.py --mock --persona <key>`,产物与状态都在
  `reports_mock/`,**不需要**再 `git checkout -- reports/`。
- 改了 `config/config.yaml` 阈值 → 注意 `repo_history.json` 不会自动迁移,
  改 `archive_threshold` 不会让已记录的项目"补触发"晋升;新阈值只对**之后**的抓取生效。
- 改了 LLM roles / 换 provider → 四处同步:`config.yaml` 的 `chain`、`src/config.py` 的
  默认 chain(有 drift 用例 `test_code_role_defaults_mirror_config_yaml` 守着)、
  workflow env + GitHub Secrets。推送后**必须**看一次 CI 日志确认
  `[LLM Init] Providers with keys:` 含该 provider、对应 stage 成功调用数 > 0,
  且逐仓库日志里 `role=… → leg i/N` 落在预期的腿上(free tier 模型随时可能下架,
  classifier 的 6.7→404 就是先例)。**腿的顺序 = 可靠性降序**,挪腿前先看 spec 的实测表。
- 改了 writer 的 4 个 `###` 小节名 → 同步 `pipeline.WRITER_SECTIONS`(完整性门),
  否则所有产物都会被判定不合格、整份报告落 stub。
- 改了 `formatter.py` 输出的 markdown 结构 → 检查翻译 prompt 的 `###` 锚点与
  `_ensure_markdown_spacing` 是否仍对齐,否则翻译排版会断;同时跑 `tests/test_formatter.py::TestTemplateIntegrity`
  (它专门验证 Slack JSON 是否真能 parse —— 解析失败会静默退化成只看 5 条的兜底载荷)。
- 改了通知渠道 → webhook URL 不要硬编码,env / config 注入即可;CI secret 已就绪。
- 写/改测试 → 日期一律相对 `_today()` 动态生成;dedup 路径用 `isolated_dedup_config(tmp_path)`;
  别依赖本机 shell 里的 API key。
- 不要把 LLM 调用整体换成 vLLM/本地推理 —— 整套 rate-limit / 退避 / mock fallback 是按
  三家托管服务的免费配额设计的。
