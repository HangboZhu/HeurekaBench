---
name: protocolqa-bench-production
description: 从 Nature Protocols 论文批量生产 LAB-Bench ProtocolQA（排障题）题包，并用官方评测打分、把难度校准到官方档位。当用户提到「protocol-qa 题」「ProtocolQA」「出一批 protocol 题」「排障题」「Nature Protocols 论文出题」「把 protocol 抽成题」「出一批 lab-bench protocol」「用官方评测打分」「筛难题 / 难度校准」，或给出一批 protocol 论文 PDF 想批量出题时使用，即使用户没明说 LAB-Bench 或 skill。
---

# ProtocolQA 题包生产（Nature Protocols 论文 → 排障题）

一条链路，七步：

```
paper.pdf ─ extract_protocol.py ─▶ protocol.json ─ protocol_to_questions.py ─▶ protocolqa_questions.json
  ─ export_labbench.py --type protocolqa ─▶ protocolqa_pool_<批>.json（+ _eval sidecar 字母表）
  ─ solve_labbench.py（复刻官方判分）─▶ 四档难度子集 ─ 复核重跑 ─▶ 难度声明 ─▶ 生产报告
```

与姊妹 skill `litqa2-bench-production` 的三处本质区别：

| | LitQA2 | ProtocolQA |
|---|---|---|
| 生成单元 | insight（论文发现） | **协议步骤块**（Procedure 按论文小节切） |
| 答题方式 | 闭卷（题干自包含） | **开卷**：官方 harness 把 `protocol` 前置到题干 |
| 答案形态 | A–D 短选项 | **自由文本** `ideal` + 3 个干扰项（导出时洗成字母选项） |

信封与序列化复刻、官方判分复刻、四档筛选与复核方法论两边共用；实现细节见
`scheurekabench/PIPELINE.md` §5.5（导出/评测）与 §5.8（ProtocolQA 全链路）。

## 前置条件

| 项 | 值 |
|---|---|
| 路径锚点 | `REPO=/Users/mac/Documents/hunpo_work/HeurekaBench`。命令默认 cwd=`$REPO/scheurekabench/benchmark_creation/`（`data/labbench/...` 相对它成立）；**.agents/ 在仓库根**，skill 自己的脚本一律写 `$REPO/.agents/skills/...` 绝对路径，别在 benchmark_creation 里用相对路径找 |
| 批次标签 | 一个标签贯穿 bench / shard / retest / 报告命名，开工先定死、中途不要换。**定标签前先 `ls data/labbench` 查占用**（已有 `0923`、`0926`、`0926b`）；标签已被占用说明这批已经跑过 → 走「复核/复算已有批次」场景，别重跑（重跑会覆盖轮次目录，且按下标命中的旧缓存会让分数错位） |
| 工作目录 | `scheurekabench/benchmark_creation/`（流程命令都在此执行） |
| Python | 仓库根 `.venv/bin/python`（含 pymupdf/dotenv/httpx/openai/anthropic） |
| 网关 | 仓库根 `.env`：`BASE_URL` / `OPENAI_KEY` / `CLAUDE_KEY` / `MODEL_NAME`；出题模型可用 `QUESTION_MODEL` 覆盖 |
| 官方对照 | `data/labbench/ProtocolQA_full.json`（108 题）+ `_eval.json`；`export_labbench.py --check/--check-style` 以它为参照 |
| 输入 | 每篇一个 `<slug>/paper.pdf`，slug 小写短横线（`temi-msi`、`dopamine-surface`） |

## 1. 抽协议（PDF → 步骤块）

```bash
cd /Users/mac/Documents/hunpo_work/HeurekaBench/scheurekabench/benchmark_creation
PY=/Users/mac/Documents/hunpo_work/HeurekaBench/.venv/bin/python
BASE=<论文批目录，例 .../molintbench/protocal-0926-2>
mkdir -p $BASE/<slug> && cp $BASE/s41596-026-XXXXX.pdf $BASE/<slug>/paper.pdf   # 工作副本约定叫 paper.pdf
$PY extract_protocol.py --pdf $BASE/<slug>/paper.pdf --out $BASE/<slug> \
    --paper-id <slug> --doi https://doi.org/10.1038/s41596-026-XXXXX --dump-text
```

