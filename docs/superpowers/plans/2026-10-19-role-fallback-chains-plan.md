# Role Fallback Chains Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `RoleConfig` from "primary + one fallback" into an ordered degradation chain of 3~4 free model legs, and fix the four routing/review defects the probe round found.

**Architecture:** `RoleConfig.chain: list[ModelLeg]` becomes the single source of truth; `model`/`provider` survive as read-only views of leg 0 so logging and the writer-variant switch keep working. `LLMClient.call_llm` walks the legs in order, tripping the breaker per `(role, leg)` instead of per role. Per-provider timeouts and minimum intervals move out of inline dicts into module constants pinned by tests. `_stage_review` stops treating an unusable judge answer as a vote for A.

**Tech Stack:** Python 3, Pydantic v2, OpenAI SDK (OpenAI-compatible endpoints), pytest, ruff/mypy/bandit, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-19-role-fallback-chains-design.md`

## Global Constraints

- 全链只用免费模型。被排除的付费模型不得出现在任何链里：`stepfun-ai/Step-3.5-Flash`（¥0.70/¥2.10）、`zai-org/GLM-5.3-Flash`（¥0.80/¥2.80）、`inclusionAI/Ling-flash-2.0`（¥1.00/¥4.00）。
- 链的顺序按**可靠性降序**；两条翻译通道的 L1 必须不同模型 + 不同 provider。
- 所有新增/修改代码必须有类型提示（PEP 484），`mypy src/` 零告警。
- CI 的三道门都要绿：`ruff check src/ tests/`、`mypy src/`、`bandit -r src/`，且 `pytest --cov` 覆盖率 ≥ 85。
- 测试禁止硬编码绝对日期（用相对 `_today()`）；禁止用默认路径构造 `AppConfig` 去写状态文件（用 `tests.conftest.isolated_dedup_config(tmp_path)`）；禁止依赖本机 shell 里的 `*_API_KEY`。
- 复现 CI 的干净环境：`env -i PATH="$PATH" HOME="$HOME" uv run pytest tests/ -q`。
- 一文件一提交，commit message 中英双语、英文块在前。不要 `git add .`。
- `RoleConfig` 含 list 字段后，`model_copy()` 是**浅拷贝** —— 任何复制 `RoleConfig` 的地方都必须 `model_copy(deep=True)`。

## Review Focus

Spec 没有逐条写、但最可能被踩到的五个输入：

1. **某条腿的 provider 没配 key**（不是失败，是没配）—— 必须静默跳过该腿，既不能拉黑 provider，也不能把它报成"重试耗尽"。
2. **`leg.max_tokens` 与调用点 `max_tokens` 同时存在** —— 必须以 leg 为准，否则 reviewer 的 256 与写作的 8192 会互相顶掉。优先级：`leg.max_tokens or 调用点 or config.ai.max_tokens`。
3. **`config.yaml` 里写了 `chain: []`** —— 必须构造即报错（pydantic），而不是运行时 `IndexError`。
4. **同一个 role 的相邻腿落在同一个 provider**（`classifier` 的 L3/L4 都是 sensenova）—— 允许，不能崩、不能无限循环。
5. **`_exhausted_legs` 的 key 含 `/` 与 `:` 的模型名**（`nvidia/nemotron-3.5-super-vl:free`）—— key 必须唯一稳定，且日志要能看出本轮用了第几条腿。

---

### Task 1: 链式 schema + 默认表迁移 + writer 变体深拷贝

**Files:**
- Modify: `src/config.py:17-93`
- Modify: `config/config.yaml:58-124`
- Modify: `tests/test_config.py:40-70, 135-145, 209-280`

**Interfaces:**
- Produces: `ModelLeg(model: str, provider: str, max_tokens: int | None = None)`；`RoleConfig.chain: list[ModelLeg]`（`min_length=1`）；只读属性 `RoleConfig.model` / `RoleConfig.provider`（= `chain[0]` 的字段）；`AIConfig.apply_writer_variant()` 内部改用 `model_copy(deep=True)`。
- Consumes: 无（本任务是最底层）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_config.py` 新增：

