# Role Fallback Chains — 设计文档

> 日期：2026-10-19
> 状态：待评审
> 关联：`RoleConfig` / `LLMClient` 路由改造；全链改为免费模型

## 问题陈述

`RoleConfig` 现在只有 `model` / `provider` 加一级 `fallback_model` / `fallback_provider`，
即"1+1"两条腿。这在免费档上是结构性不足的，已有三次真实事故：

1. **免费档模型随时下架**：classifier 的 `sensenova-6.7-flash-lite` 被撤，请求 404。
2. **账户级闸门一次带走同 provider 的所有 role**：siliconflow 欠费 402，Hunyuan 与
   Qwen2.5 同时不可用；当时 A/B 两条翻译腿与 Review 挂在同一账户，中文层整条消失四天。
3. **429 是常态而非异常**：L1 撞限流、L2 又落在同一 provider（同一个 TPM 池）时会一起挂。

本次摸底（真实 prompt + 真实代码路径）又找到四个必须修的缺陷：

4. **sensenova 兜底腿被环境变量静默打废**。`resolve_base_url("sensenova")` 优先读通用
   `SENSENOVA_BASE_URL`；本机该变量指向 `api.sensenova.cn/compatible-mode/v2`，而那个端点
   上**没有**这套限免模型，于是稳定返回 `404 model is not found`。404 属模型级不可重试，
   结果是 0.1s 秒失败、链路直接跳过。正确端点是 `https://token.sensenova.cn/v1`
   （7 个限免模型：`deepseek-flash`、`deepseek-v4-flash`、`deepseek-v4.1-flash`、`glm-5.2`、
   `kimi-k3`、`sensenova-6.8-flash-lite`、`sensenova-u1.5-lite`）。CI 未设该变量，纯属侥幸没踩到。

5. **比稿会"静默全选 A"**。`REVIEW_MAX_TOKENS = 64` 下，推理型模型把预算全烧在
   `reasoning_content` 上，`content` 返回空串；而 `_stage_review` 的规则是"只有明确选了 B
   才换，乱答（含空串）一律回到 A"（`pipeline.py:750`）——空正文被当成了"选 A"。
   实测 10 个候选里 **6 个返回空正文**。

6. **`**B**` 会被误判成 A**。`pipeline.py:751` 用 `choice.startswith("B")`，不剥 Markdown；
   而 `sf tencent/Hunyuan-MT-7B` 实测返回的就是 `**A**` 这种带星号的形式。

7. **sensenova 免费档的 TPM/RPM 上限结构性低于 classifier 的 batch**。classifier 是全管线
   单发最大的一枪（一次 batch 灌 N 个仓库，实测 ≈1500 in + 2000 out ≈ 3500 token），而
   sensenova 的限免额度是按低频小请求设计的。连打 3 轮 `deepseek-flash`，
   **first-attempt 成功率 0/3**，报错恒为：

   ```
   {'error': {'message': 'inference exceeds tpm/rpm limit',
              'type': 'rate_limit_error', 'code': 'RateLimitExceeded.EndpointTPMExceeded'}}
   ```

   其中一轮还出现了 `EndpointRPMExceeded`。

## 目标与非目标

**目标**

- 一个 role 挂 3~4 条**有序**腿，全链**免费**模型。
- 选型优先级（用户明确）：**质量 > 稳定性 > 时间/成本**。
- 任一 provider 挂掉 / 限流 / 下架，链路继续；降级路径必须存在（AGENTS.md 原则 5）。
- reviewer 的字母裁判必须**真的能选出 B**——用一个有标准答案的比稿测试证明，而不是
  假设"它没选 B 就是 A 更好"。

**非目标**

- 不做动态探测 / 自动选路：那要额外轮次，违反"减少轮次 > 减少单次 token"。
- 不引入独立节流器；只保留 per-provider 的**最小间隔**（实测依据）。
- 不使用付费模型（见下方"免费性证据"）。
- 不改 `writer_variants` 的 A/B 切换机制，只把两个变体都升级成链。

## 方案选型

- **方案 A（现状）**：primary + 1 fallback。腿不够：免费档的常见失败（下架 / 429 / 账户闸门）
  每一类都能把两条腿同时打掉，然后整份报告落 stub。
