# ProtocolQA 第四批（0927b）生产报告

日期：2026-09-27
对象：`molintbench/protocal-0927-2/` 下三篇 Nature Protocols 论文的 Procedure（PCL 基二维血小板
的活性结晶驱动自组装、盐辅助合成亚稳 1T′-相 VIB 族过渡金属硫属化物、剪切流诱导 2D 纳米片复合膜）
→ 抽协议块 → 出排障题 → 导出 futurehouse/lab-bench ProtocolQA 格式 → 官方评测打分 → 拒绝采样校准难度

链路与前两批完全一致（`extract_protocol.py` → `protocol_to_questions.py` → `export_labbench.py
--type protocolqa` → `solve_labbench.py`）；本批三篇是**材料化学合成**方向（前三批为生物协议），
抽取器**零改动**通过。代码改动只有一处，且不在抽取器：`shard_labbench.py` 的切片缓存合并串号
（§2）。**所有改动未提交。**

## 0. 结论摘要

- 三篇共 **17 个可出题协议块**（PCL 4 / TMDC 3 / 剪切流 10）→ **102 个候选**（每块 2 题 × 3 轮）
  → **导出 102 题，导出器零跳过**（前三批都有 2–10 条被跳过）。
- 信封与条目形态两层自检（`--check` / `--check-style`）**均通过**；`ideal` 点名步骤率 102/102
  （官方 86%），干扰项点名步骤率 306/306（官方 83%）；步骤引用硬检查 0 违规。
- **池子在 deepseek 口径下已落在官方档位附近**（第二批做到这一点，上批是 0926b）：102 题全量
  MiniMax-M3 **0.866**（strict 0.797）、deepseek **0.892**（strict 0.882，102/102）。同日官方原题
  基线：M3 **0.342**（strict 0.314，108/108）、deepseek **0.868**（strict 0.868，106/108）。
  deepseek 口径 0.892 vs 官方 0.868（+0.024）、`_official` 9 题 0.778（-0.09）；**M3 口径仍偏易**
  （0.866 vs 0.342），但该基线本身在 0.34–0.45 之间漂移，只作参考。
- 四档子集（规模 / 重跑遍 deepseek）：`_dsonly` **11**（0.300，n=10）、`_filtered` **17**（0.600，
  n=15）、`_official` **9**（0.778）、`_hard` **8**（0.143，n=7）——远小于 0926b（37/60/35/26），
  因为本批 102 题里 deepseek 只错了 11 题。
- **新发现并修掉一个会静默污染分数的管线缺陷**：`shard/manifest.json` 是**全目录共享**的一份，
  同一目录里并发跑另一批（本批确实发生了：另一会话在跑 `ProtocolQA_full_0927`）会互相覆盖；
  `--merge` 按别人的 bounds 回填，逐片错位。已修为"只认归属本 bench 的 manifest，否则按本 bench
  的切片文件重建 bounds 并告警"（§2.2）。本批池子缓存已按切片缓存精确重建（§5.3）。
- 抽取质量（三篇逐篇 QA）：步骤号全覆盖无断层、无图注/表格/坐标轴污染、无块首截断；材料化学
  协议的多段 `Procedure` 各自重新编号、`BOX` 内小标题、参数表交叉引用等结构均被既有抽取器覆盖。

## 1. 协议抽取（`extract_protocol.py`，零改动）

| 论文 | DOI | Procedure 页 | 步数 | 块数（可出题） | 块字符区间 | Troubleshooting 行 → 映射 |
|---|---|---|---|---|---|---|
| PCL 二维血小板（`pcl-2d-platelets`） | `10.1038/s41596-026-01428-9` | 11–17 | 31 | 6（4） | 3281–6033（出题块） | 18 → 12（另 4 行多原因折叠、1 行不完整、1 条未映射） |
| 盐辅助 1T′-TMDC（`salt-assisted-tmdc`） | `10.1038/s41596-026-01429-8` | 8–11 | 20 | 3（3） | 951–3996 | 4 → 4 |
| 剪切流纳米片复合膜（`shear-flow-nanosheet-films`） | `10.1038/s41596-026-01442-x` | 12–17 | 91 | 10（10） | 859–6025 | 17 → 8（另 8 行多原因折叠、1 行不完整） |

