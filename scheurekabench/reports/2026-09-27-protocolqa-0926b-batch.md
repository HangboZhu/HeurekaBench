# ProtocolQA 第三批（0926b）生产报告

日期：2026-09-26 开工，2026-09-27 完成（评测段受网关降级拖长）
对象：`molintbench/protocal-0926-2/` 下三篇 Nature Protocols 论文的 Procedure（人 PSC → 八细胞胚样
细胞、多巴胺聚合介导的活细胞表面功能化、组织扩张质谱成像 TEMI）→ 抽协议块 → 出排障题 → 导出
futurehouse/lab-bench ProtocolQA 格式 → 官方评测打分 → 拒绝采样校准难度

链路沿用 0923/0926（`extract_protocol.py` → `protocol_to_questions.py` → `export_labbench.py
--type protocolqa` → `solve_labbench.py`），本批修了抽取器三处结构缺陷（见 §2）。**所有改动未提交。**

## 0. 结论摘要

- 三篇共 **33 个可出题协议块**（胚样 3 / 多巴胺 16 / TEMI 14）→ **196 个候选**（每块 2 题 × 3 轮）
  → 导出 **186 题**（导出器跳过 9：7 条引用块外步骤、2 条干扰项不足；另人工撤 1 条 `ideal` 超
  官方上限）。
- 信封与条目形态两层自检（`--check` / `--check-style`）**均通过**；`ideal` 点名步骤率 100%
  （官方 86%），干扰项点名步骤率 100%（官方 83%）。
- **池子比 0923/0926 两批都硬，且首次不筛就接近官方档位**：186 题 MiniMax-M3 **0.734**
  （strict 0.679）、deepseek **0.794**（strict 0.789，183/186；3 题网关故障未判定）。
- 同日官方原题重测基线：M3 **0.359**（strict 0.275，108/108）、deepseek **0.830**（strict 0.811，
  106/108）。M3 的官方分三次重测在 0.359–0.447 之间漂移（其单题字母一致率本就只有 35–39%），
  **难度对照以 deepseek 为锚**。
- 四个难度子集（**规模与复核重跑分**）：`_dsonly` **37**（deepseek 0.063 / M3 0.543）、
  `_filtered` **60**（0.476 / 0.470）、`_official` **35**（0.722 / 0.609）、`_hard` **26**
  （0.095 / 0.504）；deepseek 口径下池全量（0.794）与 `_official`（0.722）都在官方档位内。
- 抽取器修三处：**裸 `Procedure N` 标题**（多巴胺篇两段 Procedure 各自重新编号）、**无编号 BOX
  后移**（TEMI 篇 BOX 2 是纯参数说明，原先会把后续主流程步骤 22–31 挂到 BOX 标题下）、**排障表
  `Procedure N` 分隔行**（步号重叠时按行归属到正确的 Procedure）。0923/0926 六篇用同一抽取器
  重跑，`protocol.json` **逐字段一致**（含排障映射）。
- 网关本批明显降级（deepseek 路由对长 prompt 大量 503/超时），空回复按管线口径**不计分、不入
  缓存**，靠切片重跑 + 定向补跑把覆盖拉到 183/186；残留 5 题（池 3 + 基线 2）记为 UNJUDGED，
  不进子集、不算难题。

## 1. 协议抽取（`extract_protocol.py`）

| 论文 | DOI | Procedure 页 | 步数 | 块数（可出题） | 块字符区间 | Troubleshooting 行 → 映射条目 |
|---|---|---|---|---|---|---|
| 人 PSC → 八细胞胚样细胞（`embryo-like-cells`） | `10.1038/s41596-026-01414-1` | 20–28 | 31 | 4（3） | 561–11327 | 42 → 3，未映射 2 |
| 多巴胺聚合活细胞功能化（`dopamine-surface`） | `10.1038/s41596-026-01422-1` | 14–25 | 183 | 16（16） | 874–6128 | 14 → 10，未映射 2 |
| 组织扩张质谱成像 TEMI（`temi-msi`） | `10.1038/s41596-026-01427-w` | 13–25 | 113 | 16（14） | 498–4790 | 5 → 5，未映射 0 |

