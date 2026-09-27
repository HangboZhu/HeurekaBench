# LitQA2（m3only）与 ProtocolQA（official）数据集介绍

## 数据集概览

当前目录是 2026-09-24 交付的 LitQA2 与 ProtocolQA 题包发布副本，覆盖**文献推理**与**实验协议排障**
两类任务。两个题包都是四选一单选题（3 个干扰项 + 1 个正确项），评测时再追加一个"认输"选项；
答题模型必须只回一个字母。

- **LitQA2（`m3only` 版，17 题）**：闭卷。题干自包含地把论文证据写进题面，问的是"由这些证据能推出
  什么"，考的是证据链推理；来源是三篇 Nature Aging 论文。
- **ProtocolQA（`official` 版，13 题）**：开卷。题干描述一个实验异常，论文 Procedure 的**原文**
  （`protocol` 字段）随题一起给出，问的是"该改哪一步、怎么改"；来源是三篇 Nature Protocols 论文。

本目录按 HuggingFace `futurehouse/lab-bench` 官方格式整理，可直接用官方评测流程打分：信封字段
（键集、键序、值类型、JSON 布局）与官方文件一致，`export_labbench.py --check` 通过。原始候选池与
出题溯源不在此目录，保留在仓库归档 `creating_labbench/`（`benches/` 题包、`generated*/` 出题运行目录、
`scoring/` 历史打分）。

## 任务类型

**LitQA2 文献推理任务（闭卷，17 题）**：题面给出某篇论文的一组观察与实验设计，要求判断"若再做一步
干预，最可能看到什么结果"。四个选项围绕同一制度设计，干扰项分别对应三类失败模式（把结论反向、
把量级或方向搞错、把机制归给错误的环节），正确项必须在每一步都能由题面证据支撑。模型不看论文原文，
也拿不到 `key-passage`。

**ProtocolQA 协议排障任务（开卷，13 题）**：题面描述一个症状（如"电解几分钟后拉曼基线漂移""药物处理组
没有表型变化"），并问"你可以做什么来改善"。四个选项都要落到具体步骤号上的具体改动（改哪个参数、
按什么方向改），干扰项同样覆盖反向改、改错量级、改错步骤三类失败模式。答题者能看到被提问的那段
Procedure 原文，所以这题考的是"读协议 + 定位故障步 + 判断改法"，不是记忆。

两类的共同点是**唯一答案**：正确项是原论文里可追溯的事实或做法，判分是确定性的字母比对，不需要
主观评分。

## 数据字段与目录结构

```
release_20260924/
├── README.md                                  交付说明（目录、复现、来源与许可）
├── 数据集介绍.md                                本文件
├── manifest.json                              每个文件的 sha256 + 题包来源比对 + 评分摘要
├── bench/
│   ├── litqa2_pool_filtered_m3only.json        LitQA2 题包 17 题（官方 12 键信封）
│   ├── litqa2_pool_filtered_m3only_eval.json   选项顺序与答案字母（sidecar）
│   ├── protocolqa_pool_official.json           ProtocolQA 题包 13 题（官方 8 键信封）
│   └── protocolqa_pool_official_eval.json      选项顺序、答案字母与 DOI 溯源
├── baseline_official/
│   ├── litqa2_official199.json(+_eval)         官方 LitQA2 199 题（对照基线）
│   ├── protocolqa_official108.json(+_eval)     官方 ProtocolQA 108 题（对照基线）
│   └── labbench_solved_<题集>_<模型>.json        每个模型的逐题作答与判分
├── logs/                                      运行日志（逐题调用、网关失败、格式修复）
└── tools/                                     预检 / 汇总 / manifest / 打分复核 / 评测驱动
```

两个信封的字段：

- LitQA2（12 键，与官方同序）：`id / question / ideal / distractors / canary / source / tag /
  version / sources / is_opensource / subtask / key-passage`
- ProtocolQA（8 键，与官方同序）：`id / question / ideal / distractors / canary / source /
  protocol / subtask`（`protocol` 是唯一允许多行的字段）

## 关键文件说明

- **`bench/*.json`**：题包本体，平铺 JSON 数组，题面与选项文本都在里面（`ideal` 是正确项文本，
  `distractors` 是干扰项）。官方格式本身**不带字母编号**（官方 108/108 都是 ideal + distractors），
  呈现顺序由 sidecar 决定。
- **`bench/*_eval.json`**：sidecar，与题包同下标一一对应，存 `options`（字母顺序与选项文本）与
  `answer`（正确项字母）。**评测必须带 sidecar**，否则无法复现呈现顺序；ProtocolQA 的 sidecar 另存
  每题 DOI（官方信封没有放 DOI 的位置）。
- **`baseline_official/*.json`**：官方原题副本与它们的 sidecar，用于把"自产题难度"和"官方题难度"
  放在同一个模型、同一套 prompt 下比较。