- **TMDC 篇只有 3 块**（20 步）：Block2（"Wash the prepared TMD crystals"，4 步 / 951 字符）刚过
  官方下限 569 字符，是本批最小的可出题块。
- **剪切流篇是"多段 Procedure 各自从 1 重新编号"结构**（Procedure 1 的 1–23、Procedure 2 的 1–37、
  Procedure 3 的 1–31，合计 91 步），原番号一律保留、不重编号（0926b 修好的 `Procedure N` 裸标题
  识别直接复用）。
- **PCL 篇的两个未编号块**：`BOX 2`（783 字符）与 `Platelet dispersions`（498 字符）—— 后者是
  BOX 2 内部的第三个小标题，BOX 自身的小标题把它切成了两块，两块都 `numbered=false`，按口径不出题。
- 逐篇抽读 `procedure_clean.txt`：CRITICAL STEP / TIMING / TROUBLESHOOTING 行按原文保留；
  无图注、表格列、坐标轴标签污染；块首无截断（每块都以小节标题开头）。

## 2. 本批的代码改动

### 2.1 `extract_protocol.py`：零改动

材料化学协议未触发新缺陷：多段 Procedure 重新编号（0926b 修复）、BOX 后移、排障表分隔行
（0926b 修复）均已覆盖。因此**未做六篇回归**（无代码改动可回归）。

三处**按设计**、不影响出题的结构细节（记录备查，勿当 bug）：

1. 块 `title` 在小块并入邻块时按 `_join_titles` 堆叠（如 PCL Block3 = "Preparation of
   polydisperse cylinders by CDSA Preparation of seeds particles by sonication"）。这是既有设计，
   0923/0926 六篇同样如此（如 `ipsc-remote-kit` Block1 堆了三个标题）。`title` 只进 canonical
   溯源的 JSON，**不进导出信封**。
2. 剪切流篇 Procedure 3 的 AFM 小节（步骤 1–6）与其后的 TEM 小节（7–11）并成一个块：AFM 小节
   正文落在阈值内（`--min-block-chars 800`）被并入邻块，块内正文完整保留两个小节的标题行，
   答题者读到的文本没有信息缺失。
3. PCL 篇排障表有一条复合步号标签（`25 or Box 1, Step 4`）未映射到任何块：这条"补救"参考注释
   因此对出题模型不可见（只是少一条提示，不影响题目可答性）。

### 2.2 `shard_labbench.py`：修复并发批次串用切片清单（本批唯一代码改动）

**症状**：池子 102 题的 pass-2 `--merge` 报 `coverage 106–107/102` —— 主缓存条目比题库还多，
且 75 条被"replace"。

**根因**：`shard/manifest.json` 是 shard 目录里**唯一一份、所有 bench 共用**（`shard_paths` 只按
目录与 stem 命名切片文件，manifest 不带 stem）。本批运行期间，另一个会话在同一目录跑
`ProtocolQA_full_0927`（108 题、6 片 → 每片 18 题）并覆盖了 manifest；池子（102 题 → 每片 17 题）
的 merge 于是按 18 题的边界回填，**每片错位 1 个下标**，把答案写到了别的题上。切片体积恰好只差 1，
merge 原有的一致性检查（`0 <= local < b-a`）没能触发 —— 是静默污染。

**修复**：新增 `resolve_bounds()`：只当 manifest 的 `bench` 字段指向本 bench 且边界终点等于本题库
长度时才采用；否则（别人的 manifest / 陈旧 manifest）按本 bench 自己的切片文件数重建 bounds 并
打印告警。冲突不再静默。

**本批数据修复**：切片缓存（`shard/labbench_solved_<stem>_s<i>_<model>.json`）本身就是正确的
（每个 solve 进程只读自己那片的题库与 sidecar），因此按本 bench 的正确边界重新映射即可精确重建：
102/102 条（M3 与 deepseek 各 102）。重建前后的文件都留在 `data/labbench/` 备查
（`*.premerge_backup.json` 为重建前、`*.corrupt.json` 为被污染的合并结果）。

**修复的验证**（同日官方基线那次 merge）：合并结果与"按 6 片边界独立重建"**逐条相等**
（M3 108/108、deepseek 106/108，`main == recon` 为 True）；把池子的 merge 在"manifest 属于别的 bench"
的状态下调用，会打印告警并按本 bench 的切片文件重建出 `[(0,17),(17,34),…,(85,102)]`。