- **结构**：多巴胺篇与 TEMI 篇都是「多段 Procedure、各自从 1 重新编号」的结构（多巴胺：Procedure 1
  的 1–108 + Procedure 2 的 1–75；TEMI：1–70 + 1–8 + 1–35），抽取窗口从首个真实 `Procedure` 标题起、
  到 `Timing`/`Anticipated results` 止，三段的编号重启**按原文保留、不重编号**。
- **胚样篇只有 3 个可出题块**：Block2 是论文自身的巨型 step 16（11,327 字符，选项 A/B 各含
  (i)–(xix) 子步骤，官方 protocol 上限 14,188 字符以内），Block3（"Induction of human 8CLCs"，
  561 字符）**无编号步骤**、按 `numbered=false` 跳过。该篇的排障表 42 行里只有 5 条独立
  problem（其余 37 行是同一 problem 的多条原因/方案，被折叠进上一条），其中 2 条无步骤号 → 未映射。
- 逐篇抽读 `procedure_clean.txt`：无图注/表格/坐标轴污染；CRITICAL STEP、TIMING、PAUSE POINT 行
  按原文保留。

## 2. 抽取器三处修复（`extract_protocol.py`，本批新增）

1. **裸 `Procedure N` 标题**（`find_procedure`）：多巴胺篇的两段 Procedure 用 11.25 pt 的
   `Procedure 1` / `Procedure 2`（**无冒号**）标注，而 Outline、Materials 列表里以正文尺寸重复同样
   字样。原正则只认 `Procedure N:` 形式 → 报 `no Procedure section found`。新增候选（`Procedure N`
   全文匹配）后，靠既有的"必须紧接 step 1"判据筛掉全部仿冒者。
2. **无编号 BOX 也要后移**（`lift_box_inserts`）：TEMI 篇 `BOX 2`（Instrument parameters for
   LC–MS lipidomics）是**纯说明文字、内含编号为 0**，浮动在 Procedure 3 的 step 21 与 22 之间。原实现
   只搬"含步骤的 BOX"，这条不搬 → 后续主流程步骤 22–31 被归到 `BOX 2` 标题下、正文以一段残缺的表格
   碎片开头。现在按同一判据（盒子之后出现 `上一步+1` 的步骤即视为插入）后移；并顺带修正：**紧邻恢复
   步骤之前的标题属于恢复后的章节**（TEMI 的 `LC–MS analysis` 原先被当作盒内内容一起搬走，导致
   步骤 11–13 失去自己的标题）。
3. **排障表 `Procedure N` 分隔行**（`map_troubleshooting`）：多巴胺篇排障表用「`Procedure 1` /
   `Procedure 2` 单独成行」分段，行内步号是裸数字。原实现按"首个范围包含该步号的块"匹配 → Procedure 2
   的行（22/32/55/64）全被错挂到 Procedure 1 的块上（例如 "BMDCs die after incubation" 挂到
   TEM/LSCM 块）。现在分隔行的标签向下延续，裸步号落在正确的 Procedure 上（修复后：P1 行 → P1 块
   5 条，P2 行 → P2 块 4 条，2 条无步号行仍记未映射）。

**回归**：用修好的抽取器重跑 0923（vine-seq / organoid-3d-imaging / cu-co2reconstruction）与 0926
（antifibrotic-screening / ipsc-remote-kit / trace-n-seq）六篇，输出 `protocol.json` 与归档件
**逐字段一致**（含 blocks、troubleshooting 映射、extraction 统计）。

## 3. 出题（`protocol_to_questions.py`）

每块 2 题 × 3 轮（独立目录），共 9 个轮次、33 块 → **196 题**（多巴胺 96 / 胚样 16 / TEMI 84）。

- 9 个轮次全部跑通：无解析失败、无 stray-block。
- 题型配比（196）：`critical_step` 86、`parameter` 63、`complete_step` 19、`inconsistency` 11、
  `handling` 11、`order` 6；干扰项 5 题配了 4 个、2 题只有 2 个（后者导出时跳过）。
- 1 个块未出题（胚样 r3 的 Block1，模型判断该块无可辩护的「问题-补救」对），属允许情形。
- 抽样检查：全量 196 题的题干/`ideal` **无**图、表、Supplementary、"the paper" 之类引用
  （唯一命中 "the source" 的是"去除非特异信号的来源"，语义正常）。生成器报的 4 处
  "reference the source article" 告警（temi-msi r1/r2 各 2）均为 `cryosection`/`tissue sections`
  的 "section" 子串误报；另有 25 次"带反馈重生成一次"的自纠，最终全部收敛。

