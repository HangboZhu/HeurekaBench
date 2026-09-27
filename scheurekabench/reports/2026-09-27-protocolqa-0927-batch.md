# ProtocolQA 批次 0927 生产报告

日期：2026-09-27
对象：`molintbench/protiocal-0927-1/`（目录名拼写如此，非 `protocal`）下三篇 Nature Protocols 论文的
Procedure（放射余辉纳米探针 / MOF 重金属封存与稀土回收 / 冷冻肿瘤组织集中化处理）→ 抽协议块 →
出排障题 → 导出 futurehouse/lab-bench ProtocolQA 格式 → 官方评测打分 → 拒绝采样校准难度

链路与 0923/0926/0926b 完全一致（`extract_protocol.py` → `protocol_to_questions.py` →
`export_labbench.py --type protocolqa` → `solve_labbench.py`），**抽取器零改动**（本批两处新结构
不需要改代码，理由见 §2）。所有产出未提交。

同日另有一批（`molintbench/protocal-0927-2/`，标签 **0927b**）由另一个会话并发生产、与本批共用网关与
`data/labbench/shard/` 目录；两批的题包/缓存文件名不重叠（`*_0927` vs `*_0927b`），互不污染。

## 0. 结论摘要

- 三篇共 **19 个可出题协议块**（放射余辉 10 / MOF 5 / 冷冻肿瘤 4）→ **114 个候选**（每块 2 题 × 3 轮）
  → 导出 **110 题**（导出器跳过 4：全部是"补救措施引用块外步骤"的不可答题）。
- 信封与条目形态两层自检（`--check` / `--check-style`）**均通过**；`ideal` 点名步骤率 100%
  （官方 86%），干扰项点名步骤率 100%（官方 83%）。
- 池子 110 题全判定：MiniMax-M3 **0.802**（strict 0.774）、deepseek-v4-flash **0.836**（strict 0.827）。
- 同日官方 108 题重测基线：MiniMax-M3 **0.384**（strict 0.301）、deepseek-v4-flash **0.843**（strict 0.843）。
- **难度落位**：deepseek 口径下池子与官方原题**基本同档**（0.836 vs 0.843，差 0.007，这是四批以来第二次
  做到"不筛即接近官方档位"）；M3 口径下池子仍明显偏易（0.802 vs 0.384）——该模型今日在官方原题上
  只拿 0.384，而它在本池**最难的**子集上也有 0.500，因此本批没有能落进 M3 档位的子集；M3 继续按
  "聚合分参考、不用于选题"处理（§6）。
- **分篇差异是本批最大的结构特征**：放射余辉篇（协议块短、参数密集）最难（M3 0.693 / deepseek 0.737），
  MOF 篇居中（0.900 / 0.933），冷冻肿瘤篇最易（0.955 / 1.000，deepseek 满分）。
- 四档子集（规模与复核重跑分见 §6）：`_dsonly` **17**、`_filtered` **28**、`_official` **18**、
  `_hard` **10**；1 题因网关缺口记 UNJUDGED，已排除在子集外。复核重跑（deepseek 口径）给出清晰梯度：
  `_official` **0.667** > `_filtered` **0.536** > `_dsonly` **0.118** ≈ `_hard` **0.100**，
  对照同日官方 0.843，`_official` 已比官方略难，后两档远难于官方。M3 逐题一致率 30–47%，
  再次确认其单题判定是噪声（不用来选题）。
- 抽取端两处新结构（不违法、不需改代码）：MOF 篇 Procedure 1 的 Part 1/Part 2 被"小块并入再超限切开"
  机制合并后切开，Part 1 那个块的标题串接了 Part 2 的名字（§2.1）；冷冻肿瘤篇 7 个分节是选项式
  procedure（无步骤号）不出题，另有 1 个 282 字符的调度块低于官方 protocol 下限被显式排除（§2.2）。

## 1. 协议抽取（`extract_protocol.py`）

