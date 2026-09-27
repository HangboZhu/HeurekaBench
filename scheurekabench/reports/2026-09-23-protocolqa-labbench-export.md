# ProtocolQA 兼容导出与官方评测报告

日期：2026-09-23
对象：`molintbench/protocal-0923/` 下三篇 Nature Protocols 论文的 Procedure（VINE-seq、
肠道类器官动态成像、Cu 催化剂重构）→ 抽成协议块 → 出排障题 → 导出为 futurehouse/lab-bench
ProtocolQA 格式并用其官方评测打分、拒绝采样校准难度
新增脚本：`benchmark_creation/extract_protocol.py`（抽协议）、
`benchmark_creation/protocol_to_questions.py`（出题）；
`export_labbench.py` 的 `--type protocolqa` 由预留接口改为实现。

## 0. 结论摘要

- **信封与官方逐字节可比**（8 键、键序/值类型、`indent=1` 无尾随换行），`--check` 通过。
- **条目形态也通过 `--check-style`**（这一点与 LitQA2 的自包含题不同）：题干 158–318 字符
  （官方 95–479）、`ideal` 86–199 字符（官方 16–220）、干扰项 3–4 个（官方 3–6）、协议
  850–5803 字符（官方 569–14188），`ideal` 点名步骤率 97%（官方 86%）。
- **官方同模型基线（本次实测）**：官方 108 题上 MiniMax-M3 **0.425**、deepseek-v4-flash **0.850**；
  自产候选池 136 题的 M3 0.907 / deepseek 0.926——**比官方题容易**（协议块更短 + 要求答案由
  原文支撑），筛后子集（13/18 题）才落到官方档位附近。ProtocolQA 是开卷题（协议原文给答题者），
  整体比 LitQA2（0.294 / 0.460）容易得多。
- **一个必须知道的坑**：MiniMax-M3 在同一题上两次作答只有 38–39% 的字母一致率，按它单次筛出的
  "难题"重跑就从 0.294 跳到 0.728——难度声明要用稳定的 solver（deepseek，83–92% 一致率）
  并重复测量。详见 §5.1。
- 全链路命令与坑已写入 `PIPELINE.md` §5.8 与 skill 的 ProtocolQA 附录。

## 1. 协议抽取（`extract_protocol.py`）

三篇论文的 Procedure 段落结构不同，抽取器按"字号大于正文 / 正文同号的 semibold"识别标题、
按标题切块，并逐页清掉页眉、页码、图注、图内标签与表格列。

| 论文 | Procedure 页 | 步数 | 块数 | 块字符区间 | Troubleshooting 行 |
|---|---|---|---|---|---|
| VINE-seq（`s41596-026-01434-x`） | 15–19 | 70 | 5（全部带编号） | 1169–5448 | 7 映射 / 7 未映射 |
| 肠道类器官成像（`s41596-026-01432-z`） | 14–23 | 82 | 10（全部带编号） | 850–5027 | 11 映射 / 4 未映射 |
| Cu 电催化重构（`s41596-026-01430-1`） | 15–34 | 63 | 11（8 编号 + 3 未编号） | 1684–5814 | 4 映射 / 5 未映射 |

- 块 = 论文自己的小节（VINE-seq 的 Stage 1–5、organoid 的 `Sample preparation` / `Live imaging`
  / `Antibody staining` 等、Cu 的 `Procedure N` + `Tier N` + `Section N` 叠加标题）。
- **保留论文原番号**，不重新编号：论文正文会交叉引用步骤号（"repeat Step 3"）。因此一个覆盖
  步骤 16–38 的块，块内就显示 16. … 38.。
- Cu 的 Procedure 3 整段用选项 A/B/C 写、没有步骤号，标 `numbered=false` 并**不出题**
  （引用不了步骤的题不符合本子集形态）；Procedure 4 曾因 `Option C:` 被误判为标题而碎成
  1–2 步的片段，现已把 `Option X:` 归为块内标签。
