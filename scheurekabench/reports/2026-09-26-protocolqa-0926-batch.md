# ProtocolQA 第二批（0926）生产报告

日期：2026-09-26
对象：`molintbench/protocal-0926/` 下三篇 Nature Protocols 论文的 Procedure（抗纤维化药物筛选
平台、远程采集试剂盒→iPSC/脑类器官、Trace-n-Seq）→ 抽协议块 → 出排障题 → 导出
futurehouse/lab-bench ProtocolQA 格式 → 官方评测打分 → 拒绝采样校准难度
链路沿用 0923 批次，无新增脚本；`extract_protocol.py` 修了一处 BOX 交错抽取缺陷。

## 0. 结论摘要

- 三篇 24 个协议块 → **144 个候选**（每块 2 题 × 3 轮），字段校验全 PASS；导出 **144/144**
  （0 条因"引用块外步骤"被跳过，比 0923 批次的 2 条干净）。
- 信封与条目形态两层自检（`--check` / `--check-style`）均通过，`ideal` 点名步骤率 99%
  （官方 86%）。
- **池子整体偏易**（与 0923 批次相同的结构性现象）：池 144 题 M3 **0.854** / deepseek-v4-flash
  **0.910**；官方原题同日重测基线 M3 **0.447** / deepseek **0.852**（与 0923 旧测一致）。
- **四个难度子集（复核重跑后的分数）**：
  - `_official` 22（面板均分带 [0.4, 0.6]）：deepseek **0.773** ≈ 官方 0.850（略难），M3 0.818 偏易；
  - `_dsonly` 13（**稳定模型失败口径**，0924 报告"注意事项 2"的改法）：deepseek 0.231 / M3 0.308
    —— 两个模型上**都比官方难**；
  - `_filtered` 28（任一 solver 没答对）：0.750 / 0.607；
  - `_hard` 6（两家都答错）：0.167 / 0.000。
- **M3 逐题噪声再次重现**：子集上两次作答字母一致率只有 29–36%（deepseek 89–91%）——
  难度声明继续以 deepseek 口径为准（细节见 §6）。
- 新踩坑一个并已修：**BOX 插在主流程中间**（ipsc 篇 BOX 1 夹在 Step 32/33 之间），修复后
  0923 三篇回归输出逐字段一致。

## 1. 协议抽取（`extract_protocol.py`）

| 论文 | DOI 尾号 | Procedure 页 | 步数 | 块数 | 块字符区间 | Troubleshooting 行 → 映射条目 |
|---|---|---|---|---|---|---|
| 抗纤维化药物筛选平台 | `s41596-026-01445-8` | 10–16 | 64 | 7 | 918–4905 | 12 → 12，未映射 0 |
| 远程采集 → iPSC/脑类器官 | `s41596-026-01440-z` | 11–20 | 108（96 主 + BOX 1 的 12） | 8（含 BOX 1） | 1129–9215 | 22 → 9，未映射 0 |
| Trace-n-Seq | `s41596-026-01416-z` | 13–19 | 58 | 9 | 1104–3002 | 15 → 6，未映射 0 |

- 三篇全部编号（无 `numbered=false` 块），三篇 `extraction.warnings` 均为空。
- 行→条目数下降（22→9、15→6）是"合并单元格"折叠：一个故障多个原因时后续行 Problem 列为空，
  接到上一条（0923 已修的行为），不是丢失。
- 逐篇抽读 `procedure_clean.txt`：无图注/表格/坐标轴污染；CRITICAL STEP、TIMING 行按原文保留。

## 2. 抽取器修复：BOX 夹在主流程中间

ipsc 篇的 **BOX 1（Thawing）自带 1–12 重新编号，浮动在主流程 Step 32 与 33 之间**，主流程
随后**没有新标题**地续写。修复前的两个后果：