- **`baseline_official/labbench_solved_*.json`、`bench/labbench_solved_*.json`**：逐题作答记录——
  模型原始回复（`raw`）、解析出的字母（`letter`）、官方分（`score`）、判定（`reason`）；回复格式让官方
  解析器取不到字母时，另存 `score_strict`（官方 CI 原样会得到的分）与格式修复那次重问的
  `repair_raw` / `repair_letter`。**要看某个模型的某道题具体答了什么，来这里找。**
- **`logs/solve_*.log`**：每个分片进程的逐题调用日志，包含网关失败（`GATEWAY FAILED`，该题留作
  未判定而不是判 0）与格式修复（`repaired -> X`）的完整记录。
- **`tools/`**：`preflight.py`（交付前预检）、`summarize.py`（汇总表）、`make_manifest.py`（哈希与
  来源比对）、`verify_scores.py`（从原始回复重算每一条分数）、`run_all.sh` / `run_eval.sh` /
  `fill_qwen.sh`（分片评测驱动）。

## 质量特点

**信封与官方逐字段一致。** `export_labbench.py --check` 对两个题包都通过（键集、键序、值类型，
JSON 文本布局 `indent=1`、无尾随换行、字段内无换行/双空格）——即官方 loader 可以直接读，不会因为
格式问题误判。两处**刻意**与官方不同的字段值：`subtask`（本目录为 `litqa-v2-heureka-pool` /
`protocolqa-v1-heureka`，官方是 `litqa-v2-public` / `protocolqa-v1-public`；冒用会让自产题被误认成
官方公开题）与 `canary`（自有 GUID，不复用官方 uuid）。

**条目形态与官方的对照**（`tools/style_stats.py` 实测）：

| 维度 | 官方 LitQA2 199 | LitQA2 发布版 17 | 官方 ProtocolQA 108 | ProtocolQA 发布版 13 |
|---|---|---|---|---|
| 题干字符 | 52–299（中位 115） | 347–674（中位 557） | 95–479（中位 148） | 165–265（中位 222） |
| `ideal` 字符 | 1–86（中位 7） | 128–360（中位 230） | 16–220（中位 50） | 118–196（中位 146） |
| 干扰项个数 | 1–9 | 3 | 3–6 | 3 |
| `key-passage`/`protocol` | 0–3667（中位 265，5 题无） | 387–2560（中位 1496） | 569–14188（中位 4158） | 850–5003（中位 1728） |
| `ideal` 点名步骤 | — | — | 90/108（83%） | 13/13（100%） |

读法：ProtocolQA 的形态与官方同一量级（题面略长、`ideal` 更完整、协议块更短）；LitQA2 的题干明显
比官方长——这是"自包含"风格的设计后果（官方题干是一句文献提问，我们把证据搬进题面换公平性），
不是导出缺陷（`--check-style` 会逐题报出这一差异）。

**答案位置无泄漏。** sidecar 的选项顺序由题 id 确定性洗牌，正确项在打印顺序中的位置分布：
LitQA2 17 题为 {1:2, 2:7, 3:3, 4:5}，ProtocolQA 13 题为 {1:5, 2:3, 3:3, 4:2}，官方 199 题为
{1:35, 2:43, 3:44, 4:56, …}——没有"正确项总在第一位"这类位置偏置（预检里作为硬性检查项）。

**难度已用官方评测校准**（详见下节）：LitQA2 发布版落在官方档位；ProtocolQA 发布版只在
`deepseek-v4-flash` 口径下落在官方档位，用 `MiniMax-M3` 量明显偏易。

**可复核。** `manifest.json` 记录每个文件的 sha256，并逐条比对题包与归档源文件是否逐字节一致；
`tools/verify_scores.py` 会从每题保存的原始回复重新解析、重新判分，与存储的分数比对（本轮
9 个缓存文件全部一致）。论文 PDF 不随本目录分发；题目依据的 DOI 记录在每题 `sources` 字段。

## 样例模型评测结果

本轮（2026-09-24）用三个模型对两个发布题包与两个官方基线各重跑一遍官方评测，每个模型每题只取
一次作答（不复用筛选那一遍的答案）。满分 1.0；"认输"（unsure）得 0.1。

| 模型 | 平均得分 | LitQA2 发布版（17 题） | ProtocolQA 发布版（13 题） |
|---|---|---|---|
| MiniMax-M3 | **0.502** | 0.235 | 0.769 |
| deepseek-v4-flash | **0.673** | 0.500（16/17 判定） | 0.846 |
| qwen3.8-max | — | 未判定 | 1.000（仅 4/13 题判定） |

同一轮里同模型跑官方原题的对照（同一 prompt/解析/计分）：

