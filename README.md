# 🌌 auto_github: 开源趋势与大厂动态 AI 智能体策展系统

一个基于“策展思维”设计的高审美、低信噪比开源趋势与大厂动态监测系统。它能够每日自动抓取 GitHub 热门项目及 LLM 大厂的最新动态，通过由商汤 SenseNova 驱动的“抓取 - 分析 - 总结 - 反思 - 翻译 - 排版”六阶段智能体管线，最终输出为精美且富有技术深度的多维画像评级报告。

---

## 🎨 策展设计理念
- **信息降噪即“展位控制”**：过滤冗余的 README 搬运，只选择真正具有架构创新（如 MLA 优化、MoE、KV-Cache、强化学习对齐等）的硬核开源更新。
- **多端触点即“展陈转译”**：输出针对不同阶段画像（初阶入门、中阶实践、高阶大神）深度定制的报告，且排版经过美学精修，完美支持飞书卡片、Slack Block Kit 及高颜值 Markdown。
- **透明审计与反思**：利用 **DeepSeek-R1 (Native Reasoning)** 的强大反思能力，严厉核实所有技术专有名词，杜绝空泛的 AI 腔调宣传词。

---

## 🔄 6-Stage 智能体管线架构

```mermaid
graph TD
    A[Stage 1: Crawl 抓取] -->|GitHub API & Scraper| B[Stage 2: Analyze 分析]
    B -->|画像匹配 / 智能星级评定| C[Stage 3: Summarize 总结]
    C -->|初稿生成| D[Stage 4: Reflect 反思]
    D -->|R1 深度审校 / 去除AI腔调| E[Stage 5: Translate 翻译]
    E -->|学术级专业技术翻译| F[Stage 6: Refine Layout 排版]
    F -->|多端格式打包| G[Webhook 每日推送 & Git 提交日志]
```

---

## 📂 项目结构

```
auto_github/
├── .github/workflows/
│   └── daily_trending.yml      # GitHub Actions 每日自动流 (北京时间上午9:00运行)
├── config/
│   ├── config.yaml             # 系统基础配置 (监测目标、大厂列表)
│   └── personas.yaml           # Beginner / Intermediate / Advanced 画像 prompt
├── reports/
│   ├── latest_daily.md         # 最新生成的每日策展报告
│   └── daily_YYYY-MM-DD.md     # 历史策展报告归档
├── src/
│   ├── config.py               # Pydantic 配置引擎
│   ├── crawler.py              # GitHub Trend HTML 爬虫 & API 客户端
│   ├── llm.py                  # LLM 网关 (内置 429 频率限流指数退避与 R1 reasoning 支持)
│   ├── pipeline.py             # 6-Stage 核心流程调度
│   ├── formatter.py            # 排版渲染器 (飞书/Slack/Markdown)
│   ├── notifier.py             # Webhook 分发与本地写入
│   └── main.py                 # CLI 入口
├── templates/
│   ├── feishu_card.json.j2     # 飞书卡片 Jinja2 模板
│   ├── slack_blocks.json.j2    # Slack blocks Jinja2 模板
│   └── report.md.j2            # Markdown 报告 Jinja2 模板
├── requirements.txt            # 项目依赖
└── README.md                   # 本说明文档
```

---

## 👥 策展画像支持

系统目前深度定制了三种核心画像：
1. **初阶入门者 (`beginner`)**：侧重“这能帮我干什么”，通俗易懂地解释名词，重点挑选带 WebUI、开箱即用的现成应用。
2. **中阶实践者 (`intermediate` - 默认画像)**：面向**系统化 LLM 学习者与 Vibecoding 重度用户**，侧重**工程落地、Agent 协作拓扑、RAG 降噪、MCP 插件、ComfyUI 精细化图像干预及开发效率工具（如 Claude Code, rtk 等）**，关注 Token 经济学与微体验。
3. **高阶学术大神 (`advanced`)**：关注 **MoE/MLA 底层模型架构、强化学习算法 (DPO/PRO/R1)、推理期树搜索 (MCTS) 及 CUDA 算力吞吐极限**，使用硬核高密度的学术语系。

---

## ⚙️ 快速接入指南

### 1. 初始化本地环境
```bash
# 创建虚拟环境并激活
python3 -m venv .venv
source .venv/bin/activate

# 安装项目依赖
pip install -r requirements.txt
```

### 2. 配置环境变量
在项目根目录创建 `.env` 文件：
```bash
# 商汤 SenseNova API 密钥 (DeepSeek-V3 与 R1 在该平台目前限时免费至 2026-08-09)
SENSENOVA_API_KEY="sk-your-sensenova-key"

# (可选) 飞书 webhook 地址 (如有)
FEISHU_WEBHOOK_URL="https://open.feishu.cn/open-apis/bot/v2/hook/xxx"

# (可选) GitHub Token，用于避开 GitHub API 速率限制 (Actions 运行时会自动注入)
GITHUB_TOKEN="your-github-token"
```

### 3. 本地命令行运行

```bash
# 1. 以中阶画像运行一次离线沙盒模拟 (0 消耗 token，快速验证排版与通知链路)
python src/main.py --mock --persona intermediate

# 2. 真实抓取数据并调用 LLM 进行 S/A/B 级策展总结
python src/main.py --since daily --persona intermediate

# 3. 生成高阶学术报告
python src/main.py --since weekly --persona advanced
```

---

## 🚀 GitHub Actions 线上定时自动运行

本仓库已布设好 GitHub Actions 工作流：
1. **定时触发**：每天 UTC 01:00 (北京时间上午 09:00) 自动运行。
2. **Git 归档日志**：生成报告后，工作流会自动将生成的 Markdown 报告存入 `reports/` 目录下，并以 `[skip ci]` 方式自动 commit 并 push 回本仓库，形成不可篡改的**开源技术史记看板**。
3. **推送 Webhook**：自动向绑定的飞书、Slack 或 Discord 机器人推送经过视觉美化排版的互动消息。

### 配置 Actions 密钥 (GitHub Secrets)
在 GitHub 仓库的 `Settings` -> `Secrets and variables` -> `Actions` 下添加以下机密信息：
- `SENSENOVA_API_KEY`: 您的商汤开放平台 API 密钥。
- `FEISHU_WEBHOOK_URL`: (可选) 飞书机器人 webhook 地址。
- `SLACK_WEBHOOK_URL`: (可选) Slack 机器人 webhook 地址。
- `DISCORD_WEBHOOK_URL`: (可选) Discord 机器人 webhook 地址。