1. **块内容错位**：切块时 BOX 开新块、主流程尾段（33–37）被并进盒子块——协议文本从盒内
   `12.` 直接跳到主流程 `33.`，答题者读到的是两份不相干的步骤拼接；主流程小节
   `Keratinocyte to iPS cell reprogramming` 只剩 22–32。
2. **排障表错挂**：Troubleshooting 表的 `Box 1`（"Low post-thaw viability"）步骤栏不含主流程
   编号，映射器取首个整数 1、命中了主流程第一块（毛发采集）——该行挂到了完全无关的块上。

修复（`extract_protocol.py`）：

- 新增 `lift_box_inserts`：识别 `BOX N` 标题段，把它的 item 段整体后移到流程末尾（定位方式
  是"盒子之后第一个 `上一步主流程编号 + 1` 的步骤"即续写点）；主流程小节因此重新连续，
  BOX 独立成块（`Block8 [BOX 1 Thawing] steps 1-12`）。
- `map_troubleshooting` 增加 `Box N` 分支：按**块标题**匹配盒子块，不再靠数字撞主流程范围。

修复后的 ipsc 映射：`Box 1` → BOX 块、`35` → 主流程块（22–37），全部落位。
**0923 三篇回归**：用同一抽取器重跑 vine-seq / organoid-3d-imaging / cu-co2reconstruction，
`title / procedure_pages / blocks / troubleshooting_unmatched / extraction` 逐字段一致（无 BOX
结构的论文不受影响）。

## 3. 出题（`protocol_to_questions.py`）

每块 2 题 × 3 轮（独立目录），共 144 题 = 42（7 块）+ 48（8 块）+ 54（9 块）。

- 9 个轮次全部一次跑通：无解析失败、无空数组、无重试告警。
- 题型配比（144）：`critical_step` 65、`parameter` 42、`complete_step` 19、`handling` 11、
  `inconsistency` 4、`order` 3；2 题配了 4 个干扰项，1 题的 `ideal` 未点名步骤号。
- 字段级校验：9 个目录全部 PASS（WARN 均为轮次间同块题干相似 0.40–0.44，低于导出侧
  dedupe 阈值 0.75，未触发删题）。

样例（BOX 块与 trace-n-seq 各一）：

> Q: After thawing keratinocytes from a cryovial and completing the plating procedure, you notice
> very few cells attached to the wells the next day. Which of the following may address this issue?
> ideal: Supplement the plating medium in step 9 with 10 µM ROCK inhibitor and keep it in the
> medium for at least 48 h to improve post-thaw attachment.
> （干扰项：改离心速度 / 只在换液时补 ROCK / 不加 ROCK —— 三种不同失败模式）

> Q: After sorting cells into the 384-well plate and sealing it, you notice that several wells
> appear empty when you check under the microscope. Which of the following may address this issue?
> ideal: Reduce the FACS pressure in step 44 to the lowest setting so the droplet is fully
> deposited; high pressure can deflect the stream and miss the well.

## 4. 导出与两层保真（`--type protocolqa`）

`Exported 144 protocolqa entr(ies)`，**0 条跳过**（无 `ideal` 为空 / 干扰项越界 / 引用块外步骤）。

| 维度 | 官方 108 题 | 本池 144 题 |
|---|---|---|
| 题干字符 | 95–479 | 139–348 |
| `ideal` 字符 | 16–220 | 76–200 |
| 干扰项个数 | 3–6 | 3–4 |
| 协议字符 | 569–14188 | 918–9207 |
| 协议步数 | 7–69 | 4–18（低于官方：我们按小节切得更细） |
| `ideal` 点名步骤 | 93/108（86%） | 143/144（99%） |
| 干扰项点名步骤 | 305/366（83%） | 433/434（100%） |

信封 8 键、键序、`indent=1` 无尾随换行与官方一致（`--check` 通过）；`subtask` 为自有值
`protocolqa-v1-heureka`、`canary` 为自有 GUID。144 题共用 **24 个互不相同的协议块**
（每块 2 题 × 3 轮，唯一协议正文合计 6.1 万字符）。