## 4. 导出与两层保真（`--type protocolqa`）

`Exported 186 protocolqa entr(ies)`；跳过 9 条（`cites-step-outside-the-protocol` 7、
`distractor-count-2` 2）；另人工撤 1 条（`ideal` 232 字符 > 官方上限 220，其余指标正常）。

| 维度 | 官方 108 题 | 本池 186 题 |
|---|---|---|
| 题干字符 | 95–479 | 149–336 |
| `ideal` 字符 | 16–220 | 90–219 |
| 干扰项个数 | 3–6 | 3–4（3 个 181 题、4 个 5 题） |
| 协议字符 | 569–14188 | 874–11285 |
| 协议步数 | 7–69 | 1–29（低于官方：按小节切得更细；1 步那条是胚样篇的巨型 step 16） |
| `ideal` 点名步骤 | 93/108（86%） | 186/186（100%） |
| 干扰项点名步骤 | 305/366（83%） | 563/563（100%） |

信封 8 键、键序、`indent=1` 无尾随换行与官方一致（`--check` 通过）；`subtask` 为自有值
`protocolqa-v1-heureka`、`canary` 为自有 GUID。186 题共用 **33 个互不相同的协议块**，唯一协议正文
合计 **92,798 字符**。

字段级校验（`validate_question_pack.py --qtype protocolqa`）：9 个轮次的 canonical/eval 全部通过
（2 个轮次带 WARN，均为同轮内题干相似度 0.400–0.406，低于导出侧 dedupe 阈值 0.75）；对导出后的
186 题做**步骤引用硬检查 0 违规**（每个 `ideal`/干扰项点名的步骤号都在该题自己的 `protocol` 块内），
四个子集同样 0 违规。

## 5. 官方评测打分

复刻口径同 0923/0926（协议前置 + 官方 prompt/解析/判分，1.0/0.1/0.0），6 路切片并发。

### 5.1 同日官方基线（`ProtocolQA_full_0926b.json`，108 题）

| solver | 判定数 | mean（修复后） | strict | 对/认输/格式修复 |
|---|---|---|---|---|
| MiniMax-M3 | 108/108 | **0.359** | 0.275 | 38 / 8 / 17 |
| deepseek-v4-flash | 106/108 | **0.830** | 0.811 | 88 / 0 / 2 |

**M3 的官方基线在三次同日重测间波动明显**：0923 批 **0.425**、0926 批 **0.447**、本批 **0.359**
（deepseek 对应 0.850 / 0.852 / 0.830，稳定）。M3 在官方原题上的逐题字母一致率本身就是 35–39%，108 题
上的聚合分因此有 ~±0.05 的漂移；**难度对照以 deepseek 为锚**，M3 只作参考（这条口径从 0923 起就写在
报告里，本批第三次复现）。deepseek 的 2 题缺口与池子那 3 题同类（≥10 次重试仍 503），按口径记为
UNJUDGED。

### 5.2 候选池全集（186 题）

| solver | 判定数 | mean | strict | 对/认输/格式修复 |
|---|---|---|---|---|
| MiniMax-M3 | 186/186 | **0.734** | 0.679 | 136 / 5 / 15 |
| deepseek-v4-flash | 183/186 | **0.794** | 0.789 | 145 / 3 / 2 |

分篇（M3，按题归属）：多巴胺 89 题 **0.654**、TEMI 81 题 **0.781**、胚样 16 题 **0.938**。
本批池子比 0923（M3 0.907）/0926（0.854）**明显更硬**；题型上 `critical_step` 86 + `parameter` 63
占全池的 76%（多巴胺篇 78%），没有出现 0923/0926 那种大面积"某模型近满分"的形态。按 deepseek 口径，
**池子（0.794）已经略低于同日官方原题（0.830）** —— 这是三批以来第一次。

### 5.3 网关降级与处理