| 论文 | DOI | Procedure 页 | 步数 | 块数（可出题） | 可出题块字符区间 | Troubleshooting |
|---|---|---|---|---|---|---|
| 放射余辉纳米探针（`radioafterglow-nanoprobes`） | `10.1038/s41596-026-01421-2` | 7–14 | 125 | 10（10） | 810–2815 | 7 行 → 6 条（1 条缺 solution） |
| MOF 重金属封存与稀土回收（`mof-metal-recovery`） | `10.1038/s41596-026-01425-y` | 11–27 | 21 | 6（5） | 1379–13459 | 22 行 → 18 条 → 映射 9、缺 solution 8、无步号 1 |
| 冷冻肿瘤组织集中化处理（`frozen-tumor-genomics`） | `10.1038/s41596-026-01426-x` | 13–21 | 45 | 12（4） | 1464–2905 | 35 行 → 15 条（全映射） |

- **放射余辉篇**结构最规整：单个 `Procedure` 大节 + 14 个子标题，步骤 1–125 连续无断层，
  10 个块全部可出题，排障表 7 行 6 条有效（1 条缺 solution）。
- **MOF 篇**是"多段 Procedure 各自重新编号"（Procedure 1–4：步骤 1、2、1、1–8、1–10），
  按原文保留、不重编号（坑表第 4 类合法结构）。Procedure 2 的正文整体落在未编号的 Block4
  （20557 字符，超官方上限 14188，任何情况下都不可导出），该 Procedure 因此只有调度步 Block3 参与出题。
- **冷冻肿瘤篇**是"Stage 1–5 + 选项式分节"结构：5 个 numbered 块（Stage 1 步骤 1–12、Stage 2 13–17、
  Stage 3 18–28、Stage 4 29–44、Stage 5 的调度步 45），另有 7 个 `(i)(ii)` 子步写法的选项式分节
  标 `numbered=false`。
- 逐篇核读 `procedure_clean.txt`：无图注/表格/坐标轴污染；`CRITICAL STEP`、`TIMING`、`PAUSE POINT`、
  `TROUBLESHOOTING:` 行按原文保留；三篇的 `warnings` 只有 `numbered=false` 一类。

## 2. 抽取器零改动，以及两处新结构的判定

**本批没有改 `extract_protocol.py`**，因此未触发"改过抽取器必须回归六篇老论文"的流程（0923/0926/0926b
的 `protocol.json` 本轮未被重新生成、也不受影响）。两处新结构都按"不改代码"处理，理由如下。

### 2.1 MOF 篇：Part 1 块标题串接了 Part 2 的名字（记录，不改）

该篇 Procedure 1 只有两个巨型步骤（Part 1 materials synthesis 13,459 字符、Part 2 characterization
9,603 字符），各自都是"单步块"，于是被既有的小块合并规则（`<3 步` 并入邻块）合并、又因超过
`--max-block-chars` 在步骤边界切回两段。切回时标题只认合并后的那一个，于是：

- `Block1`（内容是 Part 1）标题 = `Procedure 1: MOF synthesis and characterization Part 2: characterization of MOFs`，
  正文首行则是 `Part 1: materials synthesis`（正确的分节名在正文里没有丢）；
- `Block2`（内容是 Part 2）标题同名 + `(cont.)`。

判定**不改代码**的依据：同一机制在 0926b 批次（`protocal-0926-2/embryo-like-cells` 的巨型 step 16）已经
产生过 `(cont.)` 块并被该批接受；任何"小块也不算小、不许并入"的放宽都会改变那篇老论文的
`protocol.json`，破坏"改动没伤老论文"的逐字段回归约束。实际影响是模板层面的（标题进 prompt 的
`Source:` 行与 canonical 溯源），出题端未被误导——本轮 Block1 的 6 条题全部围绕 materials synthesis
（HKUST-1 的合成与活化 step 1A、CA–BNMG-1 珠子的挤出与干燥 step 1E），没有一条引用 Part 2 的内容。

### 2.2 冷冻肿瘤篇：选项式分节不出题 + 一个 282 字符调度块被显式排除

- 7 个 `numbered=false` 块（Stage 1 的 H&E/nucleic acid/LCM 三个分节、Stage 5 的 A–D 四个提取分节）
  用 `(i)(ii)` 子步写作，没有可引用的阿拉伯步骤号。默认跳过（与 Cu 篇 Procedure 3、胚样篇 Block3
  同口径）：官方 108 题里 93/108 的 `ideal` 点名步骤，一个引用不到步骤号的块做不出合规条目。
- `Block8`（Stage 5 的 step 45：「Choose from the following options (A–D)…」，282 字符）**低于官方
  protocol 下限 569 字符**，任何题都不可能通过形态自检。出题端用 `--blocks Block1,Block5,Block6,Block7`
  显式排除它，因此该篇 4 个块参与出题。