- 清洗量（丢弃行数）：VINE-seq 80、organoid 297、Cu 773（页眉/图注/图内标签/表格内容/坐标轴）。
- **Troubleshooting 表**（Step / Problem / Possible reason / Solution）单独抓出来，作为出题时的
  参考依据（论文自己认证的"故障→解法"），**不进入导出的协议文本**——答题者看不到它。
- 未映射的行都核对过：Cu 的 5 行指向未编号的 Procedure 3，VINE-seq 的 7 行指向
  "Downstream library prep / sequencing data"（无步骤号），确属无法映射，不是丢失。

## 2. 出题（`_PROTOCOLQA_TEMPLATE` + `protocol_to_questions.py`）

生成单元是**协议块**而不是 insight：题目问"按这份协议做完，观察到 X 现象，该改哪一步"，
参考答案是一句话的补救措施并点名步骤号。

硬规则（写在 prompt 里，逐条对应官方实测形态）：

- 题干：排障体（`After <做某事> you notice <症状>. What could you do to improve <Y>?`）、
  ≤479 字符、单段、以 `?` 结尾、**不泄漏解法**、不得照抄步骤原文、不得引用协议外材料。
- `ideal`：≤220 字符（目标 40–140，官方中位 50）、**必须点名步骤号 + 具体改动 + 简短理由**；
  优先改一个具体数值/顺序/遗漏动作，"更小心一点"式的复述不被接受（除非块内 CRITICAL STEP
  原文就是这句提醒）。
- 干扰项：恰好 3 个、与 `ideal` 同量级长度、**三种不同失败模式各一**（反向 / 量级错 /
  步骤或目标错），且都不得同样解决问题。
- 防编造：出现在题面与选项里的试剂、数值、温度、步骤号必须来自块内文本或其显式改动；如果该块
  找不到站得住的"故障→解法"对，**允许返回空数组**（这是诚实出口，实测 Cu 某个块触发过一次）。
- `question_type`：`inconsistency` / `critical_step` / `parameter` / `order` / `handling` /
  `complete_step`，用于审计配比。

产出：`protocolqa_questions.json`（嵌套，带 doi/步骤区间/协议原文）、`.txt` 渲染、
`protocolqa_questions_eval.json`（扁平，导出用）。

## 3. 导出与两层保真（`--type protocolqa`）

| 项 | 值 |
|---|---|
| 信封 | `id / question / ideal / distractors / canary / source / protocol / subtask` 8 键，键序与值类型同官方；`source` 恒为 `null`（官方 108/108 如此） |
| 序列化 | `json.dump(..., indent=1, ensure_ascii=False)`、无尾随换行——与官方 `ProtocolQA_full.json` 一致，`--check` 通过 |
| `protocol` 字段 | **唯一允许多行的字段**（官方 108/108 含换行）：只归一化 tab/行尾空格/空行，不做 `clean_text` 压平 |
| `subtask` | 自有值 `protocolqa-v1-heureka`（官方为 `protocolqa-v1-public`，冒用会让自产题被误认成官方题） |
| `canary` | 自有 GUID |
| DOI | 只进 sidecar 的 `sources`（官方信封无 DOI 位置；官方 `source` 全为 null） |
| sidecar | `<out>_eval.json` 存每题字母顺序 + 答案字母；**理想顺序由题 id 确定性洗牌**（本子集没有"正规"选项顺序，不像 MCQ 有 A–D 约定） |

**跳过规则**（都会计数打印）：`ideal` 为空、干扰项不在 3–6、干扰项重复、`ideal` 与干扰项重合、
以及**补救措施引用了本块未定义的步骤**（见 §7）。本轮 138 生成 → 136 导出（跳 2 条引用块外步骤）。

**形态自检** `--check-style` 的输出（官方 108 题对照）：

