---
name: litqa2-bench-production
description: 从论文生成 LAB-Bench LitQA2 格式的题包，并用官方评测打分、把难度校准到官方档位。当用户提到「生产/出一批 litqa2 题」「导出 LAB-Bench 格式」「用官方评测打分」「剔除简单题」「把题包难度拉到官方档位」「litqa2 bench」「protocolqa 接口」时使用，即使用户没有明说 "LAB-Bench" 或 "skill"。
---

# LitQA2 题包生产流程

从论文的 `insights.json` 出发，用 `scheurekabench/benchmark_creation/` 下的脚本产出**与官方
futurehouse/lab-bench LitQA2 逐字段一致**的题包，并用官方评测体系打分、筛选到官方难度档位。

## 前置条件

| 项 | 值 |
|---|---|
| 工作目录 | `scheurekabench/benchmark_creation/`（以下命令均在此目录下执行） |
| Python | `/Users/mac/Documents/hunpo_work/HeurekaBench/.venv/bin/python`（含 dotenv/httpx/openai/anthropic/pymupdf） |
| 网关配置 | 仓库根 `.env`：`BASE_URL` / `OPENAI_KEY` / `CLAUDE_KEY` / `MODEL_NAME`；出题模型可用 `QUESTION_MODEL` 覆盖 |
| 官方数据集 | `data/labbench/LitQA2_full.json`（199 题）、`ProtocolQA_full.json`（108 题） |
| 输入 | 每篇论文一个目录，含 `insights.json`；若只有 PDF，先跑 `run_creation.py --base_dir <dir> --mode debate --model_call gateway` 得到 insights |

## 流程（六步）

### 1. 生成候选池：多轮小批次，不要一次多题

```bash
# 每篇论文 3 个独立轮次（每条 insight 2 题/轮 → 每条 insight 共 6 题候选）
for r in 1 2 3; do
  mkdir -p pool/<Paper>-r$r && cp <paper_dir>/insights.json pool/<Paper>-r$r/
  $PY insights_to_questions.py --insight_json_path pool/<Paper>-r$r/insights.json \
      --model_call gateway --qtype mcq --structured --per-insight 2 --split-calls
done
```

关键：**单次调用只出 2 题**。实测单次输出超过约 20KB 会被传输层截尾、JSON 损坏、整篇中止；
`--per-insight 3..8` 的池模式存在此风险，批量生产不要用。多个目录可分批并发（每批 3 个进程，
`for ... & done; wait`；`xargs -I` 会因命令行过长失败）。

### 2. 答案位置重排（必须在导出前）

```bash
for d in pool/*/; do $PY rebalance_mcq_positions.py $d/mcq_questions.json; done
```

池模式不做 prompt 内答案位置指令（一次调用多题时单一位置会冲突），位置全靠这一步确定性轮转。

### 3. 导出官方格式并自检

```bash
$PY export_labbench.py --type litqa2 \
    --base_dir pool/<Paper>-r1 pool/<Paper>-r2 pool/<Paper>-r3 ... \
    --out data/labbench/litqa2_pool.json \
    --source-map "<Dir>=https://doi.org/10.1038/s43587-026-01154-7,..." --check
```

- 字段映射：`options[answer]` → `ideal`，其余选项 → `distractors`；insight 的 `relevant` →
  `key-passage`；`id` 为新 uuid4；`canary` 每次生成新 GUID（不复用官方 uuid）。
- DOI 默认由 `paper.pdf` 与目录内 `s43587-*.pdf` **哈希比对**推导（目录里可能有别的论文副本，
  按文件名会认错）；推不出时用 `--source-map` 指定。注意 source-map 的键是**目录名**。
- `--check` 与官方文件逐字段（键集/键序/值类型）对照，失败退出码 1——**没过就别往下走**。
- 多选题（answer 含逗号）、非 ABCD、缺答案字母会被跳过并打印计数。
- 同时产出 sidecar `<out>_eval.json`（与 bench 同下标，存每题字母顺序与答案字母）。

### 4. 官方评测打分

```bash
$PY solve_labbench.py data/labbench/litqa2_pool.json --solver MiniMax-M3,deepseek-v4-flash \
    --drop-all-correct
```

复刻官方 CI（promptfoo 配置 + `.github/assert.py`）：prompt 为
`Q: {question}\n\nOptions:\nA) ...\nE) unsure\n\nAnswer:`；解析优先 `([A-Z])\)`、回退首词首字符；
判分 1.0 正确 / 0.1 认输 / 0.0 错误；`ideal=="NULL"` 的无答案题只有认输得 1.0。