## 5. 官方评测打分

复刻口径同 0923（协议前置 + 官方 prompt/解析/判分），6 路切片并发、无网关故障，缓存
144/144 全覆盖。

| 题集 | 题数 | MiniMax-M3 | deepseek-v4-flash |
|---|---|---|---|
| 候选池全集（0926） | 144 | **0.854**（strict 0.812，123 对，格式修复 6） | **0.910**（strict 0.910，131 对，修复 0） |
| 官方原题（0926 当天重测） | 108 | **0.447**（strict 0.363，48 对，认输 3，修复 14） | **0.852**（strict 0.843，92 对，修复 1） |

官方原题这行是当天用同一复刻口径重跑的（6 路切片，108/108 判定完毕）；与 0923 的旧测
（M3 0.425 / deepseek 0.850）几乎一致，两遍逐题字母一致率 M3 39%、deepseek 93%。

两个模型都认这个池子比官方容易；M3 的差距尤其大（0.854 vs 0.425），结构性原因同 0923：
协议块更短（中位约 2.4k 字符 vs 官方 4157）、且要求补救措施由块内原文支撑（防编造的公平性
代价，把"考领域直觉"降级成"考文本推理"）。

## 6. 候选池与拒绝采样（144 候选 → 四档子集）

筛选命令全部带 `--judge-only --dedupe-similarity 0.75`（dedupe 实际 0 命中，相似度都低于 0.75）。

| 题集 | 题数 | solver | 挑选那遍 | **重跑那遍** | 逐题字母一致率 |
|---|---|---|---|---|---|
| `_dsonly`（只按 deepseek 失败筛） | 13 | MiniMax-M3 | 0.538 | **0.308** | 10/13（77%） |
| | | deepseek | 0.000 | **0.231** | 8/13（62%） |
| `_filtered`（任一没答对） | 28 | MiniMax-M3 | 0.250 | **0.750** | 8/28（29%） |
| | | deepseek | 0.536 | **0.607** | 25/28（89%） |
| `_official`（面板均分 ∈ [0.4,0.6]） | 22 | MiniMax-M3 | 0.318 | **0.818** | 8/22（36%） |
| | | deepseek | 0.682 | **0.773** | 20/22（91%） |
| `_hard`（两家都答错） | 6 | MiniMax-M3 | 0.000 | **0.167** | 5/6（83%） |
| | | deepseek | 0.000 | **0.000** | 5/6（83%） |

复核方式：子集复制成 `<name>_retest.json`（新 bench 名，缓存分家）整份重跑一遍。

**读法**：

- **M3 单题判定仍是噪声**：挑选遍到重跑遍，`_official` 0.318→0.818、`_filtered` 0.250→0.750，
  逐题字母一致率只有 29–36%——与 0923 在官方题上实测的 35% 一致。按 M3 单遍失败筛子集，
  必然向池均值回归。**M3 只能看聚合分，不能用来选题**。
- **deepseek 稳定**：一致率 89–91%（`_dsonly`/`_hard` 这两个按"它答错"筛出来的窗口内
  62–83%，正常——它在这两个子集上本来就答错，选的是它的困难区）。
- 因此本批次**难度声明以 deepseek 口径为准**；`_dsonly` 就是"按稳定模型失败重筛"的落实
  （0924 报告注意事项 2 的改法）。

**与官方基线的对照**（同模型、各自比较）：

| 口径 | deepseek | vs 官方 0.850 | MiniMax-M3 | vs 官方 0.425 |
|---|---|---|---|---|
| `_official` 22 | 0.773 | 略难（-0.08） | 0.818 | 偏易 |
| `_filtered` 28 | 0.607 | 更难 | 0.750 | 偏易 |
| `_dsonly` 13 | 0.231 | 难得多 | 0.308 | 略难（-0.12） |
| `_hard` 6 | 0.000 | 远难于官方 | 0.167 | 远难于官方 |