## 3. 出题（`protocol_to_questions.py`）

每块 2 题 × 3 轮（独立目录），共 9 个轮次、17 块 → **102 题**（PCL 24 / TMDC 18 / 剪切流 60）。

- 9 个轮次全部跑通：解析失败 0（1 次 JSON 损坏被 `extract_json` 抢修，1 次重试成功）、
  stray-block 0、`ideal-without-step-reference` **0**、干扰项个数全部为 3、无空块。
- 题型配比（102）：`critical_step` 52、`parameter` 25、`complete_step` 14、`handling` 6、
  `inconsistency` 3、`order` 2。
- 自纠：9 次"引用原文/文献"告警（涉及 10 题）触发带反馈重生成一次，最终导出题**无**一例出现
  `the paper`/`the source`/`Supplementary`/`Fig.` 之类引用（抽样复核）。
- 跨轮重复：102 条题干两两不重复（0 条在多于一个轮次中重复出现）。
- 字段级校验（`validate_question_pack.py --qtype protocolqa`）：9 个轮次全部 PASS；WARN 仅
  "同轮内题干相似度 0.40–0.60"（剪切流 20 题/轮时最多 48 对），远低于导出侧 dedupe 阈值 0.75。

## 4. 导出与两层保真（`--type protocolqa`）

`Exported 102 protocolqa entr(ies)`，**跳过 0 条**（前三批分别跳 2 / 0 / 10 条）。

| 维度 | 官方 108 题 | 本池 102 题 |
|---|---|---|
| 题干字符 | 95–479 | 168–314 |
| `ideal` 字符 | 16–220 | 101–214 |
| 干扰项个数 | 3–6 | 3（102/102） |
| 协议字符 | 569–14188 | 859–6020 |
| 协议步数 | 7–69 | 4–18（低于官方：按小节切得更细，同 0926b） |
| `ideal` 点名步骤 | 93/108（86%） | 102/102（100%） |
| 干扰项点名步骤 | 305/366（83%） | 306/306（100%） |

信封 8 键、键序、`indent=1` 无尾随换行与官方一致（`--check` 通过）；`subtask` 为自有值
`protocolqa-v1-heureka`、`canary` 为自有 GUID。102 题共用 **17 个互不相同的协议块**，唯一协议
正文合计 **42,210 字符**。字段级校验与步骤引用硬检查（`--bench` 对齐后逐题核）**0 违规**。

## 5. 官方评测打分

复刻口径同前三批（协议前置 + 官方 prompt/解析/判分，1.0/0.1/0.0），6 路切片并发。

### 5.1 同日官方基线（`ProtocolQA_full_0927b.json`，108 题）

| solver | 判定数 | mean（修复后） | strict | 对/认输/格式修复 |
|---|---|---|---|---|
| MiniMax-M3 | 108/108 | **0.342** | 0.314 | 36 / 9 / 9 |
| deepseek-v4-flash | 106/108 | **0.868** | 0.868 | 92 / 0 / 0 |

M3 的官方基线第四次重测（0.425 / 0.447 / 0.359 / **0.342**），继续支持"**难度对照以 deepseek
为锚、M3 只看聚合**"的口径。deepseek 的两次采样：前 37 题（网关先恢复的那批）0.838，补齐到
106 题后 0.868 —— 全量口径更可信（106/108，2 题十余次重试仍空回复，记 UNJUDGED）。

### 5.2 候选池全集（102 题）

| solver | 判定数 | mean | strict | 对/认输/格式修复 |
|---|---|---|---|---|
| MiniMax-M3 | 102/102 | **0.866** | 0.797 | 88 / 3 / 7 |
| deepseek-v4-flash | 102/102 | **0.892** | 0.882 | 91 / 0 / 1 |

分篇（按题归属）：PCL 24 题 M3 0.921 / deepseek 0.917；TMDC 18 题 M3 0.783 / deepseek 0.944；
剪切流 60 题 M3 0.868 / deepseek 0.867。

格式修复本批对 M3 依旧关键：7 题在官方 CI 正则下判 0，追问一次后全部答对（strict 0.797 → 0.866）；
deepseek 只有 1 题。

### 5.3 缓存重建（§2.2 的数据修复）