- 该篇因此只有 4/12 块可出题（Stage 1 主体 + Stage 2/3/4），产出少是结构性的，不是抽取失败。

## 3. 出题（`protocol_to_questions.py`）

每块 2 题 × 3 轮（独立目录），共 9 个轮次 → **114 题**（放射余辉 60 / MOF 30 / 冷冻肿瘤 24）。

- 9 个轮次全部跑通：无解析失败、无 stray-block、无"块返回空题"。
- 题型配比（114）：`critical_step` 57、`parameter` 33、`complete_step` 8、`order` 6、`handling` 6、
  `inconsistency` 4。干扰项：112 题配 3 个、2 题配 4 个（都在官方 3–6 内）。
- 6 次"reference the source article"告警（MOF Block1/Block3、冷冻肿瘤 Block7）全部经带反馈重生成后
  收敛；对最终 114 题做禁用词全文扫描，唯一命中是 `lead box 1 h`（"铅盒 1 小时后"）里的 `box 1`
  子串误报，无真引用。
- 生成模型 = `.env` 的 `MODEL_NAME` 首项 `MiniMax-M3`（与前三批同一配置）。

## 4. 导出与两层保真（`--type protocolqa`）

`Exported 110 protocolqa entr(ies)`；跳过 4 条，全部是 `cites-step-outside-the-protocol`：
冷冻肿瘤 r1/r3 的 Block5（步骤 13–17）要点名 step 18 / step 12，放射余辉 r2 的 Block9（步骤 89–107）
要点名步骤 7/11 与 3/4——答题者看不到那些步骤，属不可答题而非难题。

| 维度 | 官方 108 题 | 本池 110 题 |
|---|---|---|
| 题干字符 | 95–479 | 150–321 |
| `ideal` 字符 | 16–220 | 88–205 |
| 干扰项个数 | 3–6 | 3–4（4 个的 2 题） |
| 协议字符 | 569–14188 | 810–13407 |
| 协议步数 | 7–69 | 1–19（低于官方：按小节切得更细；**12 条计到 1 步**、6 条 2 步，都来自 MOF 篇把整个 Part 写成单步的巨型步骤，属论文自身结构；`--check-style` 只打印不拦） |
| `ideal` 点名步骤 | 93/108（86%） | 110/110（100%） |
| 干扰项点名步骤 | 305/366（83%） | 332/332（100%） |

信封 8 键、键序、`indent=1` 无尾随换行与官方一致（`--check` 通过）；`subtask` 为自有值
`protocolqa-v1-heureka`、`canary` 为自有 GUID（与官方及前三批一样，每份文件一个共享 GUID）。
110 题共用 **19 个互不相同的协议块**（= 参与出题的块数），**唯一协议正文合计 58,303 字符**
（池内 110 题合计 342,086 字符，同一块被 3 轮 × 2 题复用）。
sidecar 校验：答案字母分布 A 25 / B 35 / C 32 / D 18，每个条目的 options 集合恒等于
`{ideal} ∪ distractors`、`options[answer] == ideal`，110/110 无一违反。

字段级校验（`validate_question_pack.py --qtype protocolqa`）：9 个轮次的 canonical/eval 全部 PASS
（同轮题干相似度 WARN 属预期，导出侧有 `--dedupe-similarity 0.75` 兜底）；对导出后的 110 题做
**步骤引用硬检查 0 违规**（`validate_question_pack.py <bench> --bench <bench>` 只读 cites step 行；
同文件当 pack 会报字段级 HARD，那是信封键 ≠ canonical 键，属预期）。

**题干近重复画像**（供后续批次参考）：110 题两两相似度 ≥0.4 的 484 对，其中 ≥0.7 的 7 对、最高 0.831
（2 对越过 0.75）——都是同一块在 3 个轮次里被重复问到的同一症状（如"旋转蒸发后膜不均匀"、
"OCT 块出现裂纹"），这正是池模式的设计（每轮是独立候选）；本轮四档子集写出时 `--dedupe-similarity
0.75` **0 命中**（那 2 对没有同时存活进同一档）。若下游要直接使用**池全量**而非子集，建议先按 0.75
去重一次。

## 5. 官方评测打分

复刻口径同前三批（协议前置 + 官方 prompt/解析/判分，1.0/0.1/0.0），6 路切片并发。