| 维度 | 官方 | 本池 136 题 |
|---|---|---|
| 题干字符 | 95–479 | 158–318 |
| `ideal` 字符 | 16–220 | 86–199 |
| 干扰项个数 | 3–6 | 3–4 |
| 协议字符 | 569–14188 | 850–5803 |
| 协议步数 | 7–69 | 3–26（**低于官方**：我们按小节切得更细） |
| `ideal` 点名步骤 | 93/108（86%） | 132/136（97%） |
| 干扰项点名步骤 | 305/366（83%） | 402/409（98%） |

## 4. 官方评测的复刻与基线

打分逻辑同 LitQA2（§5.5）：prompt `Q: ... \n\nOptions:\nA) ...\n\nAnswer:`、解析 `([A-Z])\)`、
1.0/0.1/0.0、格式修复与 `score_strict`、网关失败留未判定。
ProtocolQA 的唯一差别是**协议前置**：官方 harness 里就是
`input.question = self.protocol + input.question`（`labbench/ProtocolQA/task.py`），
`solve_labbench.build_prompt` 复刻这一拼接。

**官方 108 题的同模型基线**（本次实测，6 路切片并发，与自产题完全同口径）：

| solver | 官方分 | strict | 答对 | 认输 | 格式修复 |
|---|---|---|---|---|---|
| MiniMax-M3 | **0.425** | 0.340 | 45/108 | 9/108 | 18 |
| deepseek-v4-flash | **0.850** | 0.841 | 91/107 | 0 | 1 |

（deepseek 有 1 题因网关故障留作未判定，故 107。）

对照官方 LitQA2 采样 50 题的同模型基线（M3 0.294 / deepseek 0.460）：**ProtocolQA 明显更容易**
（协议在上下文里，考的是阅读+推理而非回忆），且两模型分差被放大——M3 0.425 vs deepseek 0.850。
因此自产题的难度校准必须**按 solver 分别对比**官方原题分数。

## 5. 候选池与拒绝采样（3 篇 × 8–10 块 × 3 轮 = 138 候选 → 导出 136 题）

池子整体**太容易**，与 LitQA2 自包含题当年的情形一样：

| 题集 | 题数 | MiniMax-M3 | deepseek-v4-flash |
|---|---|---|---|
| 候选池全集 | 136 | 0.907（123 对） | 0.926（124/134 对） |
| `_official`（面板均分 ∈ [0.4, 0.6]） | 13 | 0.392 | 0.623 |
| `_filtered`（任一 solver 没答对） | 18 | 0.294 | 0.450 |
| `_m3only`（仅按 M3 失败筛） | 10 | 0.000 | 0.700 |
| `_dsonly`（仅按 deepseek 失败筛） | 9 | 0.467 | 0.000 |
| `_hard`（两个都答错） | 5 | 0.040 | 0.000 |
| **参照：官方 ProtocolQA 原题** | 108 | **0.425** | **0.850** |
| 参照：官方 LitQA2 采样 50 题 | 50 | 0.294 | 0.460 |

逐题评分结构（134 题已判定）：116 题两个模型都答对、7 题只有 deepseek 答对、4 题只有 M3 答对、
3 题都答错、4 题为"认输"混合——**难度分布整体左移**（偏易）。

### 5.1 上表是"挑选那一遍"的分数——复核后不成立（重要）

按 skill 第 6 步对两个子集**重跑一遍**（题目与选项逐字段核对过与池内完全一致，
sidecar 的字母顺序也一致），结果与挑选时的分数差得很远：

| 题集 | 题数 | solver | 挑选那一遍 | 重跑那一遍 | 同一题字母一致率 |
|---|---|---|---|---|---|
| `_official` | 13 | MiniMax-M3 | 0.392 | **0.769** | **5/13（38%）** |
| `_official` | 13 | deepseek-v4-flash | 0.623 | 0.692 | 12/13（92%） |
| `_filtered` | 18 | MiniMax-M3 | 0.294 | **0.728** | **7/18（39%）** |
| `_filtered` | 18 | deepseek-v4-flash | 0.450 | 0.611 | 15/18（83%） |

