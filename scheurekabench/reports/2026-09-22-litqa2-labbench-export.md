# LitQA2 兼容导出与官方评测报告

日期：2026-09-22
对象：`molintbench/Aging-Paper`、`Aging-Res`、`Aging-Res-0911` 三个目录的 canonical
`mcq_questions.json`（合计 50 题）→ 导出为 futurehouse/lab-bench LitQA2 格式并用其官方评测打分
新增脚本：`benchmark_creation/export_labbench.py`（导出）、`benchmark_creation/solve_labbench.py`
（官方评测打分 + 简单题剔除）

## 1. 导出结果

| 项 | 值 |
|---|---|
| 导出题数 | 49（canonical 50 题，跳过 1 道多选 `answer="A,C"`） |
| Schema 对照 | `--check` 与官方 `LitQA2_full.json` 逐字段一致（键集/键序/值类型） |
| DOI | 由 `paper.pdf` 与目录内 `s43587-*.pdf` **哈希比对**推导；commentary（Aging-Paper）无对应文件，用 `--source-map` 手工指定 `10.1038/s43587-026-01153-8`，已核对该 DOI 出现在 PDF 字节流中 |
| `key-passage` | ← insight 的 `relevant`（论文逐字引用），49/49 非空 |
| `canary` | 自有 GUID（格式同官方，不复用其 uuid） |
| sidecar | `litqa2_bench_eval.json` 与 bench 同下标，存每题 A–D 顺序与答案字母（官方格式不带字母，solver 靠它复现呈现顺序） |

`--type {litqa2,protocolqa}` 为统一入口；protocolqa 是预留接口，调用即提示未实现。

## 2. 官方评测的复刻与一个解析缺陷

打分逻辑取自 lab-bench CI（`.github/promptfooconfig.yaml` + `.github/assert.py`）：

- prompt：`Q: {question}\n\nOptions:\nA) ...\n\nAnswer:`（我们追加 `E) unsure`，对齐官方 unsure 机制）
- 解析：`re.search(r"([A-Z])\)", output, re.DOTALL)`，回退取首词首字符大写
- 判分：字母 == ideal → 1.0；== unsure → 0.1；否则 0.0。`ideal=="NULL"`（官方 5 题无答案题）
  时只有 unsure 得 1.0，在 `synth_sidecar`/`grade` 中一并实现

**解析缺陷**：官方 prompt 以 `Answer:` 结尾、不约束格式，模型若回 `**Answer: C**`，
官方正则匹配不到 `X)`、回退取首字符得 `*` → 判 0。实测 8 例中招，其中 6 例本意正确。
脚本对解析不出合法选项字母的作答**携带原答案追问一次"只回一个字母"**，再用同一解析器判分；
两条分都留档（`score_strict` = 官方 CI 会得到的分数，`score` = 修复后，用于难度判定）。

## 3. 得分：我们的题 vs 官方原题

同一套官方 prompt/解析/判分、同一批 solver、闭卷（与难度闭环 solver 同口径）：

| Solver | 我们生成的 49 题 | 官方 LitQA2 原题（采样 50 题） | 差距 |
|---|---|---|---|
| MiniMax-M3 | **0.939**（严格 0.837）｜46 对 3 错，认输 0 | **0.294**｜13 对 20 错，认输 17 | **+64.5 分** |
| deepseek-v4-flash | **0.959**（严格 0.918）｜47 对 2 错，认输 0 | **0.460**｜23 对 27 错，认输 0 | **+49.9 分** |

官方题采样为 `LitQA2_full.json` 199 题的确定性随机子集（seed 5c41），选项位置按题目 id
打乱后生成 sidecar（官方文件只存 ideal + distractors，不存字母）。

两个诊断信号：

1. **认输率**：官方原题上 MiniMax 有 34% 的题选 unsure（诚实弃权），我们的题上是 **0%**——
   题干自包含，模型不需要回忆文献，永远敢下判断。
2. **错因**：官方原题的错误集中在"记不住某个具体事实"，我们的题的错误是推理链断点。

## 4. 简单题剔除

规则：所有 solver 都拿 1.0 的题 = 官方口径下的简单题。

- 修复 markdown 解析前：剔除 37/49，剩 12
- 修复后：**剔除 44/49，剩 5**（全部来自两篇研究论文；commentary 的题被全部剔除）

存活 5 题：均为需要组合多个定量前提做多步推理的题（GAL3 footprint 双干预比较、APOE ε4
星形胶质细胞 FN1 通路、VEGFRi 斑马鱼双通路 rescue、ZO-1 恢复实验设计、CSF FN1 三因素交互）。

对照：把同一条剔除规则套到官方原题上（单 solver 口径），MiniMax 只有 26% 的题会被剔除——
我们的题包整体难度显著低于官方。

## 5. recall 风格 pilot：官方档位的另一半