### 5.1 同日官方基线（`ProtocolQA_full_0927.json`，108 题）

| solver | 判定数 | mean（修复后） | strict | 对/认输/格式修复 |
|---|---|---|---|---|
| MiniMax-M3 | 108/108 | **0.384** | 0.301 | 41 / 5 / 13（其中 9 条改判） |
| deepseek-v4-flash | 108/108 | **0.843** | 0.843 | 91 / 0 / 0 |

M3 的官方基线继续在批次间漂移（0923 0.425 / 0926 0.447 / 0926b 0.359 / 本批 0.384），deepseek 稳定
（0.850 / 0.852 / 0.830 / 0.843）。**难度对照继续以 deepseek 为锚**。

### 5.2 候选池全集（110 题）

| solver | 判定数 | mean | strict | 对/认输/格式修复 |
|---|---|---|---|---|
| MiniMax-M3 | 110/110 | **0.802** | 0.774 | 88 / 2 / 5（其中 3 条改判） |
| deepseek-v4-flash | 110/110 | **0.836** | 0.827 | 92 / 0 / 1 |

分篇（按题归属、各 solver 自己的分数）：

| 篇 | 题数 | MiniMax-M3 | deepseek-v4-flash |
|---|---|---|---|
| 放射余辉纳米探针 | 58 | **0.693** | **0.737** |
| MOF 重金属回收 | 30 | 0.900 | 0.933 |
| 冷冻肿瘤基因组学 | 22 | 0.955 | 1.000 |

放射余辉篇贡献了池子里几乎全部难度（该篇协议块多为 0.8–2.8k 字符的定量步骤，题型以
`parameter`/`critical_step` 为主）；后两篇偏易，尤其冷冻肿瘤篇对 deepseek 满分。

### 5.3 网关降级与处理

本批评测段全程撞上网关 503 风暴（`Service temporarily unavailable`，deepseek 路由为主），
且与并发的 0927b 批次共用同一网关与 `shard/` 目录（`shard_labbench.py` 会因 manifest 属于另一批
而重建本批 bounds，已按它的提示正常合并）。处理口径不变：**空回复不计分、不入缓存**，
6 路切片 + `fill_gaps.py`（跳过该模型必死的 anthropic 路由，`GATEWAY_TIMEOUT=40`）反复补跑，
五处缺口（池 1 题、基线 4 题）全部补齐，最终两套缓存 **110/110 与 108/108 无 UNJUDGED**。

## 6. 候选池与拒绝采样（110 候选 → 四档子集）

筛选命令全部带 `--judge-only --dedupe-similarity 0.75`（dedupe 本批 0 命中）。四档规模：
`_dsonly` **17**、`_filtered` **28**、`_official` **18**、`_hard` **10**；1 题（1-based #105，即
deepseek 补跑前缺答的那题）在筛选时被标 UNJUDGED 并自动排除。

### 复核重跑（挑选遍 vs 重跑遍）

| 题集 | 题数 | solver | 挑选那遍 | **重跑那遍** | 逐题字母一致率 |
|---|---|---|---|---|---|
| `_dsonly`（只按 deepseek 失败筛） | 17 | MiniMax-M3 | 0.418 | **0.594** | 8/17（47%） |
| | | deepseek-v4-flash | 0.000 | **0.118** | 14/17（82%） |
| `_filtered`（任一没答对） | 28 | MiniMax-M3 | 0.257 | **0.579** | 9/28（32%） |
| | | deepseek-v4-flash | 0.393 | **0.536** | 23/28（82%） |
| `_official`（面板均分 ∈ [0.4, 0.6]） | 18 | MiniMax-M3 | 0.394 | **0.500** | 6/18（33%） |
| | | deepseek-v4-flash | 0.611 | **0.667** | 16/18（89%） |
| `_hard`（两家都答错） | 10 | MiniMax-M3 | 0.010 | **0.500** | 3/10（30%） |
| | | deepseek-v4-flash | 0.000 | **0.100** | 9/10（90%） |

两个模型的四档重跑遍全部 `n = 题数`（网关缺口已用 `fill_gaps.py` 补齐，无 UNJUDGED）。

**读法**：