- 开工先 `ls -la $BASE`，把「PDF 文件名 → slug → DOI」列成表逐行核对（三篇以上别在循环里张冠李戴）。
  DOI 的推导规则：下载文件名本身就是 DOI 尾段——`s41596-026-01427-w.pdf` → `10.1038/s41596-026-01427-w`，
  `-w` / `-1` 之类后缀属于名字的一部分、要保留。
- 工作副本一旦改名 `paper.pdf`，文件名里的 DOI 就没了，**必须显式 `--doi`**。
- 纯版式解析、不调 LLM，秒级；输出 `protocol.json` + `procedure_clean.txt`（人眼复核用）。
- 参数：`--min-block-steps/--min-block-chars`（小块并入邻居）、`--max-block-chars`（按步骤边界切，官方上限 14188）。

**抽取失败 / 断层怎么查**（报错时不会生成任何产物，"通读 procedure_clean.txt"这条路走不通，只能自己 dump）：

`find_procedure` 有四道闸门，任何一道不过都报同一句 `ERROR: no Procedure section found`：
① 该行被 `is_heading` 判为标题（字号 ≥ 正文 + 0.5pt；或字号 ≥ 正文且字体名含
`bold`/`black`/`heavy`/`semibold`——后一条要求整行 ≤120 字符）；
② 文本匹配**大小写敏感**的三种写法之一（`Procedure N:` / 裸 `Procedure N` / 裸 `Procedure`）；
③ 标题后**第一条步骤行**必须以 `1.` 开头（步骤行 = `^数字\.$` 或 `^数字\. 内容`；中间容许 ≤8 行前言，
`window` 硬编码）——`1)`、`Step 1.` 这类编号在这一道就失败；
④ 窗口内步骤数 ≥ 10（硬编码，无 CLI 开关），窗口止于 `Timing` / `Troubleshooting` /
`Anticipated results` / `References` 等 `END_HEADINGS`。先按闸门定位，再决定是修抽取器还是
"这篇不在链路范围内（综述/扫描件/无编号步骤）——换论文，别硬凑"。

```bash
# 用抽取器自己的眼睛看：page_lines 已去掉页眉/图注，is_heading 的判定直接打出来
$PY - <<'EOF'
import sys; sys.path.insert(0, ".")
import fitz, re
import extract_protocol as ep
doc = fitz.open("<paper.pdf>")
for pno in range(doc.page_count):
    lines, _ = ep.page_lines(doc[pno])
    body = ep.body_size(lines)
    for line in lines:
        if re.search(r"procedur", line["text"], re.I):
            print(f"p{pno+1} size={line['size']:.2f} body={body:.2f} "
                  f"heading={ep.is_heading(line, body)} {line['text'][:80]!r}")
EOF
```

块内**步骤号断层**（12 → 33）先看三件事再下结论：接缝处有没有 `BOX N` 标题；`protocol.json` 的
`extraction.dropped_lines` 里该页的 `table-or-figure-content` / `figure-label` 是不是大量丢弃
（盒体常是小字号，会被当表格内容丢掉）；对照 PDF 原文，缺掉的步骤号（13–32）到底属于盒子自己的编号，
还是本该在主流程里。**分流**：缺号属于盒子 → 插入结构（坑表 3a/3b），抽取器该搬没搬，修完强制回归；
缺号本该在主流程却被丢弃统计吃掉 → 字号过滤误伤，同样要修；论文原文就是 12 接 33（多段各自编号，
坑表第 4 条）→ 合法，不改代码，但在报告里记一笔。

**出题前的抽取检查清单**：

- 通读 `procedure_clean.txt`：无图注/表格/坐标轴污染；`CRITICAL STEP`、`TIMING`、`PAUSE POINT` 按原文保留。
- 块内步骤号连续，断层按上面三件事查清后再出题。
- `numbered=false` 的块（选项式 procedure，如 Cu 篇 Procedure 3）出题端默认跳过，属预期。
- `troubleshooting` 条数与表行数的差额来自两种正常折叠：空 Problem 行续上一行的多原因、无步号行无法映射
  （记在 `troubleshooting_unmatched`）。差额异常大时核对原始表格。
