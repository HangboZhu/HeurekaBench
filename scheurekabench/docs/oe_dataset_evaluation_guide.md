# OE 数据集评测指南（交接给 AI Review 系统）

> 适用范围：本仓库 `insights_to_questions.py --structured --qtype oe` 产出的任意
> `<paper_dir>/oe_questions.json`（对 MCQ 版 `mcq_questions.json` 同理，判分规则不同，见 §7）。
> 本文档是**通用规范**，与具体论文无关。

---

## 1. 一句话总结

每条数据 = **一道自包含的开放题**（`question`）+ **参考答案**（`answer`，不发给被测模型）+
**机器可执行的评分标准**（`rubric`：原子事实要点 `facts` + 1-5 分映射 `scoring_guide`）。
评测流程：被测模型只看 `question` 作答 → 评分模型（grader）对照 `answer` 与 `rubric.facts`
逐条判定覆盖情况 → 输出 1-5 总分。**全程不需要论文原文**（题目设计上已自包含）。

---

## 2. 该拿哪个文件：`<qtype>_questions_eval.json`（评测入口，推荐）

出题工作流会在每个题目目录自动生成一个**平铺的评测文件**：

```
<paper_dir>/oe_questions_eval.json      # OE 评测入口
<paper_dir>/mcq_questions_eval.json     # MCQ 同理（每项多一个 options 字段）
```

格式为**纯 JSON 数组**，每个元素只含三个（MCQ 四个）字段，**没有任何论文/insight 信息**：

```json
[
  {
    "question": "…自包含题干…",
    "answer": "…参考答案…",
    "rubric": { "facts": ["F1: …", "F2: …"], "scoring_guide": "…" }
  },
  …
]
```

- **题目身份 = 数组下标**（第 i 个元素即 Qi，i 从 1 起）。评测评测按顺序对齐作答即可；
  不要依赖（也拿不到）paper/insight 编号。
- 该文件由 `export_eval_questions.py` 生成，`insights_to_questions.py --structured` 出题后
  与 `difficulty_loop.py` 难度闭环定稿回写后**都会自动刷新**，与定稿永远同步；也可手动重生成：
  `python benchmark_creation/export_eval_questions.py <dir>/oe_questions.json --qtype oe`。
- 原始嵌套版 `<qtype>_questions.json`（见 §3）保留在磁盘上作为**溯源/审计副本**
  （哪条 insight、论文原文依据），**不要**把它发给被测系统。

---

## 3. 原始嵌套文件 `<qtype>_questions.json`（溯源用，评测不需要）

```json
{
  "<paper_id>": {
    "Insight1": { ...一条 insight 及其题目... },
    "Insight2": { ... },
    ...
  }
}
```

| 层级 | 含义 |
|---|---|
| `<paper_id>` | 论文目录名（一个文件通常只含一篇论文） |
| `InsightN` | 该论文经"辩论验证"后的第 N 条 insight（每条 insight 默认挂 2 道题） |

---

## 4. Insight 层字段

`InsightN` 对象包含以下字段：

| 字段 | 是什么 | 评测时怎么用 |
|---|---|---|
| `summary` | insight 摘要（论文的核心结论，1-3 句） | ❌ 不发给被测模型；仅供 grader/人工理解题目背景 |
| `how` | 该 insight 在论文中是如何被推导/验证的 | ❌ 不发给被测模型；背景参考 |
| `relevant` | 支撑该 insight 的**论文逐字引文** | ❌ 不发给被测模型；溯源审计用 |
| `oe_questions` | **题目列表**（本评测的核心，见 §5） | ✅ 逐题评测 |
| `oe_question` | 同样题目的 legacy 文本渲染（`**Question1:** …\n\n**Answer1:** …`） | 仅兼容旧解析正则；新系统**忽略此字段**，用 `oe_questions` |

---

## 5. 题目层字段（`oe_questions[i]`）——核心

| 字段 | 是什么 | 谁能看到 |
|---|---|---|
| **`question`** | **问题本体**。自包含：所有必要前提（体系、实体、定量观察）都写在题干里，答题者无需任何外部材料即可作答；题干 ≤ ~900 字符，只保留推理所需前提 | ✅ 被测模型的**唯一输入** |
| `question_type` | 难度阶梯标签（固定六类）：`extraction / comparison / causal / multi_hop / counterfactual / experimental`；每条 insight 的 Q1 为低阶（extraction/comparison）、Q2 为高阶（causal/multi_hop/counterfactual/experimental），全集构成难度梯度。旧数据可能缺此字段 | ❌ 被测模型不可见；✅ 结果分层分析用 |
| **`answer`** | 参考答案（ground truth），基于题干前提的正确完整回答 | ❌ 被测模型不可见；✅ grader 输入 |
| **`rubric.facts`** | **3–5 条（硬性上下界）**语义级核心事实，`["F1: …", "F2: …", …]`。每条 = 一个决定正确性的核心科学断言（推导产物、机制环节、由题干数字得出的结论）；判分为**语义覆盖**——改写/换序/替代推导均算覆盖，**不要求**按参考答案的措辞或粒度复现。设计上为"推导依赖"——只靠领域通识最多 PARTIAL | ❌ 被测模型不可见；✅ grader 输入 |
| **`rubric.scoring_guide`** | facts 覆盖情况 → 1-5 整数分的映射规则（各题自带，语义见 §6.2 缺省版） | ❌ 被测模型不可见；✅ grader 输入 |

