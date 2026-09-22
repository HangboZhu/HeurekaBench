# Aging-Res 出题全流程报告（进行中）

日期：2026-09-11
论文：`molintbench/Aging-Res/paper.pdf`
（Nature Aging 2026, Carver et al., "Spatial mapping and senolytic targeting of senescent and
disease-associated microglia in aged mouse brain white matter", 37 页原创研究论文；
PDF 原件 `s43587-026-01154-7.pdf`）

流程：debate 模式（Step 1a 抽取 → 多 LLM 辩论质证 → 裁判裁决 → 终审 agent 复核），
运行目录经 `molintbench/.aging_res_links/Aging-Res` 软链隔离，避免 base_dir 扫到其它论文重跑。

## ⚠️ 模型变更记录（本次运行的实际角色配置）

用户在本次运行前更换了 `.env` 里的模型配置。实测后确认：

| 角色 | `.env` 配置 | 本次实际使用 | 说明 |
|---|---|---|---|
| 抽取（Step 1a） | `--model_call claude_code` | claude CLI（全局配置 `deepseek-v4-flash[1M]`） | 10 条 insight，38.9 KB，质量良好 |
| 辩手 1 | `DEBATE_MODELS` 第 1 个 | **MiniMax-M3** | 实测 10.4 万字符 prompt 14–31 秒返回 |
| 辩手 2 | `DEBATE_MODELS` 第 2 个 | **MiniMax-M2.5-highspeed**（替换 deepseek-v4-flash） | 实测 10.4 万字符 prompt 24 秒返回 |
| 裁判 | `JUDGE_MODEL=gpt-5.6-sol` | Insight1–4：gpt-5.6-sol；Insight5–8：**MiniMax-M3** | gpt-5.6-sol 遭网关 503 连续打断致裁决失败，改用 MiniMax-M3 补裁（除首轮尝试外均为 revise/accept，置信度 5） |
| 终审 | 默认 `claude_code` | claude_code（`CLAUDE_CODE_MODEL=MiniMax-M3`） | 11.7 万字符 review prompt 31 秒返回合法 JSON |

### 结果（Step 2 出题）

| 产物 | 内容 |
|---|---|
| `oe_questions.json/.txt/_eval.json` | **20 题**（10 条 insight × 2）。初版题型：comparison 8 / multi_hop 5 / counterfactual 3 / extraction 2 / experimental 2，题干 p50=640 |
| `mcq_questions.json/.txt/_eval.json` | **20 题**（定稿 = 闭环第 2 轮）。题型：comparison 10 / counterfactual 8 / multi_hop 2，题干 p50=675 |
| 自包含泄漏 | 定稿 0（初版 MCQ 有 1 题残留 "according to" 措辞，闭环重生成后清零） |

### 难度打分与闭环（solver 盲做，无原文）

基线（初版 20 题）：

| 模型 | MCQ 精确匹配 | OE 均分（grader gpt-5.6-sol） |
|---|---|---|
| MiniMax-M3 | 17/20（85%） | 4.35（1:1, 2:1, 4:6, 5:12） |
| deepseek-v4-flash | 20/20（100%） | 4.45（1:1, 4:7, 5:12） |

目标区间：MCQ 50–70%、OE 2.5–3.5 —— 初版两项都远超，题目偏简单。

**MCQ 难度闭环（4 轮，错误并集策略）**：0.93 → 0.93 → 0.97 → 0.97，始终高于目标上限；
加固无效（与上次 Aging 评论论文的结论一致：MCQ 存在天花板，热点主题的可答面难以消除）。
定稿回写最接近目标的 **第 2 轮（0.93：MiniMax-M3 85% / deepseek 100%）**。

**OE 难度闭环（4 轮，错误并集策略）** ✅ 有效：

| 轮次 | 整体均分（目标 2.5–3.5） | 题集 |
|---|---|---|
| 基线 | 4.40（M3 4.35 / ds 4.45） | 初版 20 题 |
| 第 2 轮 | 4.05（ds 4.16） | 18/20 题换新 |
| **第 3 轮（定稿）** | **3.54** | 16/20 题换新 |
| 第 4 轮 | 3.64（M3 3.45 / ds 3.83） | 11/20 题换新 |

定稿（第 3 轮）**MiniMax-M3 均分 3.25（已落入目标区间）**，deepseek-v4-flash 3.82（17/20 题已评，
3 题因 grader 超时未判分）；出现 5 道 1 分题（solver 完全答不动）。定稿题型：
comparison 9 / counterfactual 8 / multi_hop 3，题干 p50=802，自包含泄漏 0。

闭环机制得到验证：**OE 可通过"双 solver 错误并集 + 组合前提重生成"稳定压分（4.40 → 3.54，-0.86）**，
效果明显好于上次 Aging 评论论文（3 轮仅 -0.22）；MCQ 则压不动。

### 结论（难度侧）

论文本身（原创研究、空间转录组 + 衰老/小胶质细胞）题材仍是通识语料大户。
OE 靠闭环重生成可以压进目标区间；MCQ 的天花板问题依旧，需要选题侧优先
「冷门系统 / 新方法学的原创研究论文」（与 `2026-09-08-aging-paper-run.md` 的建议一致）。



### `utils/llm_gateway.py` 的健壮性修复（本次运行中沉淀）

1. **非 GPT 系模型优先 CLAUDE_KEY**：OpenAI 兼容端点的 key 回退循环原先盲试 OPENAI_KEY，
   而 deepseek/MiniMax/glm/qwen 等模型只归 CLAUDE_KEY 账号组，白烧重试。