池子主缓存按切片缓存精确重建后为 102/102；重建前的两份文件保留
（`*.premerge_backup.json` = pass-1 主缓存，`*.corrupt.json` = 被污染合并结果）。
重建版与前次评测的差异仅来自重采样（本批的池子分数 = pass-2 采样）。

## 6. 候选池与拒绝采样（102 候选 → 四档子集）

筛选命令全部带 `--judge-only --dedupe-similarity 0.75`。四档规模：`_dsonly` **11**、
`_filtered` **17**、`_official` **9**、`_hard` **8**；deepseek 的重跑遍各有 1–2 题网关缺口，
下表按实际判定数记（**写难度必须带 n**）。

| 题集 | 题数 | solver | 挑选那遍 | 重跑那遍 | 逐题字母一致率 |
|---|---|---|---|---|---|
| `_dsonly`（只按 deepseek 失败筛） | 11 | MiniMax-M3 | 0.282 | **0.464** | 3/11（27%） |
| | | deepseek | 0.000 | **0.300** | 6/10（60%） |
| `_filtered`（任一没答对） | 17 | MiniMax-M3 | 0.194 | **0.588** | 7/17（41%） |
| | | deepseek | 0.353 | **0.600** | 11/15（73%） |
| `_official`（面板均分 ∈ [0.4,0.6]） | 9 | MiniMax-M3 | 0.356 | **0.667** | 4/9（44%） |
| | | deepseek | 0.667 | **0.778** | 8/9（89%） |
| `_hard`（两家都答错） | 8 | MiniMax-M3 | 0.013 | **0.375** | 3/8（38%） |
| | | deepseek | 0.000 | **0.143** | 5/7（71%） |

**读法**（第四次复现 M3 口径的两条规律）：

- **M3 的单题判定是噪声，只能看聚合**：逐题字母一致率 27–44%（官方原题上也是 35–39%），
  `_hard` 挑选遍 0.013 → 重跑遍 0.375、`_dsonly` 0.282 → 0.464，子集必然向池均值回归。
- **deepseek 是锚**：一致率 60–89%；`_dsonly`/`_hard` 这两个按"它答错"筛出的窗口内一致率略低
  （60–71%），属正常（选的就是它的困难区）。

**与官方基线的对照**（同模型、各自比较；官方同日基线 M3 0.342 / deepseek 0.868）：

| 口径 | deepseek（重跑遍） | vs 官方 0.868 | MiniMax-M3（重跑遍） | vs 官方 0.342 |
|---|---|---|---|---|
| **池全量 102** | 0.892 | **略易（+0.024）** | 0.866 | 偏易 |
| `_official` 9 | 0.778 | 略难（-0.09） | 0.667 | 偏易 |
| `_filtered` 17 | 0.600 | 难得多 | 0.588 | 偏易 |
| `_dsonly` 11 | 0.300 | 远难于官方 | 0.464 | 偏易 |
| `_hard` 8 | 0.143 | 远难于官方 | 0.375 | 略偏易 |

**本批的难度结论**：deepseek 口径下，**池全量（0.892）与同日官方基线（0.868）几乎同档**——
这是 0926b 之后第二批"不筛就接近官方档位"的批次；`_official` 9 题（0.778）比官方再硬一档，
`_dsonly`/`_hard` 是"稳定模型口径硬核集"。M3 口径下所有子集都仍偏易（它的官方基线今天只有
0.342，而池里最难的 `_hard` 也有 0.375），按约定**不据此下结论**。

## 7. 与 0923/0926/0926b 批次对照

| 维度 | 0923 | 0926 | 0926b | **0927b（本批）** |
|---|---|---|---|---|
| 论文 / 协议块 | 3 / 26 | 3 / 24 | 3 / 33 | 3 / 17 |
| 候选 → 导出 | 138 → 136 | 144 → 144 | 196 → 186 | 102 → **102** |
| 池分 M3 | 0.907 | 0.854 | 0.734 | 0.866 |
| 池分 deepseek | 0.926 | 0.910 | 0.794 | 0.892 |
| 官方基线同日 M3 / deepseek | 0.425 / 0.850 | 0.447 / 0.852 | 0.359 / 0.830 | 0.342 / **0.868** |
| 池分距官方（deepseek） | +0.076 | +0.058 | **−0.036（已达标）** | **+0.024（已达标）** |
| 抽取器改动 | 首版 | BOX 含步骤后移 | 裸 `Procedure N`、无编号 BOX、排障表分隔行 | **无**（三篇为材料化学协议） |