没有一档能在两个模型上同时落进官方档位（本池对 M3 相对更容易）；按 solver 分别取值：
**deepseek 口径最接近官方档位的是 `_official` 22（0.773 vs 0.850）**，
**M3 口径最接近的是 `_dsonly` 13（0.308 vs 0.425）**，而 `_dsonly` 对 deepseek 干脆比官方难
（0.231）——可作为"稳定模型口径硬核集"。

## 7. 与 0923 批次的对照

| 维度 | 0923 批次 | 0926 批次 |
|---|---|---|
| 论文 / 协议块 | 3 / 26 | 3 / 24 |
| 候选 → 导出 | 138 → 136（跳 2 条引用块外步骤） | 144 → **144**（跳 0） |
| 池分 M3 / deepseek | 0.907 / 0.926 | 0.854 / 0.910 |
| `_filtered` 重跑（n） | 0.728 / 0.611（18） | 0.750 / 0.607（28） |
| `_official` 重跑（n） | 0.769 / 0.692（13） | 0.818 / 0.773（22） |
| `_hard`（n） | 0.040 / 0.000（5） | 0.167 / 0.000（6） |
| M3 子集一致率 / deepseek | 38–39% / 83–92% | 29–36% / 89–91% |

两批各档位分数高度一致（`_filtered` 重跑 0.75/0.61 vs 0.73/0.61），说明这条链路与难度
分布已经稳定可复现。

## 8. 产物清单

题包在 `data/labbench/`（框架目录，按 AGENTS.md 不进 git）：

| 位置 | 内容 |
|---|---|
| `protocolqa_pool_0926.json` (+ `_eval.json`) | 候选池全集 144 题（官方 8 键信封 + 字母 sidecar） |
| `protocolqa_pool_0926_official.json` | 22 题（面板均分 [0.4,0.6]，deepseek 口径最接近官方） |
| `protocolqa_pool_0926_dsonly.json` | 13 题（稳定模型失败口径，两个模型上都比官方难） |
| `protocolqa_pool_0926_filtered.json` / `_hard.json` | 28 题 / 6 题 |
| `*_retest.json`（四份 + sidecar） | 复核重跑用的副本（与主文件逐字段一致，仅缓存分家） |
| `labbench_solved_protocolqa_pool_0926_<model>.json` | 池子逐题作答与判分；四子集 retest 缓存另存 |
| `shard/protocolqa_pool_0926_s*.json` + `solve_s*.log` | 6 路切片的题目副本与评测日志 |

工作副本（`molintbench/protocal-0926/`，不进 git）：每篇一个目录含 `paper.pdf`、`protocol.json`、
`procedure_clean.txt`；`pool/<slug>-r1..r3/` 9 个轮次的生成件、日志与扁平 eval。

仓库内改动（本次）：`extract_protocol.py`（`lift_box_inserts` + `Box N` 排障映射）、
`PIPELINE.md` §5.8（四个坑 + BOX）与 §5.5（去掉过时的"预留接口"表述）、
`SKILL.md` ProtocolQA 附录（五点 + BOX）。**未提交，待确认。**

## 9. 复现