即：**MiniMax-M3 在这是同一个题上两次作答有 60% 的概率换字母**（`C→B`、`A→B`、`E→A`…），
按它单次作答筛出来的"难题"大半是噪声；deepseek 稳定（一致率 83–92%）。

**对照实验：官方原题上 M3 也一样不稳。** 把官方 `ProtocolQA_full.json` 复制成
`ProtocolQA_full_retest.json`（题目与 sidecar 逐字段核对一致）重跑一遍全新作答：

| 题集 | solver | 第一遍 | 第二遍 | 逐题字母一致率 |
|---|---|---|---|---|
| **官方 108 题** | MiniMax-M3 | 0.425（108/108） | **0.449**（108/108） | **38/108（35%）** |
| **官方 108 题** | deepseek-v4-flash | 0.853（102 题可比） | **0.833**（同 102 题） | **94/102（92%）** |
| 我们的 `_official` 13 题 | MiniMax-M3 | 0.392 | 0.769 | 5/13（38%） |
| 我们的 `_filtered` 18 题 | MiniMax-M3 | 0.294 | 0.728 | 7/18（39%） |

（deepseek 有 6 题两遍都没拿全——网关挂起、留作未判定，所以按两遍都有的 102 题比对。）

这条对照定死了两件事：

1. **"逐题不稳"是 M3 在 ProtocolQA 上的固有特性**（官方 35% vs 我们 38–39%，几乎相同），
   不是自产题的毛病——自产题与官方题在这一点上可比。
2. **M3 是"总分稳、每题不稳"**：官方 108 题 0.425 → 0.449（差 0.024）、deepseek 0.853 → 0.833
   （差 0.020），两者聚合分都可复现；但 M3 有 65% 的题换了字母（deepseek 只有 8%）。所以
   **M3 的聚合分数可用，M3 的单题判定不能用来选题**——用它的单遍失败去筛等于抽它的噪声尾巴，
   子集重测必然向池子均值回归（13 题从 0.392 弹回 0.769 正是这个效应，而不是"我们题变简单了"）。

因此本报告的口径改为：

- **用稳定的 solver（deepseek）做筛选与难度声明**；若必须用 M3，要**重复多遍取共识**
  （例如"3 遍里至少 2 遍答错"），不能只看一遍。
- 按 deepseek 的重跑分数看：`_filtered` 0.611、`_official` 0.692，都**低于官方 ProtocolQA 的
  0.850**——筛出来的子集确实比官方题更难，但差距不像"挑选那一遍"显示的那么夸张。
- 任何 n<20 的子集都要重复测量后才能报数；`_hard` 5 题（M3 0.040）只能当"硬核集"候选。

### 5.2 为什么自产池比官方 ProtocolQA 容易（结构性原因）

1. **协议更短**：我们按论文小节切块，块长 850–5803 字符（中位约 2.5k）；官方 108 个 protocol
   是 569–14188 字符（中位 4157、中位 29 步）。协议越短，"哪一步有问题"越好定位。
2. **答案必须由原文支撑**（本链路的防编造硬规则，见 §2）：官方题的 `ideal` 常常需要协议外
   的领域判断（例如"把转染混合液**逐滴均匀**加入孔中"并不写在步骤里），我们的规则要求补救
   措施能被块内文本 license，等于把"考领域直觉"降级成"考文本推理"——**这是为公平性做的取舍，
   代价就是难度下降**。

下一轮提难度的两条杠杆（本轮未做）：把小块合并成长协议（对齐官方中位 4k+ 字符）、在保公平的
前提下允许部分题目依赖原文未明写但领域内公认的判断。

**复现筛选命令**：

```bash
$PY solve_labbench.py data/labbench/protocolqa_pool.json \
    --solver MiniMax-M3,deepseek-v4-flash --drop-all-correct --judge-only --dedupe-similarity 0.75
$PY solve_labbench.py data/labbench/protocolqa_pool.json \
    --solver MiniMax-M3,deepseek-v4-flash --keep-band 0.4 0.6 --band-suffix official \
    --judge-only --dedupe-similarity 0.75
$PY solve_labbench.py data/labbench/protocolqa_pool.json \
    --solver MiniMax-M3,deepseek-v4-flash --keep-hard --judge-only --dedupe-similarity 0.75

# 复核（必做）：换一个 bench 名重跑，比较逐题字母一致率
$PY solve_labbench.py data/labbench/protocolqa_pool_filtered.json --solver MiniMax-M3,deepseek-v4-flash
```