| 模型 | 官方 LitQA2 199 题 | 官方 ProtocolQA 108 题 | 判据 |
|---|---|---|---|
| MiniMax-M3 | **0.306**（199/199） | **0.504**（108/108） | 发布版 LitQA2 低于官方题 → 难度达标；发布版 ProtocolQA 高于官方题 → 偏易 |
| deepseek-v4-flash | **0.489**（178/199 判定） | **0.860**（107/108） | 两个发布版都与官方题持平（0.500 vs 0.489；0.846 vs 0.860） |

**主要观察：**

1. **LitQA2 `m3only` 落在官方难度档位。** MiniMax-M3 在 17 题上得 0.235、deepseek-v4-flash 得 0.500，
   对照同一轮的官方原题（M3 0.306、deepseek 0.489）基本重合——M3 略低于官方、deepseek 几乎相同。
   这与归档口径（2026-09-22 测到 M3 0.300 / 0.235、deepseek 0.412，官方采样 50 题 0.294 / 0.460）一致，
   属于可复现的结论。
2. **ProtocolQA `official` 只在稳定模型口径下达标。** deepseek-v4-flash 在 13 题上得 0.846，与官方
   108 题的 0.860 几乎相同；但 MiniMax-M3 得 0.769，明显高于它在官方 108 题上的 0.504。原因在
   筛选题的口径：这 13 题是按 M3 单遍作答的失败题筛出来的，而 **M3 的单题判定是噪声**（同一题两次
   作答的字母一致率只有 59–62%，官方题上也只有 35%），两遍独立重测（归档 0.769、本轮 0.769）都远高于
   筛选那一遍的 0.392。**结论：ProtocolQA 发布版目前只对 deepseek-v4-flash 是"官方难度"，对 M3 偏易。**
3. **认输行为的差异暴露了两类题的性质不同。** 官方 LitQA2 199 题上 MiniMax-M3 认输 49/199（25%），
   官方 ProtocolQA 108 题上只认输 4/108；而自产的两个题包一次认输都没有（0/30）。自包含题干把
   "想不起来"这一层删掉了，模型总是敢答——这是自产题与官方题最本质的风格差异，也是它们在
   M3 口径下更难对齐档位的原因。
4. **格式修复是承重的，不是修饰。** 若按官方 CI 原样解析（不接受 markdown 回复，`score_strict`）：
   M3 在 LitQA2 17 题上只有 0.118（修复后 0.235）、在官方 ProtocolQA 108 题上 0.439（修复后 0.504）。
   也就是说，不修格式的话 M3 有一半的分是被 `**Answer: C**` 这类写法吃掉的。
5. **稳定性：模型间差异远大于题目噪声。** 与归档那一遍相比，逐题字母一致率：M3 59%（LitQA2）/ 62%
   （ProtocolQA），deepseek 88% / 85%。聚合分上 M3 在官方 ProtocolQA 上三次分别为 0.425（归档首测）、
   0.449（归档复测）、0.504（本轮），deepseek 为 0.850（归档）/ 0.860（本轮）——稳定模型的总分可以
   直接引用，M3 的只能连同波动一起读。

## 评测方法

本轮评测使用本仓库实现的 **LAB-Bench 官方评测逻辑逐条复刻**（`scheurekabench/benchmark_creation/
solve_labbench.py`），prompt 与判分规则对照官方仓库 `Future-House/lab-bench` 的
`.github/promptfooconfig.yaml`（prompt 模板）与 `.github/assert.py`（解析与计分）实现：

- **Prompt**：`Q: {question}\n\nOptions:\nA) …\n…\nE) unsure\n\nAnswer:`。ProtocolQA 按官方
  `labbench/ProtocolQA/task.py` 的做法把 `protocol` 原文拼在题面前面（`input.question =
  self.protocol + input.question`），因此 ProtocolQA 是开卷、LitQA2 是闭卷。
- **解析**：`re.search(r"([A-Z])\)", output, re.DOTALL)`，取不到时回退到回复第一个词的首字母大写
  （与官方 `extract_answer` 一致）。
- **计分**：字母 == 正确项 → 1.0；字母 == 认输项 → 0.1；其余 → 0.0。
- **格式修复**：官方 prompt 要求只回一个字母；模型回 `**Answer: C**` 时官方解析器取不到字母、会把
  本意正确的答案判 0。本仓遇到无法解析成合法字母的回复时，会重问一次"只回一个字母"，两个分数都存
  （`score_strict` = 官方 CI 原样所得，`score` = 修复后，用于难度结论）。
- **无产出/失败不判 0**：网关故障（超时、503）时该题**留在未判定状态**，不写入缓存、不算答错，
  重跑该命令会自动补；只有模型真的回了字母才计分。这避免了把基础设施故障读成"模型不会"。
- **并行**：大集合按题目切片（`shard_labbench.py --split/--merge`），每个分片一个进程，卡住的调用
  只拖慢自己那一片。
