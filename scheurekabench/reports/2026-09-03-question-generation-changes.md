# 出题环节变化报告（TCR-T-paper 实测驱动）

日期：2026-09-02 ~ 2026-09-03
范围：`scheurekabench/benchmark_creation/` 出题链路（debate 模式产出的 insights → 题目）
实测论文：`molintbench/TCR-T-paper`（TCR-T 综述，10 条经辩论验证的 insight，MCQ/OE 各 20 题）

---

## 0. 背景与问题

初版题目（`insights_to_questions.py --structured`）经双模型盲测（solver 只见题目、无论文）暴露两个问题：

1. **依赖原文**：40 题中 6 题含 "According to…"、"described in…"、"section" 等指向论文的措辞，
   另有 "The dataset relates…" 这类预设答题者持有数据集/论文的问法——不满足"直接拿题就能答"的目标；
2. **难度不足**：MiniMax-M3 与 glm-5.3-flash 双双 MCQ 20/20、OE 均分 4.80/5——强模型靠领域
   通识 + 选项语感即可满分，题目没有筛选力。

---

## 1. 变化一：题目自包含（去掉对论文/数据集的依赖）

**文件**：`prompts/insight2question_rubric_prompts.py`、`insights_to_questions.py`

- 考试设定改为"答题者只能看到题目本身——无论文、无数据集、无图表"；
- 所有必要上下文（体系、干预、实体、定量观察、实验背景）必须以**中性事实前提**写进题干
  （例："In patients with advanced synovial sarcoma treated with autologous T cells engineered to
  express an NY-ESO-1-directed TCR, 50% showed objective responses…"）；
- 题干禁词硬性清单：the article/paper/review/study/text/passage/material/authors、according to、
  as described、described in、mentioned、reported in、Figure/Table/Section X 及任何语言的等价说法；
  不得预设外部材料（"the dataset shows…"）；
- **机器防线**：新增 `SOURCE_LEAK_RE` 正则，生成后逐题扫描；发现泄漏 → 携带违规清单自动
  重新生成一次 → 保留泄漏更少的版本 → 仍有残留则打印 WARNING 交人工清理。

**效果**：宽严两套扫描下，40 题 → **0 泄漏**；问法全部改为"给定这些观察 → 哪个结论成立"的
独立推理题。

---

## 2. 变化二：MCQ 选项结构升级（防语感排除法）

**文件**：`prompts/insight2question_rubric_prompts.py`（MCQ_RUBRIC_PROMPT）

- 每个选项必须是"**结论 + 理由**"组合（`<conclusion> because <reasoning>`）；一个选项正确
  当且仅当**两部分都对**；
- 干扰项强制混合失败模式：结论对 + 理由错 / 结论错 + 理由貌似合理 / 命题为真但不回答所问；
- **难度硬性要求**：正确项必须需要组合题干 ≥2 个前提（或链式使用定量观察）才能确定；
  每个干扰项在"朴素单前提阅读"下必须貌似可信（不仔细推理的答题者应觉得至少两个选项都对）；
- rubric 同步升级：`correct_reasoning` 须写全结论 + 推理链（用到哪些前提、如何组合）；
  `distractor_analysis` 须指出每个干扰项错在哪部分（结论/理由/两者）及对应的朴素误读。

---

## 3. 变化三：OE 多步推导与"推导依赖"采分点

**文件**：`prompts/insight2question_rubric_prompts.py`（OE_RUBRIC_PROMPT）

- 每题必须**链式组合 ≥2 个题干前提**（或对题干给定数字做推算），禁止单前提直读、
  禁止教科书知识可直达；
- insight 中的定量观察必须写进题干，并让答案依赖这些数字的组合；
- 参考答案必须"推导依赖"：只靠领域通识、不仔细组合前提的答题者最多写出部分答案；
- `rubric.facts` 只收录**推导产物**（含由题干数字算出的量、仅由前提组合才能得出的结论），
  纯通识复述不作为满分采分点 → 知识型回答在 facts 层面只能拿 PARTIAL。

---

## 4. 变化四：难度闭环（自动化难度调优）

**新文件**：`benchmark_creation/difficulty_loop.py`（文档见 PIPELINE.md 5.2）

流程：参考 solver（无原文）做题 → 判分 → **答对的题（MCQ 正确 / OE ≥ `--keep-rating` 分）
带 solver 表现反馈逐 insight 重新生成得更难**；已有区分度的题逐字保留 → 重测 → 循环直到
整体得分落入目标区间（MCQ 命中率 50-70% / OE 均分 2.5-3.5）或轮数封顶。