本批 deepseek 路由对长 prompt 大量 503（`Service temporarily unavailable`）与读超时；管线口径是
**空回复不计分、不入缓存**（绝不当错答）。`shard_labbench.py` 6 路切片 + 缓存断点续跑把可判定的
部分先固化，缺口用 `GATEWAY_TIMEOUT=40` 的补跑驱动反复重试（跳过该模型已知必死的 anthropic 路由，
使每次进程只付一次全轮超时）。最终 **deepseek 判定 183/186、M3 186/186**；残留 3 题（下标 133、153、
166）与官方基线 2 题在十余次重试后仍未判定，按口径记为 UNJUDGED 并从子集外剔除——它们的 prompt 长度
（1.6–3.1k 字符）都在池子中位以下，不是长度问题，属当日线路抖动。

## 6. 候选池与拒绝采样（186 候选 → 四档子集）

筛选命令全部带 `--judge-only --dedupe-similarity 0.75`（dedupe 只在面板 `_filtered` 上命中 1 题）。
四档规模：`_dsonly` **37**、`_filtered` **60**、`_official` **35**、`_hard` **26**；3 题 UNJUDGED
被排除在子集外（§5.3）。

| 题集 | 题数 | solver | 挑选那遍 | **重跑那遍** | 逐题字母一致率 |
|---|---|---|---|---|---|
| `_dsonly`（只按 deepseek 失败筛） | 37 | MiniMax-M3 | 0.300 | **0.543** | 12/37（32%） |
| | | deepseek | 0.008 | **0.063** | 31/35（89%） |
| `_filtered`（任一没答对） | 60 | MiniMax-M3 | 0.192 | **0.470** | 22/60（37%） |
| | | deepseek | 0.388 | **0.476** | 50/55（91%） |
| `_official`（面板均分 ∈ [0.4, 0.6]） | 35 | MiniMax-M3 | 0.354 | **0.609** | 11/35（31%） |
| | | deepseek | 0.660 | **0.722** | 28/32（88%） |
| `_hard`（两家都答错） | 26 | MiniMax-M3 | 0.004 | **0.504** | 9/26（35%） |
| | | deepseek | 0.008 | **0.095** | 19/22（86%） |

**读法**：

- **M3 单题判定依旧是噪声**：逐题字母一致率 31–37%（与它在官方原题上的 35–39% 一致），`_hard` 上
  挑选遍 0.004 → 重跑遍 0.504、`_dsonly` 0.300 → 0.543。按 M3 单遍失败筛子集必然向池均值回归，
  **M3 只能看聚合分、不能用来选题**（第三次复现）。
- **deepseek 稳定**：一致率 86–91%；`_dsonly`/`_hard` 这两个按"它答错"筛出来的窗口内较低（86–89%），
  属正常（选的就是它的困难区）。

**与官方基线的对照**（同模型、各自比较；官方同日基线 M3 0.359 / deepseek 0.830）：

| 口径 | deepseek | vs 官方 0.830 | MiniMax-M3 | vs 官方 0.359 |
|---|---|---|---|---|
| **池全量 186** | 0.794 | 略难（-0.04） | 0.734 | 偏易 |
| `_official` 35 | 0.722 | 略难（-0.11） | 0.609 | 偏易 |
| `_filtered` 60 | 0.476 | 难得多 | 0.470 | 偏易 |
| `_dsonly` 37 | 0.063 | 远难于官方 | 0.543 | 偏易 |
| `_hard` 26 | 0.095 | 远难于官方 | 0.504 | 偏易 |

**本批最重要的一条**：**池子全量本身就已经落在 deepseek 的官方档位附近（0.794 vs 0.830）** —— 0923
（0.926）与 0926（0.910）要筛到 `_official` 才接近官方，本批不筛也接近。对 M3 仍偏易（0.734 vs 0.359），
但没有一个子集能在 M3 上落到官方档位：该模型今日在官方题上只拿到 0.359，而它在本池上最难的
`_filtered` 也有 0.470。**难度声明按 solver 分别写**：deepseek 口径下，`_official` 35 题（0.722）与
池全量（0.794）都在官方档位内；`_dsonly` 37 题（0.063）是"稳定模型口径硬核集"。

## 7. 与 0923/0926 批次对照