2. **关闭 SDK 内部重试**（`max_retries=0`）：OpenAI/anthropic SDK 默认在 HTTP 层重试 2 次，
   把一次卡住的调用放大成 ~30 分钟（超过管线自己的 600 秒读超时 3 倍）；现在超时会按时暴露并由
   管线自身的重试/换 key 逻辑接管（实测：10 分钟内暴露并自动重试）。


### 发现 1：`DEBATE_MODELS` 里模型名有笔误

原值 `iniMax-M3,deepseek-v4-flash`（少一个 `M`），网关上不存在 `iniMax-M3`，调用必然失败。
已修正为 `MiniMax-M3,deepseek-v4-flash`。

### 发现 2：deepseek-v4-flash 无法承担本管线的辩手角色（大 prompt 卡死）

用论文真实辩手 prompt（10.4 万字符 + `max_tokens=16384`）实测：

| 模型名 / 路由 | 结果 |
|---|---|
| `deepseek-v4-flash`，OPENAI 兼容端点 | 空响应（~12 秒） |
| `deepseek-v4-flash`，anthropic 端点（CLAUDE_KEY） | 空响应（~12 秒） |
| `deepseek-v4-flash[1M]`，`max_tokens=2000` | 3 秒返回（输出被截断的假象） |
| `deepseek-v4-flash[1M]`，`max_tokens=8000/16384` | **>700 秒不返回** |
| `deepseek-v4-flash`，anthropic 端点（CLAUDE_KEY），`max_tokens=16384` | **>900 秒不返回** |

**根因：deepseek-v4-flash 是推理模型**。原始响应里除正文外还有 `reasoning_content`（OpenAI 端点）
/ `thinking` 块（anthropic 端点）；空响应 = `max_tokens` 预算被思考轨迹吃光、正文为空。
大 prompt 下思考轨迹极长，表现为挂起（管线 600 秒读超时 → SDK 内部重试 → 换 key，
单次调用空转约 1/3 小时）。**第一次运行因此被拖死并中止**：deepseek 零产出。

### 路由修复（按用户指示）

`utils/llm_gateway.py`：非 GPT 系模型（claude/qwen/glm/MiniMax/deepseek/hy3 等）在
OpenAI 兼容端点的 key 回退循环里**优先 CLAUDE_KEY**（这些模型属于 CLAUDE_KEY 账号组，
先试 OPENAI_KEY 只会白烧重试）。anthropic 端点本就是 CLAUDE_KEY，保持不变。
注意：该修复解决的是「路由浪费」，**不改变 deepseek 在大 prompt 上不可用的事实**。


### 发现 3：gpt-5.6-sol 也是「prompt 一大就极慢」

| prompt 规模 | 结果 |
|---|---|
| 小请求（<1k 字符） | 3 秒 |
| 2.6 万字符（裁判 prompt 实测） | 27 秒 |
| 8.5 万字符 | >240 秒不返回（`response_format=json_object` 开关都试过，与它无关） |
| 生产裁判 prompt（约 3.5 万字符） | 约 40 分钟/次（能出结果，verdict=revise） |

### 其它模型实测（10.4 万字符辩手 prompt）

| 模型 | 结果 |
|---|---|
| MiniMax-M3 | ✅ 14–31 秒，5.2k–8.1k 字符陈述 |
| MiniMax-M2.5-highspeed | ✅ 24 秒，5.8k 字符 |
| MiniMax-M2.7 | ⚠️ 挂起 >200 秒 |
| deepseek-v4-pro | ⚠️ 挂起 >200 秒 |
| glm-5.3-flash / glm-5 / hy3 | ❌ 网关 503 `no available accounts` |

结论：当前网关时段内，只有 MiniMax 系（M3 / M2.5-highspeed）能稳定处理整篇论文的 prompt。

## 产物清单

- `insights_paragraphs_claude_code.txt` — Step 1a 抽取的 10 条 insight（38.9 KB）
- `debate_transcript_claude_code.json` — 辩论全程留痕（含每条裁决、置信度、终审结论，支持断点续跑）
- `insights.json` / `insights_debate_claude_code.json` — 8 条已裁决 insight（终审：8/8 通过，无 flagged）
- `oe_questions.json/.txt/.eval.json` — OE 16 题
- `mcq_questions.json/.txt/.eval.json` — MCQ 16 题
- `pipeline_20260911.log` — 全程日志
- `chain_questions.sh` / `watch_stall.sh` — 本次运行的守护脚本（自动衔接 Step 2、停滞检测）
- `recover_insights.py`（仓库 `scheurekabench/`）— 网关长时间不可用时，从 transcript 重建
  `insights.json` 的应急脚本（复刻 `process_paper` 尾部逻辑：裁决汇总 → 终审 → 落盘）

## 待办

1. ~~补跑 Insight9/10~~ ✅ 2026-09-12 完成：网关恢复后 `--force` 重跑辩论（transcript 缓存 1–8，
   只补辩论/裁决 9–10，两条均 accept），重跑 Step 2 得到 20+20 题。
2. 若要用回 deepseek 作辩手，需等网关侧修复其大 prompt 长思考问题；
   短期可用 `CLAUDE_CODE_MODEL=<可用模型>` 覆盖出题/终审的 CLI 模型。
3. OE 难度闭环运行中（`pipeline_20260912.log`，守望脚本 `watch_oe_loop.sh`）；
   结束后按闭环规则回写定稿题集与 `oe_questions_eval.json`。
4. 遗留：`.env` 的 `DEBATE_MODELS` 仍是用户配置的 `MiniMax-M3,deepseek-v4-flash`（本运行用
   `--debaters` 覆盖）。若后续直接用 `.env` 跑，deepseek 仍会在辩手角色卡死。