- **M3 单题判定依旧是噪声**（第四次复现）：逐题字母一致率 30–47%（与它在官方原题上的 35–39% 同量级）；
  按它单遍失败筛出的窗口在重跑遍向池均值回归——`_hard` 挑选遍 0.010 → 重跑遍 0.500、
  `_filtered` 0.257 → 0.579。**M3 只能看聚合分、不能用来选题**。
- **deepseek 稳定**：一致率 82–90%；`_dsonly`/`_hard` 这两个"按它答错筛出来的窗口"里一致率略低
  （82%、90%），属正常（选的就是它的困难区）。
- deepseek 口径的四档分数（重跑遍）与同日官方 0.843 拉开清晰梯度：
  `_official` 0.667 > `_filtered` 0.536 > `_dsonly` 0.118 ≈ `_hard` 0.100。

### 与官方基线的对照（同模型、各自比较）

| 口径 | deepseek（重跑遍） | vs 官方 0.843 | MiniMax-M3（重跑遍） | vs 官方 0.384 |
|---|---|---|---|---|
| **池全量 110** | 0.836 | **同档（-0.007）** | 0.802 | 偏易 |
| `_official` 18 | 0.667 | 略难（-0.18） | 0.500 | 偏易（最接近官方档位） |
| `_filtered` 28 | 0.536 | 难得多 | 0.579 | 偏易 |
| `_dsonly` 17 | 0.118 | 远难于官方 | 0.594 | 偏易 |
| `_hard` 10 | 0.100 | 远难于官方 | 0.500 | 偏易 |

**本批最重要的一条**：**池子全量本身即落在 deepseek 的官方档位**（0.836 vs 0.843）——四批里第二次
（0926b 之后）。对 M3 仍偏易，且不存在"能落到 M3 官方档位"的子集：该模型今日在官方题上只有 0.384，
而它在本池最难的子集上也高于此。**难度声明按 solver 分开写**，结论以 deepseek 口径为准。

## 7. 与 0923/0926/0926b 对照

| 维度 | 0923 | 0926 | 0926b | **0927（本批）** |
|---|---|---|---|---|
| 论文 / 可出题块 | 3 / 26 | 3 / 24 | 3 / 33 | 3 / **19** |
| 候选 → 导出 | 138 → 136 | 144 → 144 | 196 → 186 | 114 → **110** |
| 池分 MiniMax-M3 | 0.907 | 0.854 | 0.734 | **0.802** |
| 池分 deepseek | 0.926 | 0.910 | 0.794 | **0.836** |
| 官方基线同日（M3 / deepseek） | 0.425 / 0.850 | 0.447 / 0.852 | 0.359 / 0.830 | **0.384 / 0.843** |
| 抽取器改动 | 首版 | BOX 含步骤后移 | 裸 `Procedure N`、无编号 BOX、排障表分隔行 | **零改动**（两处结构按记录处理） |

本批是四批里产量最小的一批（19 块），两个结构性原因：MOF 篇的正文集中在未编号的分节里、
冷冻肿瘤篇三分之二的块是选项式分节。难度上回到 0926/0926b 之间：deepseek 口径与官方同档，
M3 口径偏易。

## 8. 产物清单

题包在 `data/labbench/`（按 AGENTS.md 不进 git）：

| 位置 | 内容 |
|---|---|
| `protocolqa_pool_0927.json` (+ `_eval.json`) | 候选池全集 110 题（官方 8 键信封 + 字母 sidecar） |
| `protocolqa_pool_0927_{dsonly,filtered,official,hard}.json` (+ `_eval`) | 四档难度子集：17 / 28 / 18 / 10 题 |
| `protocolqa_pool_0927_*_retest.json` (+ `_eval`) | 复核重跑副本（缓存分家） |
| `ProtocolQA_full_0927.json` (+ `_eval`) | 同日官方基线重测副本（108 题） |
| `labbench_solved_protocolqa_pool_0927_<model>.json`、`labbench_solved_ProtocolQA_full_0927_<model>.json` | 逐题作答与判分缓存（均 100% 覆盖） |
| `shard/protocolqa_pool_0927_s*.json`、`shard/ProtocolQA_full_0927_s*.json` | 6 路切片与切片缓存 |

工作副本（`molintbench/protiocal-0927-1/`，不进 git）：每篇 `paper.pdf` + `protocol.json` +
`procedure_clean.txt` + `extract.log`；`pool/<slug>-r1..r3/` 9 个轮次的生成件与 `gen.log`
（`frozen-tumor-genomics-r3/gen.log` 被第一轮失败的 argparse 尝试覆盖，该轮统计已从 JSON 重算）。