```python
def test_role_config_chain_is_the_source_of_truth():
    rc = RoleConfig(chain=[ModelLeg(model="m1", provider="p1"),
                           ModelLeg(model="m2", provider="p2", max_tokens=256)])
    assert rc.model == "m1" and rc.provider == "p1"
    assert [leg.model for leg in rc.chain] == ["m1", "m2"]
    assert rc.chain[1].max_tokens == 256

def test_role_config_rejects_empty_chain():
    with pytest.raises(ValidationError):
        RoleConfig(chain=[])

def test_writer_variant_switch_does_not_alias_chain_lists():
    """roles.writer 与 _writer_base 必须是深拷贝；浅拷贝会让切回 a 时拿到被改过的链。"""
    cfg = AIConfig()
    cfg.writer_variant = "b"
    cfg.apply_writer_variant()
    cfg.roles["writer"].chain[0].model = "mutated"
    cfg.writer_variant = "a"
    cfg.apply_writer_variant()
    assert cfg.roles["writer"].chain[0].model != "mutated"
```

并把 `tests/test_config.py` 里断言 `fallback_model` / `fallback_provider` 的用例（`:47, :49, :55, :56, :65, :66, :226, :239`）改成断言 `chain`，例如：

```python
    assert [leg.provider for leg in cfg.ai.roles["translator_a"].chain][-1] == "sensenova"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL —— `ImportError: cannot import name 'ModelLeg'`

- [ ] **Step 3: 实现 `ModelLeg` 与 `RoleConfig.chain`（`src/config.py`）**

```python
class ModelLeg(BaseModel):
    """降级链里的一条腿。"""

    model: str
    provider: str
    # 推理型模型会把输出预算烧在 reasoning_content 上，这条例外只对该腿生效。
    max_tokens: int | None = None


class RoleConfig(BaseModel):
    chain: list[ModelLeg] = Field(min_length=1)

    @property
    def model(self) -> str:
        return self.chain[0].model

    @property
    def provider(self) -> str:
        return self.chain[0].provider
```

`AIConfig.roles` / `writer_variants` 的默认值改成 `RoleConfig(chain=[...])`，`apply_writer_variant` 里两处 `model_copy()` 改成 `model_copy(deep=True)`，并把 `self.roles.get("writer", RoleConfig())` 改为显式处理 `None`（`RoleConfig()` 已不再是合法默认值）。

**过渡垫片（Ruling，见 ledger）**：本任务保留 `fallback_model` / `fallback_provider` 两个**只读属性**
（分别返回 `chain[1].model` / `chain[1].provider`，链不足两条时返回 `None`）。
原因：`src/llm.py` 有 6 处读这两个字段（`has_role` / `_resolve_providers` / `call_llm`），
而 llm.py 到 Task 4 才重写 —— 没有垫片的话，Task 2 的 `mypy src/` 会在一个"只改了 schema"
的仓库上报错。**这两个属性必须在 Task 4 删掉**，不留陈旧抽象。

`tests/test_config.py:135-145` 里的 `RoleConfig(model=..., provider=...)` 构造要一并改成 `chain=` 形式。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: 把两条链写进 `config/config.yaml`**

按 spec「最终降级链」表逐 role 改写 `ai.roles`（`classifier`/`writer`/`translator_a`/`translator_b`/`reviewer`）与 `writer_variants.b`，形状：

```yaml
    classifier:
      chain:
        - { model: agnes-3.0-flash, provider: agnes }
        - { model: nvidia/nemotron-3.5-super-vl:free, provider: openrouter }
        - { model: glm-5.2, provider: sensenova }
        - { model: sensenova-6.8-flash-lite, provider: sensenova }
```

同时把该段失效的注释（"super-vl 是免费 Nvidia 池里唯一支持 structured_outputs 的"、"RoleConfig 只有一级 fallback"）替换为链的说明。

- [ ] **Step 6: 跑 drift 用例确认代码默认值与 YAML 同步**

Run: `uv run pytest tests/test_config.py::TestAIConfigRoles -q`
Expected: PASS

- [ ] **Step 7: 提交（一文件一提交）**

```bash
git add src/config.py && git commit -m "feat(config): make RoleConfig an ordered fallback chain" -m "feat(config): RoleConfig 改为有序降级链"
git add config/config.yaml && git commit -m "chore(config): write the new chains into config.yaml" -m "chore(config): 把新降级链写进 config.yaml"
git add tests/test_config.py && git commit -m "test(config): assert the chain instead of the single fallback" -m "test(config): 断言链而不是单级 fallback"
```

---

### Task 2: 新 provider + base_url host 守卫

**Files:**
- Modify: `src/config.py:96-101, 170-200`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `_PROVIDER_ENV` 含 `agnes` / `zenmux` / `tokenrhythm` / `intern`；`resolve_base_url(provider: str) -> str` 在环境变量给的 host 不在白名单内时打印 `[Config Warning]` 并返回代码默认值。
- Consumes: Task 1 无需改动即可共存。

- [ ] **Step 1: 写失败测试**

```python
def test_resolve_base_url_ignores_a_non_allowlisted_host(monkeypatch, capsys):
    """本机 SENSENOVA_BASE_URL 指向没有限免模型的通用端点，必须被挡下。"""
    monkeypatch.setenv("SENSENOVA_BASE_URL", "https://api.sensenova.cn/compatible-mode/v2")
    assert resolve_base_url("sensenova") == "https://token.sensenova.cn/v1"
    assert "[Config Warning]" in capsys.readouterr().out