- **方案 B（采纳）**：`RoleConfig.chain: list[ModelLeg]`，有序、可带 per-leg `max_tokens`，
  `LLMClient` 逐腿尝试。

## 最终降级链

| role | L1 | L2 | L3 | L4 | 尾部降级 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `translator_a` | `siliconflow` `tencent/Hunyuan-MT-7B` | `openrouter` `nvidia/nemotron-3.5-super-vl:free` | `zenmux` `inclusionai/ling-3.1-flash` | `sensenova` `sensenova-6.8-flash-lite` | 采用 A 通道 |
| `translator_b` | `agnes` `agnes-2.5-flash` | `openrouter` `nvidia/nemotron-3-ultra-550b-a55b:free` | `zenmux` `inclusionai/ling-3.1-flash` | `sensenova` `sensenova-6.8-flash-lite` | 采用 A 通道 |
| `writer` | `agnes` `agnes-3.0-flash` | `openrouter` `nvidia/nemotron-3-ultra-550b-a55b:free` | `zenmux` `inclusionai/ling-3.1-flash` | `sensenova` `sensenova-6.8-flash-lite` | 英文 stub |
| `classifier` | `agnes` `agnes-3.0-flash` | `openrouter` `nvidia/nemotron-3.5-super-vl:free` | `sensenova` `glm-5.2` | `sensenova` `deepseek-flash` | 静态 `infer_tags` / `infer_rating` |
| `reviewer` | Jev `jev-latest`（前置） | `agnes` `agnes-3.0-flash` | `sensenova` `glm-5.2` | `zenmux` `inclusionai/ling-3.1-flash` | 退回 A 通道 |

### 排序原则

1. **可靠性降序**：越靠前的腿越可靠。实测 `zenmux ling-3.1-flash`（翻译 15.7s / 写作
   53.3s 过门 / 分类 22.4s，无 429）比 `sensenova-6.8-flash-lite` 可靠，而 sensenova 是
   我们测过最受限的端点，所以 `zenmux` 腿一律排在 `sensenova` 腿之前。
2. **相邻腿不同 provider**：避免同一个 TPM 池 / 同一个账户闸门把连接的两条腿一起打掉。
3. **classifier 首腿不放 sensenova**：它的单发是全管线最大的一枪，而 sensenova 的免费额度
   按小请求设计（见问题 7）。classifier 一轮只发 1 次（RPM 需求≈0），但它决定全部仓库的
   评级与分桶，**失败代价最高**，所以首腿必须是配额最宽、实测最稳的那条。

### 腿的独立性约束（A/B 与 Review 的有效性）

- 两条翻译通道的**主腿必须是不同模型、不同 provider**，否则 A/B 变同源、比稿失去意义。
- **reviewer 不得使用任一翻译腿的模型**，否则会变成"自己评自己"。
  实测 `sf tencent/Hunyuan-MT-7B` 在"A 漏掉末节 / B 完好"的对照里**仍然选 A**，说明它存在
  **A 偏见**（不是判断力问题，是倾向问题）；而它本身就是 `translator_a` 的 L1 ——
  两个独立理由都要求把它排除出 reviewer。
- L3/L4 允许两条通道共用（当 L1/L2 都已失败时，独立性已无法保证，此时优先"有译文"）。

## 比稿裁判的实测判定（有标准答案）

构造成对样本：`good` = 完整 4 小节译文；`bad` = 同一份译文砍掉最后一个 `###` 小节（模拟漏译）。
两个方向各测一次，正确率才算数：

| 候选 | A 好 / B 漏（期望 A） | A 漏 / B 好（**期望 B**） | 判定 |
| :--- | :--- | :--- | :--- |
| `sensenova` `glm-5.2` | A | B | **2/2 ✓** |
| `sensenova` `sensenova-6.8-flash-lite` | A | B | **2/2 ✓** |
| `agnes` `agnes-3.0-flash` | A | B | **2/2 ✓** |
| `agnes` `agnes-2.5-flash` | A | B | **2/2 ✓** |
| `zenmux` `inclusionai/ling-3.1-flash` | A | B | **2/2 ✓** |
| `siliconflow` `tencent/Hunyuan-MT-7B` | A | **A** | 1/2 ✗ A 偏见 |
| `openrouter` `nvidia/nemotron-3-ultra-550b-a55b:free` | `?The user wan…`（吐推理不吐字母） | B | 1/2 △ |
| `openrouter` `nvidia/nemotron-3.5-super-vl:free` | A | 空 | 1/2 △ |
| `tokenrhythm` `neohorse-1-9b` | 空 | 空 | 0/2 ✗ |