（`_filtered`/`_hard` 各有一题因网关故障未判定而排除——"缺答案不等于难"，这是脚本的既定护栏。）

## 6. 官方准入标准的对照

LAB-Bench 论文（arXiv:2407.10362v2）对 ProtocolQA 同样没有公布数字阈值，四条定性标准是：

1. 论文在 36 个月内；
2. 答案需要正文（不是摘要）信息；
3. 必须涉及推理，不能只是检索；
4. 干扰项可信。

本链路的落法：条件 3/4 做成出题 prompt 的硬规则（`ideal` 必须由块内细节支撑、干扰项三种不同
失败模式且不得同样解决问题、题干必须有一个起决定作用的步骤细节）；条件 2 由"协议原文进题面"
天然满足；条件 1 由选定的三篇论文满足。字段级检查见
`validate_question_pack.py --qtype protocolqa`（含 `--bench` 的步骤引用检查）。

## 7. 踩到的坑（都已修）

| 现象 | 原因 / 处理 |
|---|---|
| Cu 的 Troubleshooting 表整张丢失 | 该表最后一列叫 `Possible solution`，而查列用 `startswith("solution")` 匹配不到 → 行被静默丢弃。改为按前缀列表匹配，并把"问题/解法为空"的行计数报出而不是丢弃 |
| VINE-seq 32 行只识别出 14 行 | 论文用**合并单元格**：一个故障多个原因，后续行的 Problem 列为空。改为"Problem 为空的行接到上一行"，合并后 14 个故障条目、解法文本更全 |
| 协议文本混进图注/坐标轴/表格列 | Nature Protocols 的图注、图内标签（`cThin layer`）、表格列（7.0pt）与正文在 PDF 里交错。改为块级过滤（图注块、纯短行标签块、明显小于正文的块）+ 行级过滤（`Fig./Table N \|`、面板字母、裸数字） |
| 标题识别漏掉一半小节 | 子标题与正文**同字号**（9.75 vs 9.0，浮点比较还差 0.05）→ 加 semibold 规则；随后发现 callout（▲ CRITICAL STEP）与 `Option C:` 也是 semibold，被误当标题把 procedure 切碎 → 二者列入排除名单 |
| `9.0 + 0.8 >= 9.8` 判假 | 浮点：`9.0+0.8 = 9.800000000000001`。改用 epsilon 比较 |
| Cu 的 Procedure 起点定到 p9 | Introduction 里列了四个 procedure 的标题（目录式列表），最早的匹配胜出。改为候选必须有"紧接着的步骤 1"+ 至少 10 步，且优先 `Procedure N:` 形式 |
| 补救措施引用块外步骤 | 切块后正文可能提到留在块外的步骤（"as prepared in step 14"），模型据此写 "in step 14 …"，而答题者看不到该步 → 题目**不可答**。导出器逐题校验并跳过（计 `cites-step-outside-the-protocol`） |
| 模型返回空 `questions` 数组被判为解析失败 | prompt 允许"块内没有站得住的故障-解法对"时返回空数组，但校验函数把所有空数组当解析失败并重试 3 次后中止整篇。给 protocolqa 加 `allow_empty` 通道：空数组是合法结果，记录"该块无题"继续跑 |
| 同一块的改写题一起活到最终题包 | 多轮候选池里同一症状的改写会被难度筛同时留下 → `solve_labbench.py --dedupe-similarity X` 在写子集时去掉近似题干（默认关闭，ProtocolQA 建议 0.75） |

## 8. 产物清单

题包归档在 `creating_labbench/`（本地、不进 git）：