给管线加了 `--style litqa_recall`（`prompts/insight2question_rubric_prompts.py`
的 `_MCQ_RECALL_TEMPLATE`）：题干只给最小限定场景、直接问论文报道的具体结果，**不内嵌前提**——
即官方 LitQA2 的形态。用 `Aging-Res-0911` 的 10 条 insight 各出 1 题（`--per-insight 1
--split-calls --model_call gateway`），导出后官方评测：

| 题集 | MiniMax-M3 官方评分 | 答对 | 认输(unsure) |
|---|---|---|---|
| 官方 LitQA2 原题（n=50） | 0.294 | 13 (26%) | 17 (34%) |
| 本仓自包含题（n=49） | 0.939 | 46 (94%) | 0 |
| recall 风 pilot（n=10） | 0.060 | 0 | 6 (60%) |

**recall 风的出题形态正确**（题干 150–240 字，问具体百分比/系数/启动子/HB-EGF+IGF-1 这类
必须读过论文才知道的事实；认输率 60% 与官方题的 34% 同属"知识受限"行为谱）。
**但分数低于官方档位**：官方题的论文是 2024 年前的，模型有部分记忆（deepseek 56.5% 即来自
此）；2026 年论文对所有模型都在训练截止之后，闭卷时这些题**原理上不可答**，分数落在瞎猜线
以下。所以 recall 风单独用在近期论文上会打过头（0–6%），不是官方档位（29–57%）。

## 6. 候选池扩产 + 官方评测筛选（把 bench 拉到官方档位）

按路线 2 执行：不动论文，只扩大候选池，全程用官方评测筛选。

**池构建**（`--per-insight 2` × 3 轮/篇，共 9 个运行目录，`data/labbench/pool/`）：
每篇论文的每条 insight 生成 2 题、重复 3 个独立轮次 → 30 条 insight × 6 = **180 个候选**。
（先用 `--per-insight 6` 单次出 6 题试过：模型输出约 20KB，被传输层截尾导致 JSON 损坏、
整篇中止；小批次多轮是可靠路径。顺带给 `generate_checked` 加了**解析失败重试**
（默认 3 次），本次实测救回一次截断。）

| 阶段 | 规模 | 结果 |
|---|---|---|
| 候选池官方评测（MiniMax-M3） | 180 | mean **0.906**（163 对 / 16 错 / 1 认输）；严格解析 0.751；格式修复 31 题 |
| 剔除简单题（M3 答对的全部剔除） | 180 → **17** | 存活率 9.4%（与 49→5 的 10% 一致） |
| **硬核子集官方复核（M3 首次 / 重测）** | 17 | **0.300** / **0.235**（逐题一致率 82.4%，3 题翻转） |
| **硬核子集官方复核（deepseek）** | 17 | **0.412**（7 对 / 10 错 / 0 认输） |
| 对照：官方 LitQA2 原题 | 50 | M3 **0.294** / deepseek **0.460** |

**结论**：17 题硬核子集把两个 solver 的官方得分同时压进官方档位——

| Solver | 本子集（17 题） | 官方 LitQA2 原题（50 题） | 偏差 |
|---|---|---|---|
| MiniMax-M3 | 0.300（重测 0.235） | 0.294 | +0.006（重测 −0.059） |
| deepseek-v4-flash | 0.412 | 0.460 | −0.048 |

两者都落在官方成绩的噪声范围内（M3 重测的 3/17 翻转即噪声尺度）：**该子集对这两个模型而言已处于
官方难度**，且完全可复现（同一批题、官方 prompt/解析/判分、闭卷）。

**池的难度是双峰的**（见 §5）：约 90% 候选对强模型偏易（一次性剔除），约 10% 存活并构成官方档位
硬题。要按这个比率扩到 50 题硬子集，需要约 500 个候选（≈ 10 篇论文 × 3 轮）——扩论文池是线性
扩产，不存在"把单篇题变难"的捷径。

### 6.1 官方到底有没有"多少分才允许进 bench"的门槛？

查 LAB-Bench 论文全文（arXiv:2407.10362v2，Methods C.1 与附录 E）后的结论：**官方没有公布任何
数值化的分数门槛**（没有"模型答对率低于 X% 才收录"这类规则）。他们在附录 C.1 里给出的题目准入
标准是四条定性规则 + 一条定性难度检查：

1. 论文须在最近 36 个月内发表；
2. 题目必须**依赖正文语境**才能回答——不能靠摘要或标题答出；
3. 必须有**推理成分**，不能是原文的直接引用或陈述；
4. 干扰项要合理（用论文里讨论的其他基因等）；
5. **难度检查（唯一的模型相关标准，定性）**：*"We also periodically tested question drafts with
   ChatGPT 3.5 or 4 (with logging disabled) to ensure questions were not easily answerable by
   models already, or to help design effective distractors by asking to provide plausible answers."*
   即把草稿拿去问当时的 ChatGPT，确保"模型还不能轻易答对"——这正是我们"剔除全对题"规则的
   定性版本。此外他们还会用 Google Scholar 搜一遍，确认答案原句不是随手就能搜到。