- 论文里有 BOX / 断层时，顺带复核排障表 `Box N` 行的归块：它在 `protocol.json` 里挂在了哪一块，其
  problem 语义是否属于那块（0926 ipsc 篇曾整行挂错块）。

**改过 `extract_protocol.py` 就必须回归**（回归通过前不要进入第 2 步——块文本会逐字成为答题者看到的
`protocol`）：对已有批次（`molintbench/protocal-0923/`、`-0926/` 里带
`protocol.json` 的目录）重跑同名命令，新旧 `protocol.json` 必须**逐字段一致**（blocks + troubleshooting
映射 + extraction 统计）。这是"改动没伤老论文"的唯一凭据。

## 2. 出题（每块 2 题 × 3 轮）

```bash
for r in 1 2 3; do for slug in <slug1> <slug2> <slug3>; do
  mkdir -p $BASE/pool/$slug-r$r && cp $BASE/$slug/protocol.json $BASE/pool/$slug-r$r/
  $PY protocol_to_questions.py --protocol_json_path $BASE/pool/$slug-r$r/protocol.json \
      --model_call gateway --per-block 2 > $BASE/pool/$slug-r$r/gen.log 2>&1 &
done; wait; done          # 三篇并发、轮次串行；整段放一次 Bash 调用里后台跑（LLM 调用会超过默认超时）
```

- **单次调用只出 2 题**：单次输出超过约 20KB 会被传输层截尾、JSON 损坏；要更多候选就**加轮次**，别加
  `--per-block`（2 是实际上限）。
- 单块重跑：`--blocks Block3,Block7`；确实要给未编号块出题才加 `--include-unnumbered`。
- 每轮目录三件产物：`protocolqa_questions.json`（canonical，按块分组 + rubric 溯源）、
  `protocolqa_questions_eval.json`（扁平，身份=下标；下文一律简称 `_eval.json`）、`protocolqa_questions.txt`
  （人读）。字段级校验认的是 `protocolqa_questions_eval.json` 这个全名。
- 生成规则写在 `prompts/insight2question_rubric_prompts.py::_PROTOCOLQA_TEMPLATE`：题干是排障体（≤479
  字符、以 `?` 结尾）、`ideal` ≤220 字符且点名步骤号、恰好 3 个干扰项且是**三种不同的失败模式**、
  补救措施必须由块内原文支撑（防编造）。

**检查（读 gen.log 的 `mix`）**：`distractors:3` 占绝对多数（4 个也合法；2 个会被导出跳过）；
`ideal-without-step-reference` 应为 0；`blocks-without-questions` 偶发 1 个属正常。
`"reference the source article"` 告警可能是 `cryosection`/`tissue sections` 的子串误报——先看具体命中词。

## 3. 导出 + 两层自检

```bash
$PY export_labbench.py --type protocolqa --base_dir $BASE/pool/*/ \
    --out data/labbench/protocolqa_pool_<批>.json --check --check-style
```

- 导出只保留能成为官方条目的题，跳过项会打印计数：
  `cites-step-outside-the-protocol`（补救措施点名了**本块没有的步骤号**——不可答题，必须跳）、
  `distractor-count-N`（官方 3–6）、`missing-ideal`、`duplicate-distractors`、
  `ideal-duplicated-in-distractors`、`empty-protocol`。
- `--check`＝信封层（8 键、键序、`indent=1`、无尾随换行），**必须过**；`--check-style`＝条目形态层，也应当过。
- 若形态层只剩个别越界条目（例如某条 `ideal` 比官方上限长几字符）：回到**源轮次目录**把那条撤掉
  （canonical/eval/txt 三件同步），再重导——与导出器跳过不合形态条目同口径；**不要手改 gold 内容**。

官方形态实测范围（`--check-style` 用的就是这张表）：

| 维度 | 官方 108 题 | 自产应有 |
|---|---|---|
| 题干 | 95–479 字符、以 `?` 结尾 | 同（超上限会被判违反） |
| `ideal` | 16–220 字符、单行无连续空格 | 同（**≤220 是硬线**） |
| 干扰项 | 3–6 个（众数 3） | 3–4 |
| `protocol` | 569–14188 字符、必须多行 | 同（多行是硬检查） |
| `protocol` 步数 | 7–69 | 1–29（**只打印不拦**：按小节切得更细；1 步那条是论文自身的巨型 step，合法，别撤题） |
| 步骤点名率 | `ideal` 86%、干扰项 83% | 报比例、不逐条强制（自产通常 ~100%） |