| 维度 | 0923 | 0926 | **0926b（本批）** |
|---|---|---|---|
| 论文 / 协议块 | 3 / 26 | 3 / 24 | 3 / 33（可出题） |
| 候选 → 导出 | 138 → 136 | 144 → 144 | 196 → 186 |
| 池分 M3 | 0.907 | 0.854 | **0.734** |
| 池分 deepseek | 0.926 | 0.910 | **0.794** |
| 官方基线同日重测 M3 / deepseek | 0.425 / 0.850 | 0.447 / 0.852 | 0.359 / 0.830 |
| 子集（重跑遍）deepseek | `_filtered` 0.611（18）| `_filtered` 0.607（28）、`_dsonly` 0.231（13）| `_official` 0.722（35）、`_dsonly` 0.063（37）|
| 抽取器改动 | 首版 | BOX 含步骤后移 | 裸 `Procedure N`、无编号 BOX 后移、排障表分隔行 |

三批的难度分布**单向变硬**（deepseek 池分 0.926 → 0.910 → 0.794），本批首次做到"不筛即接近官方
档位"。可能的原因：本批三篇的 Procedure 更长更细（多巴胺篇 183 步 / 16 块、TEMI 113 步 / 14 块），
可出题的块更多落在"多步定量推理"上；同时 `_dsonly` 口径已按 0926 的稳定模型建议执行。

## 8. 产物清单

题包在 `data/labbench/`（框架目录，按 AGENTS.md 不进 git）：

| 位置 | 内容 |
|---|---|
| `protocolqa_pool_0926b.json` (+ `_eval.json`) | 候选池全集 186 题（官方 8 键信封 + 字母 sidecar） |
| `protocolqa_pool_0926b_{dsonly,filtered,official,hard}.json` (+ `_eval`) | 四档难度子集：37 / 60 / 35 / 26 题 |
| `protocolqa_pool_0926b_*_retest.json` (+ `_eval`) | 复核重跑副本（缓存分家） |
| `ProtocolQA_full_0926b.json` (+ `_eval`) | 同日官方基线重测副本 |
| `labbench_solved_protocolqa_pool_0926b_<model>.json`、`labbench_solved_ProtocolQA_full_0926b_<model>.json` | 逐题作答与判分缓存 |
| `shard/protocolqa_pool_0926b_s*.json` + `shard/labbench_solved_*` | 6 路切片与切片缓存 |
| 发布交付副本（五份题包 + 逐题作答 + 官方基线 + 日志 + 工具 + manifest） | `creating_labbench/release_20260926-2/`；打包件 `release_20260926-2.zip` + `.sha256`（格式同 `release_20260924/`） |

工作副本（`molintbench/protocal-0926-2/`，不进 git）：每篇 `paper.pdf` + `protocol.json` +
`procedure_clean.txt`；`pool/<slug>-r1..r3/` 9 个轮次的生成件与日志。

仓库内改动：`extract_protocol.py`（三处修复，见 §2）。**未提交。**

## 9. 复现