结论：干净裁判只有 5 个；扣掉"同时是翻译腿"的（`agnes-2.5-flash` 是 translator_b L1、
`sensenova-6.8-flash-lite` 是两条翻译链的末腿）后，可用于 reviewer 的是
`glm-5.2`、`agnes-3.0-flash`、`ling-3.1-flash` —— 正好构成 L2/L3/L4。

## 免费性证据

| 模型 | provider | 官方定价 | 免费 | 处置 |
| :--- | :--- | :--- | :--- | :--- |
| `tencent/Hunyuan-MT-7B` | siliconflow | 免费 / 免费 | ✅ | 保留为 translator_a L1 |
| `stepfun-ai/Step-3.5-Flash` | siliconflow | ¥0.70/M in、¥2.10/M out | ❌ | **排除**（曾是 writer L3 的候选） |
| `XingChenAGI/Xing4.0-29B` | siliconflow | 免费 / 免费 | ✅ | 排除：实测 186.4s 且落 STUB |
| `zai-org/GLM-5.3-Flash` | siliconflow | ¥0.80 / ¥2.80 | ❌ | 排除 |
| `inclusionAI/Ling-flash-2.0` | siliconflow | ¥1.00 / ¥4.00 | ❌ | 排除 |
| `openrouter` `:free` 系列 | openrouter | $0 | ✅ | 保留 |
| agnes 全系 | agnes | $0（限免） | ✅ | 保留 |
| sensenova Token Plan 7 个限免模型 | sensenova | 限免 | ✅ | 保留（但额度受限，见问题 7） |
| zenmux `ling-3.1-flash` | zenmux | $0 | ✅ | 保留 |
| `neohorse-1-9b` | tokenrhythm | 免费 | ✅ | 不入选（reviewer 0/2） |
| `intern-ai` 系列 | intern | 0 墨点 | ✅ | 不入选（分类 352.5s / 翻译 87.8s，全场景过慢） |

## 接口设计

### `src/config.py`

```python
class ModelLeg(BaseModel):
    """降级链里的一条腿。"""

    model: str
    provider: str
    # 推理型模型会把输出预算烧在 reasoning_content 上；这条例外只对该腿生效。
    max_tokens: int | None = None


class RoleConfig(BaseModel):
    chain: list[ModelLeg] = Field(min_length=1)
```

- `RoleConfig` 只保留 `chain`；`model` / `provider` 变成 `primary` property（= `chain[0]`），
  让日志与旧调用点无需知道链的存在。
- **兼容**：`model_validator(mode="before")` 接受旧的
  `{model, provider, fallback_model, fallback_provider}` 形状并**归一化**成 chain
  （leg0 = model/provider，leg1 = fallback_model/fallback_provider），
  这样 `config.yaml` 与代码默认值可以分两步迁移，且 drift 用例仍有效。
- `writer_variants` 的 b 案同样升级为链。

### `src/llm.py`

```python
def legs(self, role: str) -> list[ModelLeg]      # 过滤掉已熔断的腿与无 client 的 provider
def has_role(self, role: str) -> bool            # = bool(self.legs(role))
def call_llm(self, messages, role="writer", temperature=None,
             max_tokens=None, retries=3, backoff_factor=2.0) -> dict[str, Any]
```

- `call_llm` 逐腿调用 `_try_model`，第一条产出非空正文的腿胜出，返回值里带 `"leg"` 标识。
- **熔断粒度改为 per-(role, leg)**：`self._exhausted_legs: set[tuple[str, str]]`，
  key 是 `(role, f"{provider}/{model}")`。原 `_unavailable_roles` 的意图（"别为已经确定
  失败的通道逐仓库付失败税"）保持不变，但不再因为一条腿死了就废掉整个 role。
- `_try_model` 的**空正文视为失败**（`content.strip()` 为空 → 记一次 failed attempt 后重试/换腿）。
  这是问题 5 的根因修复：空正文不再被当成一个合法答案交给上层。