被判"违反"的只有：题干/`ideal` 超长或含换行/连续空格、题干不以 `?` 结尾、干扰项个数不在官方集合内、
`protocol` 长度出界或单行。`protocol` 步数与步骤点名率只打印。（这张表的"步数"是**单条目内部**的步数，
与 §1 抽取失败闸门④的"窗口内 ≥10 步"是两码事。）

信封里的 `subtask` 用自有值 `protocolqa-v1-heureka`、`canary` 每次新 GUID——**绝不冒用官方值**。

## 4. 官方评测打分（复刻官方 harness）

```bash
B=data/labbench/protocolqa_pool_<批>
$PY shard_labbench.py --bench $B.json --shards 6 --split
for s in 0 1 2 3 4 5; do GATEWAY_TIMEOUT=90 $PY solve_labbench.py \
    data/labbench/shard/$(basename $B)_s$s.json --solver MiniMax-M3,deepseek-v4-flash \
    > /tmp/$(basename $B)_s$s.log 2>&1 & done; wait      # 日志带 bench 名，别和基线互相覆盖
$PY shard_labbench.py --bench $B.json --shards 6 --merge --models MiniMax-M3,deepseek-v4-flash
```

- 判分＝官方口径：`Q: {protocol}\n\n{question}\n\nOptions:…\n\nAnswer:`，优先 `([A-Z])\)` 解析，
  1.0 正确 / 0.1 认输 / 0.0 错误；模型答成 `**Answer: C**` 时脚本带原答案追问一次（`score` 修复后分、
  `score_strict`＝官方 CI 原样分）。
- **同日官方基线**（难度对照的锚）：复制官方 108 题成一个新文件名，缓存按文件名分家 → 得到"今天这两
  个模型在官方题上是什么水平"：
  ```bash
  cp data/labbench/ProtocolQA_full.json data/labbench/ProtocolQA_full_<批>.json
  cp data/labbench/ProtocolQA_full_eval.json data/labbench/ProtocolQA_full_<批>_eval.json
  # 再把上面三条（split → 6 路 solve → merge）原样跑一遍，把 B 换成 ProtocolQA_full_<批>
  ```
- **网关故障不是"答错"**：空回复留未判定、不入缓存，重跑补齐。补跑用
  `scripts/fill_gaps.py`（跳过该模型已知必死的 anthropic 路由，新进程因此少付一轮读超时；脚本按自身
  位置定位 benchmark_creation，可从任意 cwd 调用）：
  ```bash
  GATEWAY_TIMEOUT=40 $PY $REPO/.agents/skills/protocolqa-bench-production/scripts/fill_gaps.py \
      data/labbench/shard/$(basename $B)_s3.json --solver deepseek-v4-flash
  ```
  反复跑到覆盖率不动为止（~98% 即可收）；仍判不了的记 UNJUDGED，**从子集外剔除**（缺答案 ≠ 难题）。

## 5. 四档难度筛选（`--judge-only` 复用缓存）

顺序要紧（先单模型那步，避免 `_filtered` 被覆盖）。四条命令都带 `--dedupe-similarity 0.75`：按题干文本
去掉跨轮次的近重复题；0.75 是本仓惯例阈值，实测跨轮相似度多在 0.40–0.44，远低于它，正常不会误删。

```bash
# a) 稳定模型失败口径 → 改名 _dsonly
$PY solve_labbench.py $B.json --solver deepseek-v4-flash --drop-all-correct --judge-only \
    --dedupe-similarity 0.75 && mv ${B}_filtered.json ${B}_dsonly.json \
    && mv ${B}_filtered_eval.json ${B}_dsonly_eval.json
# b) 面板：任一没答对 → _filtered
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --drop-all-correct \
    --judge-only --dedupe-similarity 0.75
# c) 面板全错 → _hard
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --keep-hard \
    --judge-only --dedupe-similarity 0.75
# d) 面板均分带 → _official
$PY solve_labbench.py $B.json --solver MiniMax-M3,deepseek-v4-flash --keep-band 0.4 0.6 \
    --band-suffix official --judge-only --dedupe-similarity 0.75
```