- **没有 LLM 裁判**：全程是确定性解析 + 字母比对，没有主观评分环节，也没有裁判模型偏差问题。

**与官方 CI 的两处已知差异（都不影响本轮结论的方向，但如实记录）：**

1. **认输选项的文本**：官方 CI 生成的是 `Insufficient information to answer this question`，本仓复刻
   用的是 `unsure`。本轮沿用 `unsure`，是为了与归档的历史测量（本目录引用的官方基线数字）保持同一
   口径可比。
2. **不可答题（官方 199 题里 5 题 `ideal = "null"`）的判分**：官方 CI 把 `null` 当作一个普通选项，
   选中它按"答对理想项"计 1.0；本仓复刻按该字段的设计意图（"给出答案即视为幻觉"）只把**认输**判对。
   本轮 M3 在这 5 题里全部给出了具体选项、其中 1 题（#142）恰好选中 `null` 选项——即该差异对本轮
   M3 的官方 199 题总分影响为 1/199 ≈ 0.005。

### 本轮运行记录（2026-09-24）

- 模型面板 `MiniMax-M3` / `qwen3.8-max` / `deepseek-v4-flash`，经仓库根 `.env` 的网关调用；无 LLM
  裁判介入。
- 四个题集合计 **655 条已判定作答**（每题每模型一条），按题目分片并行（小集合 3 片、官方大集合 6 片），
  逐题日志在 `logs/`。网关不稳定期间，失败的题一律留作未判定并随后补跑，从不记 0 分；本轮因此没有
  "因故障而出现的错题"。全部未判定题共 32 道：deepseek-v4-flash 23 道（官方 LitQA2 21、官方
  ProtocolQA 1、LitQA2 发布版 1），qwen3.8-max 9 道（见注意事项 3）；表里按"已判定题数"标注。
- 期间修了两个链路缺陷（都在仓库主代码里，不属于本目录）：网关客户端对"某模型的 Anthropic 路由整轮
  失败"改为进程内粘性跳过（否则每个问题都要先付一轮读超时，实测 qwen3.8-max 单题因此从 25 秒降到
  2 秒）；评测汇总函数在某个模型一道题都没答上时不再除零崩溃。
- qwen3.8-max 的故障形态：OpenAI 兼容路由 503 `Service temporarily unavailable`、Anthropic 路由
  429 `upstream rate limit` / 读超时；同时段同网关的 MiniMax-M3 与 deepseek-v4-flash 可正常作答，
  因此这是该模型上游的问题，不是本次评测配置的问题。

## 注意事项

1. **发布前必须处理版权。** LitQA2 的 `key-passage` 与 ProtocolQA 的 `protocol` 是 Nature Aging /
   Nature Protocols 订阅刊正文的**逐字引用**（LitQA2 17 题合计约 2.4 万字符，ProtocolQA 每题 0.85–5.0 千字符）。
   对外公开发布需要取得授权或大幅裁短这两个字段；在此之前不应公开分发。题包其余部分（题干、选项、
   评分键）为自产内容。`baseline_official/` 里的官方数据集副本来自 HuggingFace `futurehouse/lab-bench`，
   许可 CC-BY-SA-4.0：可再分发，需署名并保持 share-alike。
2. **ProtocolQA 发布版的难度声明要按模型分开写。** 它对 `deepseek-v4-flash` 是官方难度（0.846 vs 官方
   0.857），对 `MiniMax-M3` 偏易（0.769 vs 官方 0.504）。若一定要一版对所有模型都落在官方档位，应按
   **稳定模型口径**（deepseek 答错、或多遍共识）重新筛选协议题，而不是沿用 M3 单遍失败的口径。
3. **qwen3.8-max 未纳入本轮完整评测。** 该模型上游在本轮窗口（约 11:30–17:30）内持续故障（OpenAI
   兼容路由 503 `Service temporarily unavailable`、Anthropic 路由 429 `upstream rate limit` / 读超时），
   300 余次调用只成功少量（ProtocolQA 发布版 4/13 题，全部答对；其余题集 0 题），因此表中它的分数
   不作数、不参与任何结论。端点恢复后用 `tools/fill_qwen.sh` 可以续跑：缓存按题续跑，已完成的题不会
   重问；同网关同时段的 MiniMax-M3 与 deepseek-v4-flash 作答正常，故障仅限该模型。
4. **单次采样。** 每个模型每道题只作答一次。聚合分可以引用，但**单题判定不要当结论**（M3 尤其如此，
   逐题一致率 59–62%）。要做逐题级的判断题（例如"某题是不是真的难"），需要多遍作答取共识。
5. **本数据集用于模型能力评测**，不是真实实验方案或医学建议；题目依据的论文 DOI 记录在每题
   `sources` 字段，可回溯到公开文献。