- **格式修复**：模型若答 `**Answer: C**`，官方正则解析成 `*` 判 0。脚本会携带原答案追问一次
  "只回一个字母"再用同一解析器判分；`score_strict` 是官方 CI 会得到的分数，`score` 是修复后
  （用于难度判定）。
- **断点续跑**：结果缓存在 `labbench_solved_<bench>_<model>.json`，按题下标跳过已有项。
- 换 solver 或补跑单模型：重复第 4 步、改 `--solver` 即可；缓存按 bench 名+模型名分文件。

### 5. 筛选到官方档位

```bash
# a) 剔除简单题：solver 答对的题全删 → 硬核子集
$PY solve_labbench.py data/labbench/litqa2_pool.json --solver MiniMax-M3 --drop-all-correct
# b) 或多模型面板保留"有区分度"的题：面板均分落在区间内
$PY solve_labbench.py data/labbench/litqa2_pool.json --solver A,B --keep-band 0.45 0.55 --band-suffix official
```

产出 `<bench>_filtered.json` / `<bench>_official.json` 及对应 sidecar。

### 6. 复核最终产物（必做）

```bash
$PY solve_labbench.py data/labbench/litqa2_pool_filtered.json --solver MiniMax-M3,deepseek-v4-flash
```

给出子集上的官方分数，并与**同模型在官方原题上的分数**对比（可靠口径，见下）。建议再改个
bench 名重跑一次做稳定性检验：实测同一批 17 题重测有 3/17 翻转，这是单次采样的噪声尺度。

## 规模换算与验收标准

- **存活率约 10%**：候选池 → 硬核子集。一篇论文（~10 条 insight）× 3 轮 = 60 候选 ≈ 6 题硬题。
  要 N 题硬题就准备约 `10 × N` 个候选（≈ N/6 篇论文 × 3 轮）。
- **官方档位（验收线）**——官方论文（arXiv:2407.10362v2）公布的 LitQA2 基线：

  | 指标 | 人类专家 | 2024 年各模型 | 随机基线 |
  |---|---|---|---|
  | Accuracy | 0.70 | 0.06 – 0.35 | ~0.25 |
  | Precision（答对/已作答） | 0.76 | 0.38 – 0.47 | ~0.25 |

  官方对"合格难度"的实际定义是"模型精度高于随机、但明显低于人类"。注意 2024 年模型准确率低
  主要因为**大量拒答**（coverage 最低 0.12），现代模型几乎不拒答——**跨年份比准确率会失真**，
  必须用同模型在官方原题上的分数作对照。
- **同模型对照基线**（本仓实测，n=50 官方原题采样）：MiniMax-M3 **0.294**、deepseek-v4-flash
  **0.460**。自产题包在该模型上落到同一区间即算达标（实测 17 题硬核子集：M3 0.300/0.235、
  deepseek 0.412）。

## 常见坑

| 现象 | 原因 / 处理 |
|---|---|
| 生成报 "no parseable structured questions" | 长输出被截尾。脚本已自动重试 3 次；仍失败则减小单次题量（2 题/次） |
| 某个 solver 长时间零推进（10 分钟级） | 网关间歇挂起。缓存断点续跑，换个模型或稍后重跑；MiniMax-M3 最稳（~5s/题） |
| 池里模型答对的题被删后只剩极少 | 正常，~10% 存活；按 `10×N` 准备候选 |
| 子集复核分数与首次测量差很多 | 单次采样噪声（逐题约 18% 翻转），用重复测量或面板口径描述难度 |
| `sources` 为空 / DOI 错 | 目录里 `paper.pdf` 与 `s43587-*.pdf` 哈希不匹配（commentary 类文章），用 `--source-map` 指定 |
| 想加 recall 风（文献回忆）题 | `--style litqa_recall`（仅 mcq）。2026 年论文闭卷时原理上不可答（实测 M3 0.06），只适合带检索的评测 |

## 相关文档（需要细节时读）

- `scheurekabench/PIPELINE.md` §5.5–5.7：导出/官方评测/候选池工作流的完整说明
- `scheurekabench/reports/2026-09-22-litqa2-labbench-export.md`：本次全链路实测数字、官方准入
  标准原文引用（§6.1）、recall 风 pilot、产物清单
- `benchmark_creation/export_labbench.py` / `solve_labbench.py` 的模块 docstring：字段映射与
  判分语义的权威说明