即：**"问题" = `question`，"rubric" = `rubric`（`facts` 是逐条判分依据，`scoring_guide` 是汇总成 1-5 分的规则），`answer` 是 rubric 的依据原文。**

---

## 6. 评测协议

### 6.1 输入输出

1. **作答阶段**：对每题，把 `question`（原样、不增删）发给被测模型，收集自由文本回答。
   - 不要提供 `answer` / `rubric` / 论文 / 联网检索。
2. **评分阶段**：对每题，把 `question` + 被测模型回答 + `answer` + `rubric.facts` +
   `rubric.scoring_guide` 发给 grader 模型，要求其输出：
   - 每条 fact 一行：`F<n>: PRESENT | PARTIAL | MISSING | INCORRECT`
   - 最后一行总分：`<rating>1-5</rating>`

### 6.2 fact 判定标准

| 标签 | 定义 |
|---|---|
| `PRESENT` | 该事实被完整覆盖：语义一致，且在被测回答自身的推理/依据中得到实质支撑 |
| `PARTIAL` | 语义沾边但支撑薄弱：含糊其辞（"likely / for example / 一般而言"）、仅凭背景常识复述而无从题干前提推出的依据、或混在一堆未承诺的候选罗列里 |
| `MISSING` | 未提及 |
| `INCORRECT` | 与 GT 事实矛盾或陈述错误 |

补充规则：
- 只按 GT facts 判分；回答中**多余的额外信息不扣分**，除非与 GT 矛盾。
- 表述/语言/顺序差异不影响判定，只看语义覆盖。
- facts 之间同等重要，无主次之分。

### 6.3 总分（1-5）

缺省映射（各题 `scoring_guide` 与此语义一致，以各题自带文案为准）：

| 分 | 条件 |
|---|---|
| 5 | 全部 facts PRESENT，无 MISSING/INCORRECT |
| 4 | 多数 facts PRESENT，允许少量 MISSING，无 INCORRECT |
| 3 | 部分 facts PRESENT，至少一条 PARTIAL/MISSING，允许轻微矛盾 |
| 2 | 无任何 fact 完整 PRESENT，若干 PARTIAL |
| 1 | facts 缺失或被 contradicted；回答明显错误或弃答 |

### 6.4 数据集级指标建议

- 主指标：所有题目的**平均 rating**（1-5）。
- 辅助指标：facts 层面的覆盖统计（PRESENT/PARTIAL/MISSING/INCORRECT 计数占比）、
  满分题占比（rating=5 的比例，衡量题目区分度）、评分解析失败率（应 <5%）。

---

## 7. MCQ 数据集（`mcq_questions.json`）的差异

结构同上，但题目层字段为：`question`（题干）、`options`（`{"A": …, "B": …, "C": …, "D": …}` 四选项，
每项为"结论 + 理由"式陈述）、`answer`（键值，单选如 `"B"`，多选如 `"A,C"`）、
`rubric.correct_reasoning`（正确项推理链）与 `rubric.distractor_analysis`（逐干扰项误读分析）。
**判分不需要 LLM**：被测模型回答提取字母集合后与 `answer` 的字母集合做**精确匹配**；
可另报逐字母 partial recall/precision。

---

## 8. 端到端示例

**数据（`oe_questions[i]` 节选）**：

```json
{
  "question": "Engineered T-cell receptors used for tumor recognition operate under two
    opposing affinity constraints: … candidate A, whose affinity is far below what is
    needed … candidate B, whose affinity already sits at the upper edge of the tolerated
    range … The team proposes to apply the same glycosylation-reduction step to both
    candidates. … would the glycosylation-reduction step be equally advisable for the two
    candidates? Justify your assessment by considering both of the step's stated effects.",
  "answer": "No, the step is not equally advisable …",
  "rubric": {
    "facts": [
      "F1: …",
      "F2: …"
    ],
    "scoring_guide": "5 = all facts fully covered …; 1 = facts missing or contradicted."
  }
}
```

**grader 应答示例**：

```
F1: PRESENT
F2: PARTIAL
F3: MISSING
<rating>3</rating>
```

解析：逐行正则 `^F(\d+)\s*[:\-]\s*(PRESENT|PARTIAL|MISSING|INCORRECT)$`，
总分正则 `<rating>\s*([1-5])\s*</rating>`。解析失败应重试（≤3 次）而非记 0 分。

---

## 9. 注意事项（工程）

- **自包含约束**：题干不得引用任何外部材料（"the article/according to/Figure X" 等），
  评测系统无需也无法获取论文；若发现此类措辞应视为脏数据。
- **不泄露**：`answer`、`rubric`、`summary/how/relevant` 任何情况下不得进入被测模型的输入。
- **多题隔离**：同一模型的各题回答应独立收集（每题单独上下文），不得让模型看到其他题。
- **幂等审计**：建议落盘每题的原始回答与 grader 原始输出，便于复核与重评。
- 参考实现：本仓库 `benchmark_creation/solve_and_grade.py`（单模型评测）、
  `difficulty_loop.py`（难度闭环）与 `export_eval_questions.py`（评测文件导出）；
  G-Eval 协议原始定义见 `geval_prompts/eval_prompts.py`。