## 8. 产物清单

题包在 `data/labbench/`（框架目录，按 AGENTS.md 不进 git）：

| 位置 | 内容 |
|---|---|
| `protocolqa_pool_0927b.json` (+ `_eval.json`) | 候选池全集 102 题（官方 8 键信封 + 字母 sidecar） |
| `protocolqa_pool_0927b_{dsonly,filtered,official,hard}.json` (+ `_eval`) | 四档难度子集：11 / 17 / 9 / 8 题 |
| `protocolqa_pool_0927b_*_retest.json` (+ `_eval`) | 复核重跑副本（缓存分家） |
| `ProtocolQA_full_0927b.json` (+ `_eval`) | 同日官方基线重测副本 |
| `labbench_solved_protocolqa_pool_0927b_<model>.json` (+ `.premerge_backup` / `.corrupt`) | 逐题作答与判分缓存（重建 §5.3） |
| `shard/protocolqa_pool_0927b_s*.json`、`log_*_0927b_*.txt`、`fill_ProtocolQA_full_0927b_s*.log`、`retest_<档>_{ds,m3}.log` | 6 路切片、切片缓存、切片运行日志、基线缺口补跑日志、四档复核日志 |

工作副本（`molintbench/protocal-0927-2/`，不进 git）：每篇 `paper.pdf` + `protocol.json` +
`procedure_clean.txt`；`pool/<slug>-r1..r3/` 9 个轮次的生成件与日志；`run_gen.sh` / `run_eval.sh` /
`rebuild_cache.py`。

仓库内改动：`shard_labbench.py`（§2.2）。**未提交。**

## 9. 复现