- **不可重试状态码的语义不变**：401/402/403 → provider 级拉黑；404 → 只停该模型，
  继续走下一腿（跨 provider 的正常降级）。

### per-provider 参数（实测依据）

| provider | base_url | 超时 | 最小间隔 | 依据 |
| :--- | :--- | ---: | ---: | :--- |
| `sensenova` | `https://token.sensenova.cn/v1` | 30 → **60** | **6.0s** | `glm-5.2` 写作实测 66.0s，30s 下每次都要先白烧一次超时重试；3s 内发第二发必 429 |
| `openrouter` | `https://openrouter.ai/api/v1` | 45 → **60** | 2.0s | `super-vl` 翻译实测 39.2s，已逼近旧 45s |
| `siliconflow` | `https://api.siliconflow.cn/v1` | 90 | 2.0s | Hunyuan 长输出 >30s，是模型慢不是通道死 |
| `agnes` | `https://apihub.agnes-ai.com/v1` | **60** | 2.0s | 写作实测最大 40.6s |
| `zenmux` | `https://zenmux.ai/api/v1` | **60** | 2.0s | 写作实测 53.3s |
| `tokenrhythm` | `https://tokenrhythm.studio/v1` | 90 | 2.0s | 预留（当前无 role 使用） |
| `intern` | `https://discovery-api.intern-ai.org.cn/v1` | 120 | 2.0s | 预留（当前无 role 使用） |

`Retry-After` 不再作为设计元素：实测 sensenova 的 429 响应里没有这个头。

### base_url 守卫（问题 4 的修复）

`resolve_base_url(provider)` 增加 per-provider host allowlist：当环境变量提供的 URL 不在
白名单内（例如本机那个 `api.sensenova.cn`），**打印 `[Config Warning]` 并回落到代码默认值**，
而不是把请求发到一个必然 404 的端点。宁可响一声，不要静默降级。

### `src/pipeline.py`

- `REVIEW_MAX_TOKENS` 64 → **256**（推理型模型要有余量吐那一个字母）。
- `_stage_review` 的判定改为：先剥 Markdown 与标点（`strip("*_` \t\r\n.")`），
  只有归一化后**首字符是 A 或 B** 才采纳；既不是 A 也不是 B 的答案（含空串）**不再默认等于 A**，
  而是交给 `llm.call_llm` 换腿；所有腿都答不出才退回 A 通道。
- 新增一行日志：本轮用了链上第几条腿（便于在 Actions 日志里看出降级是否被触发）。

### `.github/workflows/daily_trending.yml`

- `env:` 增加 `AGNES_API_KEY` / `ZENMUX_API_KEY` 映射（`_PROVIDER_ENV` 已含 provider 定义）。
- `model-probe.yml` 同步（已在 `feat/role-fallback-chains` 的 `ci(probe)` 提交里完成）。

### `AGENTS.md`

- 角色表改成链；删掉"RoleConfig 只支持一级 fallback，所以写作链路是 super-vl → ultra →
  英文 stub"这类已失效的描述；补上 per-provider 参数表、腿的独立性约束、以及
  "classifier 首腿不放 sensenova"的原因。

## 风险与未验证项

1. **zenmux 在 GitHub runner 上的可达性未验证**。本机必须挂代理（本机 DNS 被污染），
   而仓库里 `scripts/probe_models.py` 的旧注释断言"CI runner 直连会被 DNS 污染挡掉"，
   与"GitHub Actions 没有防火墙"的判断相反。这直接决定 `writer L3` / `reviewer L4` /
   翻译 `L3` 是不是死腿 —— **必须由 `model-probe.yml` 在真实 runner 上实测**，
   本机带代理测出的结论不算数。
2. **sensenova 作为末位腿的能力边界**：它的限免额度对上翻译一发（≈2000 token）实测能过
   （22.7s），但余量小。因此它只出现在 L4（最后一条），且不再出现在 `classifier` 首腿。
3. **`or super-vl:free` 的传输层不稳**：实测 `502` ×2、流解析失败 1 次。它是 L2 而不是 L1，
   且每腿自带 3 次重试，可以接受。
4. **agnes / zenmux 的长期免费承诺**未知；链式结构本身就是应对"随时变卦"的机制。