工程细节：
- **逐 insight 小 prompt 重生成**（含该题 solver 的实际推理摘录，要求新题"让这种推理风格
  不再够用"），避免大 prompt 解析失败；
- 每轮题目快照 `<qtype>_questions_round<N>.json`，结束回写**最接近目标**的轮次为正式文件
  （含 legacy 文本渲染与 .txt；距离平分时取强化最深的轮次）；
- 重生成题仍过自包含检查，泄漏的题自动回退上一轮版本；解析失败落盘原始输出供排查；
- 重生成"零变化"检测，防止空转循环。

---

## 5. 变化五：评测基建（出题质量的度量衡）

**新文件**：`benchmark_creation/solve_and_grade.py`

- solver 侧：任意网关模型对 MCQ/OE 盲做（只见题目），结果断点续跑；
- MCQ 判分：字母集合精确匹配 + 逐字母 partial credit（支持 "ANSWER: A,C" 与裸字母回答）；
- OE 判分：LLM grader（如 gpt-5.6-luna）按每题 rubric facts 逐条标注
  PRESENT/PARTIAL/MISSING/INCORRECT（G-Eval 协议）+ 1-5 总分；
- 输出每题作答原文、grader 原始输出，全程可审计。

---

## 6. 量化效果（TCR-T-paper 实测）

### 自包含
| | 初版 | 定稿 |
|---|---|---|
| 引用原文措辞（严格扫描） | 6/40 | **0/40** |
| 宽泛扫描（含 the dataset / the above 等） | 更多 | **0/40** |

### 难度（solver 无原文盲测）
| 题型 | solver | 初版 | 定稿 |
|---|---|---|---|
| MCQ 20 题 | MiniMax-M3 | 20/20 | **19/20（95%）** |
| MCQ 20 题 | glm-5.3-flash | 20/20 | 20/20（100%） |
| OE 20 题 | MiniMax-M3 | 4.80/5 | **3.85/5** |
| OE 20 题 | glm-5.3-flash | 4.80/5 | 4.35/5 |

### OE 难度曲线（标尺 = MiniMax-M3，闭环 6 轮 + 终测）
4.80 → 4.65 → 4.45 → 4.10 → 4.45 → 4.00 →（复测 3.85）
定稿分布：2分×2、3分×4、4分×9、5分×5；facts 层面 15 PARTIAL / 9 MISSING / 5 INCORRECT
（初版：80 PRESENT / 2 PARTIAL / 1 MISSING，几乎全满分）。

---

## 7. 结论与遗留问题

1. **OE 闭环有效且已接近目标**（3.85 vs 目标 ≤3.5，且单次测量噪声约 ±0.2）；
2. **MCQ 存在天花板**：累计 5 轮强化后 MiniMax-M3 才掉 1 题，glm-5.3-flash 仍全对——
   成熟领域知识的四选一题对强模型筛选力有限，建议 MCQ 定位为"基础理解检查"；
3. **根因在内容而非措辞**：综述论文的知识在强模型训练语料内；措辞加固的收益已到噪声底。
   真正"不读原文就不知道"的信息来自原创研究论文的专有事实（具体数字、专有数据集），
   建议难度区分度主要押在原创研究类论文 + OE 严格 rubric 评分上；
4. **可选后续**：用更强 solver（如 gpt-5.6-luna）做闭环标尺再压几轮；或提高 grader 严格度。

---

## 附：文件变更清单

| 文件 | 变更 |
|---|---|
| `prompts/insight2question_rubric_prompts.py` | 自包含硬性规则；MCQ 结论+理由选项结构与难度要求；OE 多步推导与推导依赖 facts |
| `insights_to_questions.py` | `SOURCE_LEAK_RE` 泄漏检测 + 带反馈自动重试；生成调用抽为 `generate_raw` |
| `difficulty_loop.py`（新增） | 难度闭环：solve → grade → 逐 insight 重生成 → 循环至目标区间；快照/回写/回退机制 |
| `solve_and_grade.py`（新增） | 盲测评测器：MCQ 字母匹配判分、OE rubric facts G-Eval 判分，断点续跑 |
| `PIPELINE.md` | 5.1 自包含规则说明；新增 5.2 难度闭环文档 |