```bash
cd /Users/mac/Documents/hunpo_work/HeurekaBench/scheurekabench/benchmark_creation
PY=/Users/mac/Documents/hunpo_work/HeurekaBench/.venv/bin/python
BASE=/Users/mac/Documents/hunpo_work/HeurekaBench/molintbench/protocal-0927-2
mkdir -p $BASE/{pcl-2d-platelets,salt-assisted-tmdc,shear-flow-nanosheet-films}
cp $BASE/s41596-026-01428-9.pdf $BASE/pcl-2d-platelets/paper.pdf
cp $BASE/s41596-026-01429-8.pdf $BASE/salt-assisted-tmdc/paper.pdf
cp $BASE/s41596-026-01442-x.pdf $BASE/shear-flow-nanosheet-films/paper.pdf

# 1) 抽协议（材料化学三篇，零改动）
$PY extract_protocol.py --pdf $BASE/pcl-2d-platelets/paper.pdf --out $BASE/pcl-2d-platelets \
    --paper-id pcl-2d-platelets --doi https://doi.org/10.1038/s41596-026-01428-9 --dump-text
$PY extract_protocol.py --pdf $BASE/salt-assisted-tmdc/paper.pdf --out $BASE/salt-assisted-tmdc \
    --paper-id salt-assisted-tmdc --doi https://doi.org/10.1038/s41596-026-01429-8 --dump-text
$PY extract_protocol.py --pdf $BASE/shear-flow-nanosheet-films/paper.pdf \
    --out $BASE/shear-flow-nanosheet-films --paper-id shear-flow-nanosheet-films \
    --doi https://doi.org/10.1038/s41596-026-01442-x --dump-text

# 2) 候选池：每块 2 题 × 3 轮（脚本见 $BASE/run_gen.sh）
$BASE/run_gen.sh

# 3) 字段校验 + 导出 + 两层自检
for d in $BASE/pool/*/; do $PY validate_question_pack.py $d/protocolqa_questions_eval.json --qtype protocolqa; done
$PY export_labbench.py --type protocolqa --base_dir $BASE/pool/*/ \
    --out data/labbench/protocolqa_pool_0927b.json --check --check-style

# 4) 评测：池子 + 同日官方基线（6 路切片；网关抖动时用 skill 的补跑脚本，比整轮重跑快得多）
cp data/labbench/ProtocolQA_full.json data/labbench/ProtocolQA_full_0927b.json
cp data/labbench/ProtocolQA_full_eval.json data/labbench/ProtocolQA_full_0927b_eval.json
SKILL=/Users/mac/Documents/hunpo_work/HeurekaBench/.agents/skills/protocolqa-bench-production
for bench in protocolqa_pool_0927b ProtocolQA_full_0927b; do
  $PY shard_labbench.py --bench data/labbench/$bench.json --shards 6 --split
  for s in 0 1 2 3 4 5; do GATEWAY_TIMEOUT=90 $PY solve_labbench.py \
      data/labbench/shard/${bench}_s$s.json --solver MiniMax-M3,deepseek-v4-flash & done; wait
  # 缺口：只补未判定的题（deepseek 的 anthropic 死路由被跳过，一批就能从 47/108 补到 105/108）
  for s in 0 1 2 3 4 5; do ( GATEWAY_TIMEOUT=40 $PY $SKILL/scripts/fill_gaps.py \
      data/labbench/shard/${bench}_s$s.json --solver deepseek-v4-flash ) & done; wait
  $PY shard_labbench.py --bench data/labbench/$bench.json --shards 6 --merge \
      --models MiniMax-M3,deepseek-v4-flash --overwrite
  $PY solve_labbench.py data/labbench/$bench.json --solver MiniMax-M3,deepseek-v4-flash --judge-only
done

# 5) 四档筛选（--judge-only 复用缓存；先跑 deepseek 单模型再改名）
B=data/labbench/protocolqa_pool_0927b
$PY solve_labbench.py $B.json --solver deepseek-v4-flash --drop-all-correct --judge-only \
    --dedupe-similarity 0.75 && mv ${B}_filtered.json ${B}_dsonly.json && \
    mv ${B}_filtered_eval.json ${B}_dsonly_eval.json
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --drop-all-correct --judge-only --dedupe-similarity 0.75
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --keep-hard --judge-only --dedupe-similarity 0.75
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --keep-band 0.4 0.6 \
    --band-suffix official --judge-only --dedupe-similarity 0.75

# 6) 复核（必做）：子集复制成新 bench 名重跑（缓存分家），再一键出对照表
for n in dsonly filtered official hard; do
  cp ${B}_$n.json ${B}_${n}_retest.json; cp ${B}_$n_eval.json ${B}_${n}_retest_eval.json
  ( GATEWAY_TIMEOUT=40 $PY $SKILL/scripts/fill_gaps.py ${B}_${n}_retest.json \
      --solver deepseek-v4-flash > /tmp/retest_${n}_ds.log 2>&1 ) &
  ( GATEWAY_TIMEOUT=90 $PY solve_labbench.py ${B}_${n}_retest.json \
      --solver MiniMax-M3 > /tmp/retest_${n}_m3.log 2>&1 ) &
done; wait
$PY $SKILL/scripts/subset_retest_stats.py --pool $B.json --models MiniMax-M3,deepseek-v4-flash
```

**并发注意**：本批与另一会话的 0927 批次在同一 `shard/` 目录并发评测，触发了 §2.2 的下标串号。
同目录并发评测前先确认没有别的 batch 在跑，或等 `shard_labbench.py` 的修复到位。

## 10. 遗留

1. **版权**：`protocol` 字段是 Nature Protocols 正文逐字引用（17 个块、唯一正文 42,210 字符，
   池内合计约 17 万字符）；公开发布前必须取得授权或大幅裁短（同 0924 发布版注意事项）。
2. **池子对 deepseek 已在官方档位**（0.892 vs 官方同日 0.868，+0.024），对 M3 仍偏易
   （0.866 vs 0.342，但该基线本身漂移 ~0.09）。这是 0926b 之后第二批"不筛即达标"的批次；
   要更硬的口径用 `_official`（0.778）与 `_dsonly`（0.300）。
3. 本批四档子集规模小（8–17 题）：102 个候选里 deepseek 只错了 11 题，`_hard` 仅 8 题。
   要把子集做到可用规模，需要更多候选（每篇更多轮次）或换更硬的论文。
4. 三篇都是材料化学合成协议，Procedure 结构比生物协议更"平"（参数表 + 定量步骤），
   可出题的"问题-补救"对更少（17 块 vs 0926b 33 块）——这是本批候选量只有一半的原因。
5. PCL 篇排障表 1 条复合步号行（`25 or Box 1, Step 4`）未映射；PCL 篇 BOX 2 被自身小标题切成
   两块（均未编号、不出题）。两者都不影响出题，记录备查。