报告：本文件。**本批自身未改动任何仓库代码**（抽取器、出题器、导出器、评测脚本、skill 脚本均按现状
使用）；工作区里 `shard_labbench.py` 的"切片缓存合并串号"修复来自同日 0927b 批次（本批的合并正是靠它
才在两批共用 `shard/` 目录的情况下正常完成），改动未提交。

## 9. 复现

```bash
cd /Users/mac/Documents/hunpo_work/HeurekaBench/scheurekabench/benchmark_creation
PY=/Users/mac/Documents/hunpo_work/HeurekaBench/.venv/bin/python
BASE=/Users/mac/Documents/hunpo_work/HeurekaBench/molintbench/protiocal-0927-1
REPO=/Users/mac/Documents/hunpo_work/HeurekaBench
mkdir -p $BASE/radioafterglow-nanoprobes $BASE/mof-metal-recovery $BASE/frozen-tumor-genomics
cp $BASE/s41596-026-01421-2.pdf $BASE/radioafterglow-nanoprobes/paper.pdf
cp $BASE/s41596-026-01425-y.pdf $BASE/mof-metal-recovery/paper.pdf
cp $BASE/s41596-026-01426-x.pdf $BASE/frozen-tumor-genomics/paper.pdf

# 1) 抽协议（工作副本叫 paper.pdf，DOI 必须显式给）
$PY extract_protocol.py --pdf $BASE/radioafterglow-nanoprobes/paper.pdf \
    --out $BASE/radioafterglow-nanoprobes --paper-id radioafterglow-nanoprobes \
    --doi https://doi.org/10.1038/s41596-026-01421-2 --dump-text
$PY extract_protocol.py --pdf $BASE/mof-metal-recovery/paper.pdf \
    --out $BASE/mof-metal-recovery --paper-id mof-metal-recovery \
    --doi https://doi.org/10.1038/s41596-026-01425-y --dump-text
$PY extract_protocol.py --pdf $BASE/frozen-tumor-genomics/paper.pdf \
    --out $BASE/frozen-tumor-genomics --paper-id frozen-tumor-genomics \
    --doi https://doi.org/10.1038/s41596-026-01426-x --dump-text

# 2) 候选池：每块 2 题 × 3 轮（按篇 3 并发，一篇一批）
for r in 1 2 3; do for slug in radioafterglow-nanoprobes mof-metal-recovery; do
  mkdir -p $BASE/pool/$slug-r$r && cp $BASE/$slug/protocol.json $BASE/pool/$slug-r$r/
  $PY protocol_to_questions.py --protocol_json_path $BASE/pool/$slug-r$r/protocol.json \
      --model_call gateway --per-block 2 > $BASE/pool/$slug-r$r/gen.log 2>&1 &
done; wait; done
# 冷冻肿瘤篇排除 282 字符的 Block8（低于官方 protocol 下限）
for r in 1 2 3; do
  mkdir -p $BASE/pool/frozen-tumor-genomics-r$r && cp $BASE/frozen-tumor-genomics/protocol.json $BASE/pool/frozen-tumor-genomics-r$r/
  $PY protocol_to_questions.py --protocol_json_path $BASE/pool/frozen-tumor-genomics-r$r/protocol.json \
      --model_call gateway --per-block 2 --blocks Block1,Block5,Block6,Block7 \
      > $BASE/pool/frozen-tumor-genomics-r$r/gen.log 2>&1 &
done; wait

# 3) 导出 + 两层自检 + 步骤引用硬检查
$PY export_labbench.py --type protocolqa --base_dir $BASE/pool/*/ \
    --out data/labbench/protocolqa_pool_0927.json --check --check-style
$PY validate_question_pack.py data/labbench/protocolqa_pool_0927.json \
    --qtype protocolqa --bench data/labbench/protocolqa_pool_0927.json

# 4) 评测：池子 6 路切片 + 同日官方基线（串行——merge 依赖 shard/manifest.json）
cp data/labbench/ProtocolQA_full.json data/labbench/ProtocolQA_full_0927.json
cp data/labbench/ProtocolQA_full_eval.json data/labbench/ProtocolQA_full_0927_eval.json
$PY shard_labbench.py --bench data/labbench/protocolqa_pool_0927.json --shards 6 --split
for s in 0 1 2 3 4 5; do GATEWAY_TIMEOUT=90 $PY solve_labbench.py \
    data/labbench/shard/protocolqa_pool_0927_s$s.json --solver MiniMax-M3,deepseek-v4-flash & done; wait
$PY shard_labbench.py --bench data/labbench/protocolqa_pool_0927.json --shards 6 \
    --merge --models MiniMax-M3,deepseek-v4-flash
# 基线同法：--bench data/labbench/ProtocolQA_full_0927.json
# 缺口补跑（本批 5 处）：
#   GATEWAY_TIMEOUT=40 $PY $REPO/.agents/skills/protocolqa-bench-production/scripts/fill_gaps.py \
#       data/labbench/shard/<shard>.json --solver deepseek-v4-flash   # 反复跑到覆盖不再涨

# 5) 四档筛选（--judge-only 复用缓存；先跑 deepseek 单模型再改名，避免覆盖 _filtered）
B=data/labbench/protocolqa_pool_0927
$PY solve_labbench.py $B.json --solver deepseek-v4-flash --drop-all-correct --judge-only \
    --dedupe-similarity 0.75 && mv ${B}_filtered.json ${B}_dsonly.json \
    && mv ${B}_filtered_eval.json ${B}_dsonly_eval.json
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --drop-all-correct \
    --judge-only --dedupe-similarity 0.75
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --keep-hard \
    --judge-only --dedupe-similarity 0.75
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --keep-band 0.4 0.6 \
    --band-suffix official --judge-only --dedupe-similarity 0.75

# 6) 复核（必做）：子集复制成新 bench 名重跑，再用脚本对照挑选遍/重跑遍
for n in dsonly filtered official hard; do
  cp ${B}_$n.json ${B}_${n}_retest.json; cp ${B}_${n}_eval.json ${B}_${n}_retest_eval.json
  GATEWAY_TIMEOUT=90 $PY solve_labbench.py ${B}_${n}_retest.json --solver MiniMax-M3,deepseek-v4-flash &
done; wait
$PY $REPO/.agents/skills/protocolqa-bench-production/scripts/subset_retest_stats.py \
    --pool $B.json --models MiniMax-M3,deepseek-v4-flash
```