```bash
cd /Users/mac/Documents/hunpo_work/HeurekaBench/scheurekabench/benchmark_creation
PY=/Users/mac/Documents/hunpo_work/HeurekaBench/.venv/bin/python
BASE=/Users/mac/Documents/hunpo_work/HeurekaBench/molintbench/protocal-0926-2
mkdir -p $BASE/embryo-like-cells $BASE/dopamine-surface $BASE/temi-msi
cp $BASE/s41596-026-01414-1.pdf $BASE/embryo-like-cells/paper.pdf
cp $BASE/s41596-026-01422-1.pdf $BASE/dopamine-surface/paper.pdf
cp $BASE/s41596-026-01427-w.pdf $BASE/temi-msi/paper.pdf

# 1) 抽协议（DOI 显式给出——工作副本叫 paper.pdf，文件名里没有 DOI）
$PY extract_protocol.py --pdf $BASE/embryo-like-cells/paper.pdf --out $BASE/embryo-like-cells \
    --paper-id embryo-like-cells --doi https://doi.org/10.1038/s41596-026-01414-1 --dump-text
$PY extract_protocol.py --pdf $BASE/dopamine-surface/paper.pdf --out $BASE/dopamine-surface \
    --paper-id dopamine-surface --doi https://doi.org/10.1038/s41596-026-01422-1 --dump-text
$PY extract_protocol.py --pdf $BASE/temi-msi/paper.pdf --out $BASE/temi-msi \
    --paper-id temi-msi --doi https://doi.org/10.1038/s41596-026-01427-w --dump-text

# 2) 候选池：每块 2 题 × 3 轮（三篇各自独立目录，按篇 3 并发）
for r in 1 2 3; do for slug in embryo-like-cells dopamine-surface temi-msi; do
  mkdir -p $BASE/pool/$slug-r$r && cp $BASE/$slug/protocol.json $BASE/pool/$slug-r$r/
  $PY protocol_to_questions.py --protocol_json_path $BASE/pool/$slug-r$r/protocol.json \
      --model_call gateway --per-block 2
done; done

# 3) 导出 + 两层自检
$PY export_labbench.py --type protocolqa --base_dir $BASE/pool/*/ \
    --out data/labbench/protocolqa_pool_0926b.json --check --check-style

# 4) 评测：池子 6 路切片 + 同日官方基线重测（缓存断点续跑；空回复不计分）
$PY shard_labbench.py --bench data/labbench/protocolqa_pool_0926b.json --shards 6 --split
for s in 0 1 2 3 4 5; do GATEWAY_TIMEOUT=90 $PY solve_labbench.py \
    data/labbench/shard/protocolqa_pool_0926b_s$s.json --solver MiniMax-M3,deepseek-v4-flash & done; wait
$PY shard_labbench.py --bench data/labbench/protocolqa_pool_0926b.json --shards 6 \
    --merge --models MiniMax-M3,deepseek-v4-flash

cp data/labbench/ProtocolQA_full.json data/labbench/ProtocolQA_full_0926b.json
cp data/labbench/ProtocolQA_full_eval.json data/labbench/ProtocolQA_full_0926b_eval.json
# 基线同样切片评测（见 §5.1 的两个分数）

# 5) 四档筛选（--judge-only 复用缓存；先跑 deepseek 单模型再改名，避免覆盖 _filtered）
B=data/labbench/protocolqa_pool_0926b
$PY solve_labbench.py $B.json --solver deepseek-v4-flash --drop-all-correct --judge-only \
    --dedupe-similarity 0.75 && mv ${B}_filtered.json ${B}_dsonly.json && \
    mv ${B}_filtered_eval.json ${B}_dsonly_eval.json
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --drop-all-correct \
    --judge-only --dedupe-similarity 0.75
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --keep-hard \
    --judge-only --dedupe-similarity 0.75
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --keep-band 0.4 0.6 \
    --band-suffix official --judge-only --dedupe-similarity 0.75

# 6) 复核（必做）：子集复制成新 bench 名重跑，比较逐题字母一致率
for n in dsonly filtered official hard; do
  cp ${B}_$n.json ${B}_${n}_retest.json; cp ${B}_$n_eval.json ${B}_${n}_retest_eval.json
  GATEWAY_TIMEOUT=90 $PY solve_labbench.py ${B}_${n}_retest.json --solver MiniMax-M3,deepseek-v4-flash
done
```

## 10. 遗留

1. **版权**：`protocol` 字段是 Nature Protocols 正文逐字引用（33 个块、唯一正文 92,798 字符，
   池内合计约 50 万字符）；公开发布前必须取得授权或大幅裁短（同 0924 发布版注意事项）。
2. **偏易问题在本批大幅缓解**：deepseek 口径下池全量已略难于官方（0.794 vs 0.830），只剩 M3 口径
   仍偏易（0.734 vs 0.359，但该基线本身不稳）。0926 列的提难杠杆（合并小块对齐官方中位、允许部分
   领域判断）仍未启用——本批不再急需。
3. M3 的官方基线在本批三次重测（0.425 / 0.447 / 0.359）之间波动 ~0.09，**难度声明继续以
   deepseek 口径为准**，M3 只用于聚合参考。
4. 胚胎胚样篇只有 3 个可出题块（巨型 step 16 + 无编号小节），是该篇产出少的结构性原因；
   未编号块（"Induction of human 8CLCs"）以及排障表中 2 条无步号行未参与出题。
5. 本批新踩的三个结构坑（多段 Procedure 各自编号、纯文字 BOX、排障表 Procedure 分隔行）已修在
   `extract_protocol.py`，但**尚未写进 skill 附录/PIPELINE §5.8** 的坑表（本次按约定不动文档）。