| 位置 | 内容 |
|---|---|
| `benches/protocolqa_pool.json` (+ `_eval.json`) | 候选池全集 136 题（官方 8 键信封 + 字母 sidecar） |
| `benches/protocolqa_pool_filtered.json` | 18 题（任一 solver 没答对） |
| `benches/protocolqa_pool_official.json` | 13 题（面板均分 ∈ [0.4, 0.6]） |
| `benches/protocolqa_pool_hard.json` | 5 题（都答错） |
| `benches/protocolqa_pool_m3only.json` / `_dsonly.json` | 10 / 9 题（单模型失败口径） |
| `scoring/labbench_solved_protocolqa_pool_<model>.json` | 池子逐题作答与判分（含 raw 回复、字母、strict/修复后分数） |
| `scoring/labbench_solved_ProtocolQA_full_<model>.json` | 官方 108 题的同模型基线作答 |
| `generated_protocolqa/<Paper>-r<轮>/` | 9 个候选池运行目录：`protocol.json`（抽出的协议块 + Troubleshooting 行）、`protocolqa_questions.json`（含 rubric 与 doi/步骤区间溯源）、扁平 `_eval.json` |

工作副本（`molintbench/protocal-0923/`，同样不进 git）：每篇一个目录含 `paper.pdf`、
`protocol.json`、`procedure_clean.txt`（清洗后的 Procedure 全文，便于人工核对抽取质量），
以及 `pool/<Paper>-r1..r3/` 三个轮次的生成件与日志。

仓库内的代码与文档：`extract_protocol.py`、`protocol_to_questions.py`、
`prompts/insight2question_rubric_prompts.py::_PROTOCOLQA_TEMPLATE`、
`export_labbench.py --type protocolqa`、`solve_labbench.py`（protocol 前置 + `--dedupe-similarity`）、
`validate_question_pack.py --qtype protocolqa`、`PIPELINE.md §5.8`、skill 的 ProtocolQA 附录。

## 9. 复现

```bash
cd /Users/mac/Documents/hunpo_work/HeurekaBench/scheurekabench/benchmark_creation
PY=/Users/mac/Documents/hunpo_work/HeurekaBench/.venv/bin/python
BASE=/Users/mac/Documents/hunpo_work/HeurekaBench/molintbench/protocal-0923

# 1) 抽协议（三篇，DOI 显式给出——工作副本叫 paper.pdf 时文件名里没有 DOI）
$PY extract_protocol.py --pdf $BASE/vine-seq/paper.pdf --out $BASE/vine-seq \
    --paper-id vine-seq --doi https://doi.org/10.1038/s41596-026-01434-x --dump-text
# 2) 候选池：每块 2 题 × 3 轮（各自独立目录）
for r in 1 2 3; do
  mkdir -p $BASE/pool/vine-seq-r$r && cp $BASE/vine-seq/protocol.json $BASE/pool/vine-seq-r$r/
  $PY protocol_to_questions.py --protocol_json_path $BASE/pool/vine-seq-r$r/protocol.json \
      --model_call gateway --per-block 2
done
# 3) 导出 + 两层自检
$PY export_labbench.py --type protocolqa --base_dir $BASE/pool/*/ \
    --out data/labbench/protocolqa_pool.json --check --check-style
# 4) 官方基线（官方 108 题，同模型同口径）
$PY solve_labbench.py data/labbench/ProtocolQA_full.json --synth-sidecar --sample 1  # 生成 sidecar
$PY shard_labbench.py --bench data/labbench/ProtocolQA_full.json --shards 6 --split
for s in 0 1 2 3 4 5; do
  GATEWAY_TIMEOUT=90 $PY solve_labbench.py data/labbench/shard/ProtocolQA_full_s$s.json \
      --solver MiniMax-M3 &
done; wait
$PY shard_labbench.py --bench data/labbench/ProtocolQA_full.json --shards 6 --merge --models MiniMax-M3
# 5) 池子评测 + 三档筛选（见 §5 命令）
```