踩到的两条命令坑（本批实际花费了时间）：**zsh 不对未加引号的变量做分词**，
`--blocks $extra` 形式的可变参数会被当成一个 token（用字面量或 `${=...}`）；
**`$n_eval` 会被解析成变量 `n_eval`**（下划线是标识符字符），拼文件名一律写 `${n}_eval`。

## 10. 遗留

1. **版权**：`protocol` 字段是 Nature Protocols 正文逐字引用（19 个块、唯一正文 **58,303** 字符，
   池内 110 题合计 342,086 字符——同一块被多题复用），公开发布前必须取得授权或大幅裁短
   （同 0924 发布版注意事项）。
2. **难度对 M3 偏易、对 deepseek 已同档**：deepseek 口径下池全量 0.836 ≈ 官方 0.843，可直接用；
   M3 口径下偏易（0.802 vs 0.384）且无子集能落进该口径。若后续要以 M3 口径交付，需要更强的提难手段
   （0926 报告列的两条杠杆：把小块合并成 4k+ 字符长协议、在保公平前提下允许依赖领域公认判断）。
3. **产量结构性偏低**：19 个可出题块里，MOF 篇只有 5 块（Procedure 2 的正文在未编号分节里）、
   冷冻肿瘤篇只有 4 块（7 个选项式分节 + 1 个 282 字符调度块）。这类"选项式 procedure"论文的
   出题口径是下一批可以讨论的点：是否给 `(i)(ii)` 子步补一种"块内编号"（形如 `Step 12A(i)`），
   让官方不接受的步骤号引用变成可接受的——那是出题 prompt 与抽取器的一次联合改动，需要回归。
4. **MOF 篇 Block1 标题串接**（§2.1）：只影响标题/canonical 溯源，不影响题目；若要修，
   需要一次"放宽合并判据 + 重跑六篇老论文并为胚样篇建立新基线"的抽取器改动，本批按回归优先未做。
5. 本批评测段与并发的 0927b 批次共用网关，503 风暴明显；缺口全部靠 `fill_gaps.py` 补齐。
   两批的产物文件名不重叠，无串号。