- 四档的取舍口径：
  - `--keep-band` 算的是**面板均分**（两模型平均），不是单模型分数；0.4–0.6 挑的是"对两个模型都有区分度"
    的题。同一天的官方原题自己面板均分 ≈0.6（0.36 与 0.83 的平均），所以这条带只保证"两模型都不至于
    全对/全错"，**不能替代**"落进官方档位"的判断——那要按 solver 逐一对同日官方基线（§6）。
  - `--drop-all-correct` / `--keep-hard` 的 `UNJUDGED` 行是**自动**排除（`write_subset` 里完成），
    把打印出的下标抄进报告即可，不要手工补题。
- 产出规模随池子难度浮动：0926b（186 候选）`_filtered` 32%、`_dsonly` 20%、`_official` 19%、`_hard` 14%；
  0923/0926 更严（`_official` 10–15%、`_hard` 4%）。规划按 **10×N** 备候选（官方档位）、**25×N**（硬核集），
  偏保守但跨批安全；池子明显偏易时可以先用 5×N 估。

## 6. 复核重跑 + 难度声明

```bash
SKILL=$REPO/.agents/skills/protocolqa-bench-production
for n in dsonly filtered official hard; do
  cp ${B}_$n.json ${B}_${n}_retest.json; cp ${B}_${n}_eval.json ${B}_${n}_retest_eval.json
  GATEWAY_TIMEOUT=90 $PY solve_labbench.py ${B}_${n}_retest.json --solver MiniMax-M3,deepseek-v4-flash \
      > /tmp/retest_<批>_$n.log 2>&1 &
done; wait
$PY $SKILL/scripts/subset_retest_stats.py --pool $B.json --models MiniMax-M3,deepseek-v4-flash
```

- 子集换新 bench 名重跑＝缓存分家，才有"第二遍"可比（同 bench 重跑只会命中缓存）。
- **难度声明按 solver 分开写**，各自对比同日官方基线：
  - **不能以 M3 单模型口径选题**：它的逐题字母一致率只有 31–39%（在官方原题上一样），按它单遍失败筛
    子集会向池均值回归（实测 `_hard` 挑选遍 0.004 → 重跑遍 0.504）。**面板里 M3 照常陪跑**
    （`_filtered`/`_hard`/`_official` 都是面板口径），但结论——尤其"这批够不够难"——看 deepseek。
  - **deepseek-v4-flash 是锚**：一致率 86–93%，子集重跑分与挑选遍接近；`_dsonly` 就是它的单模型口径子集。
- 重跑那遍也会有网关缺口：统计脚本会把每条的 `n=` 打出来，**写报告必须带 n**（例：`_hard` 的
  deepseek 只有 22/26 可比，别按 26 报分）。
- 官方基线同模型参考：ProtocolQA 108 题 M3 ≈ **0.36–0.45**（三次重测漂移 ~0.09）、deepseek ≈ **0.83–0.85**。
  "落进官方档位"＝自产题在该模型上的分数与同日官方基线接近，而不是套用某个绝对阈值。

## 7. 写生产报告

`scheurekabench/reports/<YYYY-MM-DD>-protocolqa-<批>-batch.md`，章节照 0926b 报告：
结论摘要 → 抽取表（论文/DOI/页/步数/块数/块字符/排障映射）→ 抽取器改动与回归 → 出题统计（题型配比、
告警）→ 导出形态对照（vs 官方 108 题）→ 评测（池 + 同日官方基线 + 网关降级处理）→ 四档表（挑选遍/
重跑遍/一致率/与官方对照）→ 与前几批对照 → 产物清单 → 复现命令 → 遗留（**版权**：`protocol` 是期刊
正文逐字引用，公开发布需授权或裁短）。

## 场景：复核 / 复算已有批次（不重跑）

产物齐备（pool、四档、`_retest`、缓存都在）时**不要按 §4–§6 盲跑**：同 bench 重跑只会命中缓存，拿不到
"第二遍"。正确顺序：

