# AGENTS.md

本仓库的约定，供在此工作的 agent 遵守。

## 产出数据不进 git

生成的题包、评测数据、候选池、打分记录等**产出物一律不提交、不推送**：

- `creating_labbench/` —— 题包归档（已 ignore）
- `scheurekabench/benchmark_creation/data/` —— 本地数据与中间产物（已 ignore）
- `molintbench/` —— 论文原文与 PDF（已 ignore，版权原因尤其不能推）

随仓库走的只有：代码、文档（`PIPELINE.md`、`reports/*.md`）、skill、以及少量测试夹具。
若某次确实需要提交数据文件，先与仓库所有者确认。

## 管线位置速查

- 出题与评测脚本：`scheurekabench/benchmark_creation/`（`insights_to_questions.py`、
  `export_labbench.py`、`solve_labbench.py`、`rebalance_mcq_positions.py` 等）
- 流程文档：`scheurekabench/PIPELINE.md`（§5.5–5.7 为 LAB-Bench 导出与官方评测）
- LitQA2 题包生产流程已封装为 skill：`.agents/skills/litqa2-bench-production/`
- Python 环境：仓库根 `.venv/bin/python`（含 fitz/dotenv/httpx/openai/anthropic）