def test_resolve_base_url_accepts_an_allowlisted_host(monkeypatch):
    monkeypatch.setenv("SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1")
    assert resolve_base_url("sensenova") == "https://token.sensenova.cn/v1"

def test_provider_env_covers_the_new_legs():
    assert {"agnes", "zenmux", "tokenrhythm", "intern"} <= set(_PROVIDER_ENV)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_config.py -k "base_url or provider_env" -q`
Expected: FAIL —— `resolve_base_url` 会返回被污染的那个 URL

- [ ] **Step 3: 实现**

`_PROVIDER_ENV` 增加四条（env key / base_url env / default base_url）：
`agnes` → `(AGNES_API_KEY, AGNES_BASE_URL, https://apihub.agnes-ai.com/v1)`；
`zenmux` → `(ZENMUX_API_KEY, ZENMUX_BASE_URL, https://zenmux.ai/api/v1)`；
`tokenrhythm` → `(TOKENRHYTHM_API_KEY, TOKENRHYTHM_BASE_URL, https://tokenrhythm.studio/v1)`；
`intern` → `(INTERN_AI_API_KEY, INTERN_AI_BASE_URL, https://discovery-api.intern-ai.org.cn/v1)`。

再给 `resolve_base_url` 加 per-provider 允许的 host 集合（sensenova 只允许 `token.sensenova.cn`），
host 不匹配时 `print("[Config Warning] ...")` 并返回默认值。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_config.py -q` 与 `uv run mypy src/`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add src/config.py && git commit -m "fix(config): pin each provider's base_url to an allowlisted host" -m "fix(config): 用 host 白名单钉死各 provider 的 base_url"
git add tests/test_config.py && git commit -m "test(config): cover the base_url guard and the new providers" -m "test(config): 覆盖 base_url 守卫与新 provider"
```

---

### Task 3: per-provider 超时与最小间隔外提为常量

**Files:**
- Modify: `src/llm.py:13-15, 139-143`
- Modify: `tests/test_llm.py`

**Interfaces:**
- Produces: 模块级 `PROVIDER_TIMEOUTS: dict[str, int]`、`PROVIDER_MIN_INTERVAL: dict[str, float]`、`DEFAULT_TIMEOUT = 30`、`DEFAULT_MIN_INTERVAL`；`_try_model` 改读这两张表。
- Consumes: 无。

- [ ] **Step 1: 写失败测试**

```python
def test_provider_timeouts_match_the_measured_table():
    assert PROVIDER_TIMEOUTS["sensenova"] == 60      # glm-5.2 写作实测 66.0s
    assert PROVIDER_TIMEOUTS["openrouter"] == 60     # super-vl 翻译实测 39.2s
    assert PROVIDER_TIMEOUTS["siliconflow"] == 90
    assert PROVIDER_TIMEOUTS["agnes"] == 60
    assert PROVIDER_TIMEOUTS["zenmux"] == 60
    assert PROVIDER_TIMEOUTS["intern"] == 120

def test_sensenova_min_interval_is_six_seconds():
    """3s 内发第二发实测必 429。"""
    assert PROVIDER_MIN_INTERVAL["sensenova"] == 6.0

def test_unknown_provider_falls_back_to_defaults():
    assert PROVIDER_TIMEOUTS.get("nope", DEFAULT_TIMEOUT) == 30
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_llm.py -k "provider_timeout or min_interval" -q`
Expected: FAIL —— `ImportError: cannot import name 'PROVIDER_TIMEOUTS'`

- [ ] **Step 3: 实现**

按 spec 的 per-provider 参数表建两张常量表，`_try_model` 里现有的两个内联 dict 与 `MIN_RETRY_DELAY` 兜底逻辑改为读表（保留 `max(interval, MIN_RETRY_DELAY)`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_llm.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/llm.py && git commit -m "refactor(llm): lift per-provider timeouts and gaps into constants" -m "refactor(llm): per-provider 超时与最小间隔提为常量"
git add tests/test_llm.py && git commit -m "test(llm): pin the per-provider numbers to the measured table" -m "test(llm): 用实测数字钉住 per-provider 参数"
```

---

### Task 4: `LLMClient` 逐腿降级 + per-(role, leg) 熔断 + 空正文换腿

**Files:**
- Modify: `src/llm.py:57, 94-121, 158-175, 196-249`
- Modify: `tests/test_llm.py`

**Interfaces:**
- Produces: `LLMClient.legs(role: str) -> list[ModelLeg]`（已剔除无 client 的 provider 与已熔断的腿）；`has_role(role) -> bool`（= `bool(self.legs(role))`）；`_exhausted_legs: set[tuple[str, str]]`；`call_llm()` 的返回值多一个 `"leg"` 键（`"1/4"` 形式的字符串）。
- Consumes: Task 1 的 `RoleConfig.chain` / `ModelLeg`；Task 3 的两张常量表。

- [ ] **Step 1: 写失败测试**

在 `tests/test_llm.py` 新增。辅助函数（本步骤一并写）：

```python
def _client_with_chain(legs, replies, keys=None):
    """legs=[("p1","m1")|("p1","m1",256)]; replies={provider: "text" | Exception};
    keys=可用的 provider 子集（默认全可用）。monkeypatch 掉 _clients，不联网。"""
```

测试：

```python
def test_call_llm_walks_the_chain_in_order(fake_clients):
    """leg1 返回空正文，必须换到 leg2，而不是把空串当成功。"""
    llm = _client_with_chain([("p1", "m1"), ("p2", "m2")], replies={"p1": "", "p2": "ok"})
    res = llm.call_llm([{"role": "user", "content": "x"}], role="writer")
    assert res["content"] == "ok"
    assert res["leg"] == "2/2"

def test_exhausted_leg_is_skipped_on_the_next_call(fake_clients):
    """per-(role, leg) 熔断：第二个仓库不再为已死的 leg1 付失败税。"""
    llm = _client_with_chain([("p1", "m1"), ("p2", "m2")],
                             replies={"p1": RuntimeError("boom"), "p2": "ok"})
    llm.call_llm([{"role": "user", "content": "x"}], role="writer", retries=1)
    hit_before = fake_clients.calls["p1"]
    llm.call_llm([{"role": "user", "content": "y"}], role="writer", retries=1)
    assert fake_clients.calls["p1"] == hit_before

def test_leg_max_tokens_overrides_the_call_site_value(fake_clients):
    """Review Focus #2：leg 上的 max_tokens 优先于调用点给的 8192。"""
    llm = _client_with_chain([("p1", "m1", 256)], replies={"p1": "A"})
    llm.call_llm([{"role": "user", "content": "x"}], role="reviewer", max_tokens=8192)
    assert fake_clients.last_kwargs["max_tokens"] == 256

def test_adjacent_legs_on_one_provider_neither_loop_nor_crash(fake_clients):
    """Review Focus #4：classifier 的 L3/L4 都是 sensenova，两条腿各打一次就收手。"""
    llm = _client_with_chain([("p1", "m1"), ("p1", "m2")], replies={"p1": ""})
    with pytest.raises(LLMError):
        llm.call_llm([{"role": "user", "content": "x"}], role="classifier", retries=1)
    assert fake_clients.calls["p1"] == 2

def test_missing_provider_key_is_unconfigured_not_exhausted(fake_clients):
    """Review Focus #1：没配 key 不是失败，也不能把 provider 拉黑。"""
    llm = _client_with_chain([("p_missing", "m1"), ("p2", "m2")],
                             replies={"p2": "ok"}, keys={"p2"})
    res = llm.call_llm([{"role": "user", "content": "x"}], role="writer")
    assert res["content"] == "ok"
    assert "p_missing" not in llm._banned_providers
```

（`fake_clients` 记录 `calls` 与 `last_kwargs`，参照 `tests/test_llm.py` 既有的 stub 写法。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_llm.py -k "chain or exhausted_leg or leg_max_tokens" -q`
Expected: FAIL —— `AttributeError: 'RoleConfig' object has no attribute 'fallback_model'`

- [ ] **Step 3: 实现**

- `legs(role)`：按 `chain` 顺序过滤掉 `not self.has_provider(leg.provider)` 与 `(role, f"{leg.provider}/{leg.model}") in self._exhausted_legs` 的腿。
- `has_role(role)` 直接 `return bool(self.legs(role))`（在 `_unavailable_roles` 短路之后）。
- `call_llm` 改成 for 循环遍历 `legs(role)`，逐腿 `_try_model(...)`；某腿返回 `None` 就把该 `(role, key)` 加进 `_exhausted_legs` 并继续下一腿；全部腿失败才抛 `LLMError`。
- `max_tokens` 优先级：`leg.max_tokens or max_tokens or self.config.ai.max_tokens`。
- `_try_model` 里 `content.strip()` 为空时按一次失败处理（`self.failed_attempt_count += 1`），继续重试或返回 `None`。
- **删掉 Task 1 留下的过渡垫片**：`RoleConfig.fallback_model` / `fallback_provider` 两个属性此时已无人读取（llm.py 是本仓库唯一读者），必须一并删除。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_llm.py tests/test_llm_roles.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/llm.py && git commit -m "feat(llm): walk an ordered leg chain with a per-leg breaker" -m "feat(llm): 按序走降级链，熔断粒度改为 per-(role, leg)"
git add tests/test_llm.py && git commit -m "test(llm): cover chain order, per-leg breaker and empty content" -m "test(llm): 覆盖链序、per-leg 熔断与空正文换腿"
```

---

### Task 5: `_stage_review` 判定修复

**Files:**
- Modify: `src/pipeline.py:26, 749-751`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `REVIEW_MAX_TOKENS = 256`；模块级 `_normalize_verdict(raw: str) -> str | None`（剥 Markdown 与空白，只认首字符 A/B，其余返回 `None`）。
- Consumes: Task 4 的 `call_llm` 链（空正文已在 llm 层换腿，这里只处理"答了但不是 A/B"）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_pipeline.py` 新增。辅助 fixture（本步骤一并写）：

```python
def stub_judge(reply):   # pytest fixture
    """关掉 Jev，把 reviewer 的 call_llm 换成固定回复；返回 (pipeline, repo)，
    repo 已带 translation_a="### 甲" 与 translation_b="### 乙"。"""
```

测试：

```python
def test_normalize_verdict_strips_markdown():
    assert _normalize_verdict("**B**") == "B"
    assert _normalize_verdict("  b.\n") == "B"
    assert _normalize_verdict("The user wants B") is None
    assert _normalize_verdict("") is None

def test_review_takes_b_when_the_verdict_is_markdown_wrapped(stub_judge):
    """Hunyuan 实测返回 **A** 这种形式，不能因为星号就当成"没选 B"。"""
    pipeline, repo = stub_judge("**B**")
    pipeline._stage_review([repo])
    assert repo["chinese_summary"] == "### 乙"

def test_review_does_not_invent_a_verdict_from_prose(stub_judge, capsys):
    """散文不是判词：既不换到 B，也不该被记成裁判成功。"""
    pipeline, repo = stub_judge("The user wants translation B")
    pipeline._stage_review([repo])
    assert repo["chinese_summary"] == "### 甲"
    assert "[Review No Verdict]" in capsys.readouterr().out
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_pipeline.py -k "normalize_verdict or markdown_b" -q`
Expected: FAIL —— `ImportError: cannot import name '_normalize_verdict'`

- [ ] **Step 3: 实现**

`REVIEW_MAX_TOKENS` 改 256；加 `_normalize_verdict`；`_stage_review` 里改为：

```python
            choice = _normalize_verdict(res.get("content") or "")
            if choice is None:
                print(f"  [Review No Verdict] {r['full_name']} 裁判未给出 A/B → 采用 A 通道")
                r["chinese_summary"] = ta
                continue
            r["chinese_summary"] = tb if choice == "B" else ta
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_pipeline.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/pipeline.py && git commit -m "fix(pipeline): never read a missing verdict as a vote for A" -m "fix(pipeline): 裁判没给出 A/B 时不再默认成选 A"
git add tests/test_pipeline.py && git commit -m "test(pipeline): pin markdown-wrapped and missing verdicts" -m "test(pipeline): 钉住 Markdown 包裹与缺失判词两种情况"
```

---

### Task 6: workflow env + 覆盖率守卫 + AGENTS.md

**Files:**
- Modify: `.github/workflows/daily_trending.yml:218-222`
- Modify: `AGENTS.md`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: 新用例 `test_every_provider_has_a_workflow_env_line`（读 `daily_trending.yml`，断言 `_PROVIDER_ENV` 的每个 provider 都有对应的 `*_API_KEY` env 行）。
- Consumes: Task 2 的 `_PROVIDER_ENV`。

- [ ] **Step 1: 写失败测试**

```python
def test_every_provider_used_by_a_leg_has_a_workflow_env_line():
    """只做其一等于没配：任何被某条腿用到的 provider，都必须在 workflow env 里有映射。

    只看"被腿用到"的 provider，不看 _PROVIDER_ENV 全集 —— 后者是注册表，合法地含有
    当前无人使用的 provider（openai / tokenrhythm / intern），它们不该要求 CI secret。
    """
    cfg = AIConfig()
    used = {leg.provider for rc in cfg.roles.values() for leg in rc.chain}
    used |= {leg.provider for rc in cfg.writer_variants.values() for leg in rc.chain}
    text = Path(".github/workflows/daily_trending.yml").read_text(encoding="utf-8")
    for provider in sorted(used):
        env_key = _PROVIDER_ENV[provider][0]
        assert f"{env_key}: ${{{{ secrets.{env_key} }}}}" in text, provider
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_config.py::test_every_provider_used_by_a_leg_has_a_workflow_env_line -q`
Expected: FAIL —— `agnes`

- [ ] **Step 3: 实现**

`daily_trending.yml` 的 "Run Curator Pipeline" 步骤 `env:` 增加
`AGNES_API_KEY: ${{ secrets.AGNES_API_KEY }}` 与 `ZENMUX_API_KEY: ${{ secrets.ZENMUX_API_KEY }}`。
`AGENTS.md`：更新五角色表（改成链）、删除"RoleConfig 只支持一级 fallback"的失效描述、
补 per-provider 参数表与"classifier 首腿不放 sensenova"的原因、把 probe 的 CI 实测结论写进
"改通道配置后必须验证"一节。另外必须写明三条防"修坏"的记录：`or super-vl` 的 T2 只拿到
3/4 小节（它不当 writer）、`sn glm-5.2` 的 T4 锚点 0/4（它不当翻译腿）、`sn deepseek-flash`
因 sensenova 的 TPM 天花板在 CI 上 T1/T2/T4 全 429（已从 classifier L4 剔除）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/daily_trending.yml && git commit -m "ci: expose the agnes and zenmux keys to the curator" -m "ci: 给策展管线注入 agnes 与 zenmux 的 key"
git add tests/test_config.py && git commit -m "test(config): require a workflow env line for every provider" -m "test(config): 每个 provider 都要在 workflow env 里有映射"
git add AGENTS.md && git commit -m "docs: describe the chains and the per-provider numbers" -m "docs: 写清降级链与 per-provider 参数"
```

---

### Task 7: 门 — 分支 CI + 真实 runner 探测

**Files:** 无代码改动。

**Interfaces:**
- Consumes: Task 1~6 的链路配置；本分支上已提交的 probe 改动。

- [x] **Step 1: 触发探测（已完成）**

Run: `gh workflow run model-probe.yml --ref feat/role-fallback-chains`
Result: run **39211785785**，7m44s 完成，9 个候选全部有结果。

- [x] **Step 2: 核对结果（已完成）**

`zenmux inclusionai/ling-3.1-flash` 四项探针全过（**不是 "无 key/跳过"**）→ zenmux 在 runner
上可达，三条 zenmux 腿是活的。`deepseek-flash` T1/T2/T4 全 429 → 已从 classifier L4 换成
`sensenova-6.8-flash-lite`。完整对照见 spec 的「CI probe 实测结论」一节。

- [ ] **Step 3: 推分支并开 PR**

```bash
git push -u origin feat/role-fallback-chains
gh pr create --title "feat: ordered role fallback chains" --body-file <pr-body.md>
```

PR 描述必须包含：probe 的 run 链接（39211785785）、`[LLM Init] Providers with keys:` 那一行、
本次探测里失败过的腿及其处置、以及 spec/plan 的路径。

- [ ] **Step 4: 等 test job 绿**

Run: `gh pr checks --watch`
Expected: `test` job 绿（ruff + mypy + bandit + pytest/coverage 85%）。

这是真正的门：probe 验的是"模型可用"，这一关验的才是"代码没坏"。