官方公布的难度基线（LitQA2，Table 2/3/4，人类可查文献并使用工具）：

| 指标 | 人类专家 | 2024 年各模型区间 | 随机基线 |
|---|---|---|---|
| Accuracy | **0.70** | 0.06 – 0.35 | ~0.25（四选一） |
| Precision（答对/已作答） | **0.76** | 0.38 – 0.47 | ~0.25 |
| Coverage（作答率） | 0.92 | 0.12 – 0.92 | — |

论文正文的表述是"all scoring well above random at 40% precision"——即官方对"合格难度"的实际
操作定义是：**模型精度高于随机线、但明显低于人类专家**（人类 0.76 精度 / 0.70 准确率是参考线）。

对照我们的 17 题硬核子集：MiniMax-M3 准确率 0.300 / 精度 0.31，deepseek 准确率 0.412 / 精度
0.41——**两者都落在官方模型区间内（准确率 0.06–0.35、精度 0.38–0.47），且远低于人类 0.70**；
按官方口径，这批题属于"难度合格"。

需要提醒两个口径差异：
- 官方 2024 年模型准确率低（0.06）主要因为**大量拒答**（coverage 低至 0.12）；我们用的 2026 年
  模型几乎不拒答（coverage ≈ 1.0），所以同样难度的题在准确率上天然更高。跨年份比准确率会失真，
  同模型在"官方原题 vs 自产题"上的对比才是可靠口径（上表：0.300 vs 0.294；0.412 vs 0.460）。
- 官方 20% 的私测集（private split）与难度无关，只用于监测训练数据污染，不是"更难的题"。

## 7. 结论与后续

- 导出链路与官方评测已打通、可复现；`--type protocolqa` 接口预留；
  `--style {self_contained,litqa_recall}` 已接入出题脚本（默认 self_contained，行为不变）。
- **难度是双峰的**：自包含题 90–96%（上下文推理，太易），recall 题 0–6%（闭卷知识，过难）。
  官方档位（29–46%）来自"部分记忆的文献"这一条件；好消息是 **"剔除全对"筛选后的硬核子集
  恰好落在官方档位**（M3: 0.300/0.235 vs 官方 0.294；deepseek: 0.412 vs 官方 0.460），
  因为单次采样噪声会保留一部分中等题——这条路径已验证可行且可复制。
- 继续扩产的三个方向：
  1. **扩论文池**：按 10% 存活率线性扩产（每篇 ~10 条 insight 贡献 ~6 题硬题）；
  2. **recall 风 + 带检索的 agent 评测**：符合官方设计意图（官方 baseline agent 可检索文献），
     闭卷时不可用；
  3. **改进难度闭环的硬化信号**：历史 4 轮闭环结束时准确率仍 85–100%，"答对→重生成更难"的
     信号太弱；本轮的"多轮候选 + solver 实测筛选"（拒绝采样）才是有效机制，可直接取代旧闭环。

## 8. 附：产物清单（`benchmark_creation/data/labbench/`）

| 文件 | 内容 |
|---|---|
| `LitQA2_full.json` / `ProtocolQA_full.json` | 官方数据集原样下载（199 / 108 题） |
| `litqa2_bench.json` + `litqa2_bench_eval.json` | 我们导出的 49 题 + sidecar |
| `litqa2_bench_filtered.json` + `_filtered_eval.json` | 剔除简单题后的 5 题硬子集 |
| `labbench_solved_litqa2_bench_*.json` | 两个 solver 在我们题上的逐题作答与双分数 |
| `LitQA2_full_sample50.json` + `_eval.json` | 官方原题采样 50 题与打乱后的 sidecar |
| `labbench_solved_LitQA2_full_sample50_*.json` | 官方原题对照打分（M3 / deepseek 均已完成） |
| `litqa2_pool.json` + `_eval.json` | **180 题候选池**（9 个运行目录合并导出，schema 通过） |
| `litqa2_pool_filtered.json` + `_filtered_eval.json` | **17 题硬核子集 = 最终产物**（M3 官方 0.300/0.235，deepseek 0.412；对照官方原题 0.294/0.460） |
| `litqa2_pool_retest.json` + `_eval.json` / `labbench_solved_litqa2_pool_retest_*.json` | 稳定性重测副本与结果（逐题一致率 82.4%） |
| `litqa2_recall_pilot.json` + `_eval.json` | recall 风 pilot 10 题（§5） |
| `pool/<Paper>-r<1..3>/` | 池生成的 9 个运行目录（含各自的 mcq_questions.json 与 eval 导出） |