```bash
cd /Users/mac/Documents/hunpo_work/HeurekaBench/scheurekabench/benchmark_creation
PY=/Users/mac/Documents/hunpo_work/HeurekaBench/.venv/bin/python
BASE=/Users/mac/Documents/hunpo_work/HeurekaBench/molintbench/protocal-0926

# 1) 抽协议（DOI 显式给出——工作副本叫 paper.pdf，文件名里没有 DOI）
$PY extract_protocol.py --pdf $BASE/antifibrotic-screening/paper.pdf --out $BASE/antifibrotic-screening \
    --paper-id antifibrotic-screening --doi https://doi.org/10.1038/s41596-026-01445-8 --dump-text
$PY extract_protocol.py --pdf $BASE/ipsc-remote-kit/paper.pdf --out $BASE/ipsc-remote-kit \
    --paper-id ipsc-remote-kit --doi https://doi.org/10.1038/s41596-026-01440-z --dump-text
$PY extract_protocol.py --pdf $BASE/trace-n-seq/paper.pdf --out $BASE/trace-n-seq \
    --paper-id trace-n-seq --doi https://doi.org/10.1038/s41596-026-01416-z --dump-text

# 2) 候选池：每块 2 题 × 3 轮（三篇可并行，各自独立目录）
for slug in antifibrotic-screening ipsc-remote-kit trace-n-seq; do for r in 1 2 3; do
  mkdir -p $BASE/pool/$slug-r$r && cp $BASE/$slug/protocol.json $BASE/pool/$slug-r$r/
  $PY protocol_to_questions.py --protocol_json_path $BASE/pool/$slug-r$r/protocol.json \
      --model_call gateway --per-block 2
done; done

# 3) 导出 + 两层自检（--base_dir 接受 glob）
$PY export_labbench.py --type protocolqa --base_dir $BASE/pool/*/ \
    --out data/labbench/protocolqa_pool_0926.json --check --check-style

# 4) 评测（6 路切片摊平网关毛刺，再合回主缓存）
$PY shard_labbench.py --bench data/labbench/protocolqa_pool_0926.json --shards 6 --split
for s in 0 1 2 3 4 5; do
  GATEWAY_TIMEOUT=90 $PY solve_labbench.py data/labbench/shard/protocolqa_pool_0926_s$s.json \
      --solver MiniMax-M3,deepseek-v4-flash & done; wait
$PY shard_labbench.py --bench data/labbench/protocolqa_pool_0926.json --shards 6 \
    --merge --models MiniMax-M3,deepseek-v4-flash

# 5) 四档筛选（--judge-only 复用缓存）
B=data/labbench/protocolqa_pool_0926
$PY solve_labbench.py $B.json --solver deepseek-v4-flash --drop-all-correct --judge-only \
    --dedupe-similarity 0.75           # → _filtered，手工改名 `_dsonly`
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --drop-all-correct \
    --judge-only --dedupe-similarity 0.75
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --keep-hard \
    --judge-only --dedupe-similarity 0.75
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --keep-band 0.4 0.6 \
    --band-suffix official --judge-only --dedupe-similarity 0.75

# 6) 复核（必做）：子集复制成新 bench 名重跑，比较逐题字母一致率
for n in dsonly filtered official hard; do
  cp ${B}_$n.json ${B}_${n}_retest.json; cp ${B}_${n}_eval.json ${B}_${n}_retest_eval.json
  GATEWAY_TIMEOUT=90 $PY solve_labbench.py ${B}_${n}_retest.json --solver MiniMax-M3,deepseek-v4-flash
done
```

## 10. 遗留

1. **版权**：`protocol` 字段是 Nature Protocols 正文逐字引用（24 个块、唯一正文约 6.1 万字符，
   池内合计 36 万字符）；公开发布前必须取得授权或大幅裁短（同 0924 发布版注意事项）。
2. **未做提难度杠杆**：合并小块成长协议（对齐官方中位 4k 字符/29 步）、在保公平前提下允许部分
   题目依赖领域公认判断——若下批仍偏易可启用。
3. **面板第三个模型未纳入**（qwen 系列本轮未测）；`_dsonly` 口径已落实 0924 报告建议，
   如要再严可对 `_dsonly` 做 deepseek 两遍共识（本批两遍一致 8/13，可用第三遍收敛）。
4. 与 0923 池（136 题）**未合并**；如需合并，把两批共 6 篇的 pool 目录一起重新导出、
   按新 bench 名重评（缓存按下标命中，不能复用旧文件）。