1. 核对池文件的条目数与 mtime——判断它是不是被重新生成过（重新生成过就必须按"缓存必须与子集同版本"
   移走对应的 `labbench_solved_<bench>_<model>.json` 再评测，否则分数错位）。
2. 四档子集与 retest 缓存是否成对齐全（`_<suffix>.json` + `_eval` + `..._retest_<model>.json`）。
3. 用 `--judge-only` + `scripts/subset_retest_stats.py` **零 API 调用**重算全部分数。
4. 与旧报告逐节核对数字；不一致先查证据（哪份文件被改过），再决定重跑哪一步。

## 坑表：抽取器会遇到的六种结构

| # | 现象 | 原因 / 处理 |
|---|---|---|
| 1 | `ERROR: no Procedure section found` | 按 §1 的四道闸门 dump 定位。已知成因：裸 `Procedure N` 标题（无冒号，11.25pt）而 Outline/Materials 用正文尺寸重复同样字样（0926b 多巴胺篇，已支持）；标题写法超出三种正则（全大写、`Procedure:` 无编号、`1)` 式步骤号）；该文根本没有 Procedure 段（综述/扫描件）→ 换论文，别硬凑。 |
| 2 | 标题认出来了但窗口不对 | 候选按"带冒号 > 裸号 > 裸 `Procedure`"的优先级排序，且标题常在多处重复（Outline p3/p5、Materials 小标题）；判据是"标题后第一条步骤行必须是 `1.`（中间容许 ≤8 行前言）"，不是标题文字本身。 |
| 3a | 断层（12 → 33），后续步骤挂在 `BOX N` 标题下 | 盒**自带编号**时（0926 ipsc 篇 BOX 1，盒内 1–12）：把盒段整体后移成独立块，主流程重新连续。 |
| 3b | 同上，但盒里**没有编号步骤** | TEMI 篇 BOX 2（纯参数说明）也要后移，否则主流程步骤落在盒标题下、正文以表格碎片开头；且紧邻恢复步骤的标题归恢复后的章节，不跟盒子走。 |
| 3c | 断层仍在（盒后第一步 ≠ 上一步 + 1） | `lift_box_inserts` 只认 `last_main + 1` 这一种续写点，认不出会**整体返回、后续盒子也不再处理**。按 §1 的三件事分流：漏覆盖 → 放宽续写判据（**保留 `end > start` 保护**——盒内首步恰为 `last_main+1` 时那一步是空搬迁，这个死循环边界 2026-09-27 已修）并回归；论文原样编号 → 不改代码。 |
| 4 | 多段 Procedure 各自从 1 编号，块 `step_range` 重复 | 合法结构（多巴胺篇 1–108 + 1–75；TEMI 篇 1–70 + 1–8 + 1–35）。**按原文保留、绝不重编号**（论文交叉引用靠它）。 |
| 5 | 排障表行挂错 Procedure | 表用 `Procedure N` 单独一行分段、行内只有裸步号时，标签要向下延续才能落到正确的 Procedure（0926b 多巴胺篇：否则 "BMDCs die" 会挂到 TEM 成像块）。 |
| 6 | 单个块只有 1 步、但正文上万字符 | 论文自身的巨型 step（胚样篇 step 16 含选项 A/B 与 (i)–(xix) 子步骤，11,327 字符）。合法；该块 `protocol_steps` 会低于官方下限（7），`--check-style` 标出来但不拦。 |

排障表还有两种**正常**折叠：空 Problem 行＝同一问题的另一条原因/方案（并入上一条）；无步号行无法映射
（进 `troubleshooting_unmatched`）。

**0927b 的补充（三篇材料化学合成协议，抽取器零改动通过）**：PCL 二维血小板（多段 `Procedure` 无冒号）、
盐辅助 1T′-TMDC、剪切流纳米片复合膜。两个记录在案的正常现象，别当 bug：BOX **自身的小标题**会把盒子
切成碎块（PCL 篇 BOX 2 → `BOX 2` + `Platelet dispersions`，两块都 `numbered=false`、不出题）；排障表
里的**复合步号标签**（`25 or Box 1, Step 4`）不映射，只是让该条补救注释对出题模型不可见。另外块
`title` 在小块并入邻块时会堆叠（`_join_titles`），只进内部溯源、**不进导出信封**。

## 坑表：网关

| 现象 | 处理 |
|---|---|
| 单题长时间零推进 | 写超时链路是 3 次尝试 × 2 路由 × `GATEWAY_TIMEOUT`；批量跑设 60–90。 |
| `deepseek-v4-flash` 每次都先超时一轮 | 该模型 anthropic 路由必死（`utils/llm_gateway.py` 里有记录），**每个新进程**付一轮读超时。补跑用 `scripts/fill_gaps.py` 直接跳过它。 |
| 大量 `503 Service temporarily unavailable` | 上游抖动，与 prompt 长度无关。空回复**不计分不入缓存**；6 路切片 + 反复补跑，覆盖到 98% 上下即可收，残题记 UNJUDGED。 |
| 子集内容变了分数错位 | 缓存只按下标命中、不校验题干：任何子集文件重生成后，其 `labbench_solved_<bench>_<model>.json` 必须先移走。 |
| 同一目录并发跑两批，`--merge` 报 `coverage` 大于题库条目数 | `shard/manifest.json` 是全目录**共享**的一份，另一批的 `--split` 会覆盖它，`--merge` 于是按别人的 bounds 回填、**每片错位**（实测：同目录里 108 题的基线清单覆盖了 102 题池子的，每片错位 1 格，75 条答案写到了别的题上）。`shard_labbench.resolve_bounds` 已修为"只认归属本 bench 的 manifest，否则按本 bench 的切片文件重建 bounds 并告警"；**切片缓存本身是正确的**，按正确 bounds 重建主缓存即可（样例：`molintbench/protocal-0927-2/rebuild_cache.py`）。根治办法是别在同一目录并发跑两批评测。 |

## 验收清单

- [ ] 抽取：`warnings` 只有 `numbered=false` 一类；块内步骤号连续；**改过抽取器**的话，老论文回归逐字段
      一致（没改就记 N/A，别写成"已回归"）。
- [ ] 出题：9 个（或 3N 个）轮次无解析失败、无 stray-block；`distractors:3` 占多数。
- [ ] 导出：`--check` 通过、`--check-style` 通过；跳过项逐类解释得清。
- [ ] 字段级：`validate_question_pack.py <轮次>/protocolqa_questions_eval.json --qtype protocolqa` 过；
      导出 bench 的步骤引用检查 0 违规（`validate_question_pack.py <bench> --bench <bench> --qtype protocolqa`
      只看 `cites step` 行——同文件当 pack 会报字段级 HARD，那是信封键 ≠ canonical 键，预期）。
- [ ] 评测：池子与同日官方基线两套分数齐；UNJUDGED 数量明示且排除在子集外。
- [ ] 复核：四档子集都做了换名重跑；一致率**带 n=**写入报告，难度声明按 solver 分开。
- [ ] 命名与产物：批次标签全文统一（bench/shard/retest/报告）；产物确认在 `data/labbench/`
      （按 AGENTS.md 不进 git）。
- [ ] 报告落盘（`reports/<日期>-protocolqa-<批>-batch.md`）。

## 相关文档与文件

- `scheurekabench/PIPELINE.md` §5.5（LAB-Bench 导出与官方评测）、§5.8（ProtocolQA 全链路 + 四个坑 + 基线表）
- 生产报告（按批次读，含实测数字与完整复现命令）：
  `reports/2026-09-23-protocolqa-labbench-export.md`（首批 136 题）、
  `reports/2026-09-26-protocolqa-0926-batch.md`（BOX 修复）、
  `reports/2026-09-27-protocolqa-0926b-batch.md`（多段 Procedure 三坑 + 首次不筛即达官方档位）、
  `reports/2026-09-27-protocolqa-0927b-batch.md`（材料化学协议零改动 + 切片清单串号修复）
- 代码：`benchmark_creation/{extract_protocol,protocol_to_questions,export_labbench,solve_labbench,shard_labbench,validate_question_pack}.py`
- 本 skill 的脚本：`scripts/fill_gaps.py`（评测缺口补跑）、`scripts/subset_retest_stats.py`（子集挑选遍/重跑遍对照）
