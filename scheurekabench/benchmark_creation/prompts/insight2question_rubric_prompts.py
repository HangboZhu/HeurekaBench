"""Structured question-generation prompts (Question + Answer + grading Rubric).

Used by insights_to_questions.py --structured: unlike the legacy text-format
prompts, these ask the LLM to return strict JSON in which every question
carries an explicit grading rubric, so the reference answer does not have to
be re-decomposed into atomic facts by the LLM judge at evaluation time.

Rubric design mirrors the G-Eval protocol in geval_prompts/eval_prompts.py:
OE rubrics are atomic, verifiable facts (PRESENT/PARTIAL/MISSING/INCORRECT
checkable) plus a 1-5 scoring guide; MCQ rubrics explain the keyed answer and
every distractor.

Both texts are built by mcq_rubric_prompt()/oe_rubric_prompt(), whose
`per_insight` argument sets how many questions each insight gets (default 2 =
the original behaviour). With a single question per insight there is no
two-tier split to fall back on, so that question is required to be the higher
tier one. The module-level MCQ_RUBRIC_PROMPT / OE_RUBRIC_PROMPT keep the
two-question text for existing callers.
"""

_MCQ_RUBRIC_TEMPLATE = """
I am designing assignments for my PhD students on scientific data analysis. The assignment is based on a published scientific article presenting research findings.
I want the PhDs to reason through observations and derive insights similar to those presented in the article, but without access to the article itself. That way, they will have to rely on their analytical skills and understanding rather than simply recalling the article's conclusions or general biological knowledge.

**Assignment structure:**

* My PhD students will receive ONLY the questions themselves — no article, no dataset, no figures, no supplementary material.
* They will be required to answer a series of fully self-contained questions that test their ability to interpret stated observations and derive conclusions from them.

**Self-containment rule (HARD REQUIREMENT):**

* Every question must be answerable from the question text ALONE. Embed all context the student needs (system, intervention, entities, quantitative observations, experimental setting) directly in the question stem as neutral, factual premises (e.g., "In patients with advanced synovial sarcoma treated with autologous T cells engineered to express an NY-ESO-1-directed TCR, 50% showed objective responses while serious complications such as cytokine release syndrome were manageable, ...").
* **Stem economy (HARD REQUIREMENT):** keep each stem under ~900 characters (~150 words). Include ONLY the premises the reasoning needs; cut background narration, redundant qualifiers, and scene-setting that no inference depends on. Self-containment means completeness of the NECESSARY premises, not length — otherwise the benchmark silently becomes a long-text interference test.
* NEVER refer to any external source. The following are FORBIDDEN in questions: "the article", "the paper", "the review", "the study", "the text", "the passage", "the material", "the authors", "according to ...", "as described", "described in", "mentioned", "reported in", "Figure X", "Table X", "Section X", and any equivalent wording in any language.
* Do not presuppose access to any artifact (e.g., "the dataset shows..."). Instead, state the concrete observations yourself (e.g., "a screen of 1,477 tick-associated viruses found...").

**Evidential validity (HARD REQUIREMENT) — the keyed answer must be exactly what the stated evidence licenses, never stronger:**

An external review of a pack built with these prompts rejected 9 of 10 items because the gold answers asserted conclusions the underlying data cannot support. Every one of the following inferences is FORBIDDEN in a question, in its reference answer, and in its rubric facts:

* **No arithmetic across different baselines / denominators / contexts.** Percentages and fold-changes measured against different reference groups, in different experiments, cell types, species or conditions cannot be added, subtracted, or ratioed into a new number. Do not write "a −25.7% effect minus a +20.0% rescue leaves −5.7%" or treat a difference of two percent-changes as "percentage points". If two quantities do not share a baseline, you may ask the respondent to note that they are not directly comparable (and what design would make them so) — never to combine them.
* **Never rank pathway position by P-value magnitude.** A smaller P-value does not mean "more upstream", "more downstream", "stronger effect on the pathway", or "more causal"; it reflects effect size, variance and n. Do not build a question on a claimed expectation that an upstream node must show a smaller P-value, and do not read a P-value "reversal" as evidence about cascade architecture.
* **Do not compare different statistic types.** An F statistic, a t statistic, an R², an odds ratio and a coefficient are not interchangeable: R² is a proportion of variance explained, F is a test statistic. Never assert that values of two different types "match in magnitude" or that one is larger/smaller in a meaningful way.
* **Sufficiency is not necessity, uniqueness, or obligation.** A rescue/manipulation that restores a phenotype shows that the manipulated node is *sufficient* in that context. It does NOT establish that the node is *the* gate, the unique route, the obligatory effector, the most direct mediator, or necessary at all. Use "is sufficient to" / "contributes to", never "is the only/most direct gate" or "is obligatory" unless the evidence includes a genuine necessity test (e.g., loss-of-function with no residual effect).
* **A negative binding assay is not a universal exclusion.** A co-immunoprecipitation that detects no interaction rules out a stable direct binary interaction under those conditions. It does not rule out indirect coupling, transient interaction, or co-localisation through a third partner, and it does not license "therefore X is independent of Y".
* **Never infer per-cell production from a compositional proportion.** The fraction of expressing cells among all X cells (from single-cell/single-nucleus data) is a *composition*, not a production rate: it says nothing about transcript or protein output per cell. Do not divide such a fraction by a cell-abundance fraction and call the result a "per-cell rate".
* **A confounded manipulation licenses no causal attribution.** If a manipulation introduces an unavoidable co-variable (a transgene cassette, a vector, an injected vehicle, a surgical procedure), it cannot be credited as the cause. Either state the missing control and ask what it would establish, or scope the question to what the design isolates.
* **Counterfactuals may only be asked in a direction the evidence identifies.** Before asking "if X were removed/altered, what would happen to Y", check that the stated evidence pins down the sign (and ideally the rough size) of that dependence. If the data cannot distinguish "Y rises", "Y falls" and "Y unchanged", the counterfactual is unanswerable — ask instead what the evidence does license about X and Y, or what experiment would identify the direction.
* **Traceability:** every quantitative premise in the stem (a percentage, P-value, statistic, count, concentration) must be a number that actually appears in the source material, or be computed only from numbers that share a baseline and are all stated in the stem. Never invent a number to make a question harder, and never present a quantity as observed when it is an assumption (e.g. a midpoint of a stated range).

When the source evidence cannot support a strong conclusion, the correct question asks what the evidence *does* license, or what additional measurement would resolve the ambiguity — that is a legitimate and hard reasoning task. Do not manufacture determinacy the science does not have.

**Question types and difficulty ladder (HARD REQUIREMENT):**

* Every question has exactly ONE primary type, recorded in its "question_type" field, chosen from this fixed list:
  - "extraction" — identify/integrate the stated observations (basic comprehension tier)
  - "comparison" — contrast conditions/entities: what differs, and what explains the difference
  - "causal" — explain the mechanism or causal chain behind the stated observations
  - "multi_hop" — derive a NEW conclusion that requires chaining at least two stated premises (or computing from stated quantities)
  - "counterfactual" — alter or remove a stated condition and determine what follows ("if condition X were removed / if group Y were excluded, does the conclusion still hold, and why (not)?")
  - "experimental" — interpret an experimental design or manipulation: what each comparison isolates, what conclusion the observed pattern licenses
{ladder_split}
* This produces a set-wide difficulty ladder (understanding → integration → multi-step reasoning → counterfactual/conditional change → judgement) instead of N questions at one indistinguishable difficulty.
* **One core reasoning target (HARD REQUIREMENT):** a question may involve several supporting steps, but everything in it must serve ONE main reasoning target — the single insight-proving move. Never mix unrelated competences (number extraction + cause explanation + design critique + conclusion restatement) in one question; otherwise a failure cannot be attributed to any specific ability.

**Your task:**

* I will provide a list of key insights extracted from the article. Each insight contains a summary, the description of how the insight was derived by the authors, and the associated paragraphs from the paper that support this insight.
* For each insight, create {task_lead} that assess students’ ability to reason through the stated observations to reach similar conclusions. {coverage_open}The {subject} must remain strictly grounded in the provided insight** without introducing hallucinations.
* **Questions should be mostly designed based on the derivation of the insight** and not be simply factual recall of the insight's summary.
* Use the associated paragraphs to identify additional details that help you design challenging questions, including plausible but tricky wrong answers (hard negatives).
* Questions and the correct answer should not rely on your external knowledge. The correct answer(s) must reflect conclusions that can be drawn directly from the data, not just recall of factual statements.
* Tips for **designing hard questions with hard negatives**:
  1) Wrong answers should simulate realistic misinterpretations of the data, premature conclusions, or confusions between similar cell types / genes / pathways / systems. These are more cognitively demanding for PhD students to distinguish.
  2) Avoid irrelevant or obviously false options. Each incorrect option should reflect a misguided but well-intentioned line of reasoning from someone analyzing the dataset.

**Guidelines for the questions:**

* Questions can be *single-answer* (e.g., "D") or *multiple-answer* (e.g., "A,C,D"). If multiple entities are part of the correct answer, split them across options and key the combination (e.g., "A,C").
* Exactly four options A-D. Randomize the position of the correct answer(s) among the options.
* **Option structure (HARD REQUIREMENT):** every option must be a *conclusion + reasoning* pair of the form "<conclusion> because <reasoning>" (or an equivalent single sentence making both a claim and its justification). An option is correct ONLY IF both its conclusion AND its reasoning are correct. Distractors must mix failure modes: right conclusion + wrong reasoning, wrong conclusion + plausible-sounding reasoning, and claims that are individually true but do not answer the question asked.
* **Difficulty requirement (HARD REQUIREMENT):** the keyed answer must NOT be identifiable by surface plausibility alone. It must require combining at least two premises stated in the stem (or chaining a premise with a quantitative observation given in the stem). Every distractor must remain defensible under a naive single-premise reading, so that a respondent who does not carefully reason through the stated observations should find at least two options credible.
* **Length parity (HARD REQUIREMENT):** keep the four options within roughly the same length — the longest must stay under ~1.3x the shortest. In particular the keyed option must NOT be the longest one more often than chance; a respondent must never be able to pick the answer by length, by being the most detailed, or by being the only one carrying a justification. "Conclusion + reasoning" applies to every option equally, so if a distractor needs more words to stay defensible, shorten the keyed option rather than padding the distractor.
* Phrase questions as standalone scientific reasoning problems: state the observations neutrally, then ask which conclusion, interpretation, or decision follows. Use neutral language such as “these observations indicate,” “based on the stated findings,” or “given this pattern.”
* The question should not specify the exact methods to use to derive the answer from the data.
* Do not create questions that can be answered without analyzing the data.
* Do not create questions that ask which techniques a PhD student should use to obtain a certain result.
* Each question should ask only one clear thing (no double-barreled formulations).

**Guidelines for the rubric:**

* For every question provide a grading rubric consisting of:
  - "correct_reasoning": spell out BOTH parts of the keyed option — the conclusion and the full reasoning chain (which premises from the stem are combined, in what order), grounded in the insight derivation and the associated paragraphs.
  - "distractor_analysis": for every non-keyed option, state which part fails (the conclusion, the reasoning, or both), what naive single-premise reading it corresponds to, and why that reading is insufficient.
* The rubric must let a grader verify the answer without re-reading the article.

---

**Output format:**
Return ONLY a strict JSON array — no prose before or after, no markdown fences. The array must contain exactly one object per provided insight, in the same order, each with the "insight_index" matching the insight numbering below:

[
  {
    "insight_index": 1,
    "questions": [
{question_examples}
    ]
  }
]

## Extracted insights (their summaries, how they were derived, and supporting paragraphs):

**Below is the source material for generating the questions.**
Focus more on the derivation of the insights and the associated paragraphs, not just their summaries. The insights are:

{insights}

{final_request}
"""

_OE_RUBRIC_TEMPLATE = """
I am designing assignments for my PhD students on scientific data analysis. The assignment is based on a published scientific article presenting research findings.
I want the PhDs to reason through observations and derive insights similar to those presented in the article, but without access to the article itself. That way, they will have to rely on their analytical skills and understanding rather than simply recalling the article's conclusions or general biological knowledge.

**Assignment structure:**

* My PhD students will receive ONLY the questions themselves — no article, no dataset, no figures, no supplementary material.
* They will be required to answer a series of fully self-contained questions that test their ability to interpret stated observations and derive conclusions from them.

**Self-containment rule (HARD REQUIREMENT):**

* Every question must be answerable from the question text ALONE. Embed all context the student needs (system, intervention, entities, quantitative observations, experimental setting) directly in the question stem as neutral, factual premises (e.g., "In patients with advanced synovial sarcoma treated with autologous T cells engineered to express an NY-ESO-1-directed TCR, 50% showed objective responses while serious complications such as cytokine release syndrome were manageable, ...").
* **Stem economy (HARD REQUIREMENT):** keep each stem under ~900 characters (~150 words). Include ONLY the premises the reasoning needs; cut background narration, redundant qualifiers, and scene-setting that no inference depends on. Self-containment means completeness of the NECESSARY premises, not length — otherwise the benchmark silently becomes a long-text interference test.
* NEVER refer to any external source. The following are FORBIDDEN in questions: "the article", "the paper", "the review", "the study", "the text", "the passage", "the material", "the authors", "according to ...", "as described", "described in", "mentioned", "reported in", "Figure X", "Table X", "Section X", and any equivalent wording in any language.
* Do not presuppose access to any artifact (e.g., "the dataset shows..."). Instead, state the concrete observations yourself (e.g., "a screen of 1,477 tick-associated viruses found...").

**Evidential validity (HARD REQUIREMENT) — the keyed answer must be exactly what the stated evidence licenses, never stronger:**

An external review of a pack built with these prompts rejected 9 of 10 items because the gold answers asserted conclusions the underlying data cannot support. Every one of the following inferences is FORBIDDEN in a question, in its reference answer, and in its rubric facts:

* **No arithmetic across different baselines / denominators / contexts.** Percentages and fold-changes measured against different reference groups, in different experiments, cell types, species or conditions cannot be added, subtracted, or ratioed into a new number. Do not write "a −25.7% effect minus a +20.0% rescue leaves −5.7%" or treat a difference of two percent-changes as "percentage points". If two quantities do not share a baseline, you may ask the respondent to note that they are not directly comparable (and what design would make them so) — never to combine them.
* **Never rank pathway position by P-value magnitude.** A smaller P-value does not mean "more upstream", "more downstream", "stronger effect on the pathway", or "more causal"; it reflects effect size, variance and n. Do not build a question on a claimed expectation that an upstream node must show a smaller P-value, and do not read a P-value "reversal" as evidence about cascade architecture.
* **Do not compare different statistic types.** An F statistic, a t statistic, an R², an odds ratio and a coefficient are not interchangeable: R² is a proportion of variance explained, F is a test statistic. Never assert that values of two different types "match in magnitude" or that one is larger/smaller in a meaningful way.
* **Sufficiency is not necessity, uniqueness, or obligation.** A rescue/manipulation that restores a phenotype shows that the manipulated node is *sufficient* in that context. It does NOT establish that the node is *the* gate, the unique route, the obligatory effector, the most direct mediator, or necessary at all. Use "is sufficient to" / "contributes to", never "is the only/most direct gate" or "is obligatory" unless the evidence includes a genuine necessity test (e.g., loss-of-function with no residual effect).
* **A negative binding assay is not a universal exclusion.** A co-immunoprecipitation that detects no interaction rules out a stable direct binary interaction under those conditions. It does not rule out indirect coupling, transient interaction, or co-localisation through a third partner, and it does not license "therefore X is independent of Y".
* **Never infer per-cell production from a compositional proportion.** The fraction of expressing cells among all X cells (from single-cell/single-nucleus data) is a *composition*, not a production rate: it says nothing about transcript or protein output per cell. Do not divide such a fraction by a cell-abundance fraction and call the result a "per-cell rate".
* **A confounded manipulation licenses no causal attribution.** If a manipulation introduces an unavoidable co-variable (a transgene cassette, a vector, an injected vehicle, a surgical procedure), it cannot be credited as the cause. Either state the missing control and ask what it would establish, or scope the question to what the design isolates.
* **Counterfactuals may only be asked in a direction the evidence identifies.** Before asking "if X were removed/altered, what would happen to Y", check that the stated evidence pins down the sign (and ideally the rough size) of that dependence. If the data cannot distinguish "Y rises", "Y falls" and "Y unchanged", the counterfactual is unanswerable — ask instead what the evidence does license about X and Y, or what experiment would identify the direction.
* **Traceability:** every quantitative premise in the stem (a percentage, P-value, statistic, count, concentration) must be a number that actually appears in the source material, or be computed only from numbers that share a baseline and are all stated in the stem. Never invent a number to make a question harder, and never present a quantity as observed when it is an assumption (e.g. a midpoint of a stated range).

When the source evidence cannot support a strong conclusion, the correct question asks what the evidence *does* license, or what additional measurement would resolve the ambiguity — that is a legitimate and hard reasoning task. Do not manufacture determinacy the science does not have.

**Question types and difficulty ladder (HARD REQUIREMENT):**

* Every question has exactly ONE primary type, recorded in its "question_type" field, chosen from this fixed list:
  - "extraction" — identify/integrate the stated observations (basic comprehension tier)
  - "comparison" — contrast conditions/entities: what differs, and what explains the difference
  - "causal" — explain the mechanism or causal chain behind the stated observations
  - "multi_hop" — derive a NEW conclusion that requires chaining at least two stated premises (or computing from stated quantities)
  - "counterfactual" — alter or remove a stated condition and determine what follows ("if condition X were removed / if group Y were excluded, does the conclusion still hold, and why (not)?")
  - "experimental" — interpret an experimental design or manipulation: what each comparison isolates, what conclusion the observed pattern licenses
{ladder_split}
* This produces a set-wide difficulty ladder (understanding → integration → multi-step reasoning → counterfactual/conditional change → judgement) instead of N questions at one indistinguishable difficulty.
* **One core reasoning target (HARD REQUIREMENT):** a question may involve several supporting steps, but everything in it must serve ONE main reasoning target — the single insight-proving move. Never mix unrelated competences (number extraction + cause explanation + design critique + conclusion restatement) in one question; otherwise a failure cannot be attributed to any specific ability.

**Your task:**

* I will provide a list of key insights extracted from the article. Each insight contains a summary, the description of how the insight was derived by the authors, and the associated paragraphs from the paper that support this insight.
* For each insight, create {task_lead} that assess students’ ability to reason through the stated observations to reach similar conclusions. {coverage_open}The {subject} must remain strictly grounded in the provided insight** without introducing hallucinations.
* **Questions should be mostly designed based on the derivation of the insight** and not be simply factual recall of the insight's summary.
* Question should not rely on your external knowledge.

**Guidelines for the questions:**

* **Difficulty requirement (HARD REQUIREMENT):** each question must require chaining at least two stated premises (or computing/combining quantities given in the stem) — never a single-premise read and never something answerable from textbook knowledge alone. When the insight carries quantitative observations, embed them in the stem and make the answer depend on combining them.
* Avoid phrasing that suggests PhD students need to recall the article or authors’ conclusions. Phrase questions as standalone scientific reasoning problems: state the observations neutrally, then ask which conclusion, interpretation, or decision follows. Use neutral language such as “these observations indicate,” “based on the stated findings,” or “given this pattern.”
* The question should not specify the exact methods to use to derive the answer from the data.
* To reduce bias, formulate questions more generally (e.g., instead of “Type 1 and Type 7 show xyz behaviour”, ask “Which types show xyz behaviour, and justify with evidence?”).
* **Each question should ask only one clear thing.** Do not merge multiple sub-questions into a single question.
* Do not use double-barreled formulations such as “How does X differ, and what might this suggest…?” or “How do X influence Y, and what is the impact on Z?”.
* Do not create questions that can be answered without analyzing the data.
* Questions should go beyond simple answers like “increase/decrease” or “yes/no.” They should be open-ended, requiring PhDs to explore different possibilities and justify their reasoning.

**Guidelines for the answers:**

* For each question, provide the reference answer based strictly on the dataset-derived insight.
* Answers should focus on the findings themselves and not mention the specific methods or tools used to obtain them.
* The reference answer must be *derivation-dependent*: it should follow only from combining the stated premises. A respondent relying on general domain knowledge alone, without carefully chaining the stated observations, should only be able to produce a partial answer.
* **Prefer combinatorial reasoning over enumeration:** design questions whose answer is a NEW conclusion derived from combining two or three stated facts, rather than a list of stated facts. The benchmark should measure reasoning, not checklist completion — do not add more scoring facts where an additional inference hop could be demanded instead.

**Guidelines for the rubric:**

* For every question provide a grading rubric with two components:
  - "facts": **3 to 5 facts — hard lower AND upper bound** — capturing the decision-critical scientific claims of the answer. Each fact is ONE core claim at SEMANTIC level: a distinct inference result, mechanism step, quantity-derived conclusion, or judgement. Rules:
      * Do NOT mechanically split one coherent conclusion into wording-level sub-points (e.g., the same claim with and without its justification as separate facts, or one conclusion fractured into "states X" + "states X because Y" + "states X applies under Z"). A respondent who states the core conclusion correctly earns that fact — full stop.
      * Do NOT transcribe the reference answer's granularity or phrasing. Grading is SEMANTIC COVERAGE: paraphrase, reordering, and alternative correct derivations all count as coverage.
      * Prefer facts that require COMBINING stated premises over facts that restate a single premise. Every fact must be necessary to judge correctness — no checklist padding: if dropping a fact would not change the score of any reasonable answer, drop it.
      * Facts must capture the DERIVED claims (conclusions that follow only from combining the stated premises, including quantities computed from them); generic domain knowledge without derivation must not appear, so a knowledge-only answer cannot score above PARTIAL on them.
  - "scoring_guide": a concise mapping from fact coverage to a 1-5 correctness score, in the spirit of: 5 = all facts fully covered with data-grounded support; 4 = most facts covered, none incorrect; 3 = some facts covered, at least one partial/missing; 2 = no fact fully covered, some partial; 1 = facts missing or contradicted.
* The rubric must let a grader score any student answer without re-reading the article.

---

**Output format:**
Return ONLY a strict JSON array — no prose before or after, no markdown fences. The array must contain exactly one object per provided insight, in the same order, each with the "insight_index" matching the insight numbering below:

[
  {
    "insight_index": 1,
    "questions": [
{question_examples}
    ]
  }
]

## Extracted insights (their summaries, how they were derived, and supporting paragraphs):

**Below is the source material for generating the questions.**
Focus more on the derivation of the insights and the associated paragraphs, not just their summaries. The insights are:

{insights}

{final_request}
"""

_OE_QUESTION_EXAMPLES = {
    2: """      {
        "question": "…",
        "question_type": "extraction",
        "answer": "…",
        "rubric": {
          "facts": ["F1: …", "F2: …", "F3: …"],
          "scoring_guide": "…"
        }
      },
      {
        "question": "…",
        "question_type": "counterfactual",
        "answer": "…",
        "rubric": {
          "facts": ["F1: …", "F2: …", "F3: …"],
          "scoring_guide": "…"
        }
      }""",
    1: """      {
        "question": "…",
        "question_type": "counterfactual",
        "answer": "…",
        "rubric": {
          "facts": ["F1: …", "F2: …", "F3: …"],
          "scoring_guide": "…"
        }
      }""",
}

_MCQ_QUESTION_EXAMPLES = {
    2: """      {
        "question": "…",
        "question_type": "comparison",
        "options": {"A": "<conclusion> because <reasoning>", "B": "…", "C": "…", "D": "…"},
        "answer": "A,C",
        "rubric": {
          "correct_reasoning": "…",
          "distractor_analysis": {"B": "…", "D": "…"}
        }
      },
      {
        "question": "…",
        "question_type": "multi_hop",
        "options": {"A": "…", "B": "…", "C": "…", "D": "…"},
        "answer": "B",
        "rubric": {
          "correct_reasoning": "…",
          "distractor_analysis": {"A": "…", "C": "…", "D": "…"}
        }
      }""",
    1: """      {
        "question": "…",
        "question_type": "multi_hop",
        "options": {"A": "…", "B": "…", "C": "…", "D": "…"},
        "answer": "B",
        "rubric": {
          "correct_reasoning": "…",
          "distractor_analysis": {"A": "…", "C": "…", "D": "…"}
        }
      }""",
}

# Per-insight tier split. Two questions → Q1 low tier + Q2 high tier (the
# original wording); one question → that question must itself be the high tier.
_LADDER_SPLIT = {
    2: """* Per insight, the two questions must NOT be near-duplicates in different words. Follow a two-tier split:
  - **Q1 = lower tier** ("extraction" or "comparison"): core understanding of the stated observations.
  - **Q2 = higher tier** ("causal", "multi_hop", "counterfactual", or "experimental" — prefer "counterfactual" or "multi_hop"): reasoning that goes beyond restating the observations.""",
    1: """* Per insight, create exactly ONE question, and make it the HIGHER tier: "causal", "multi_hop", "counterfactual", or "experimental" (prefer "counterfactual" or "multi_hop"). Because the insight is represented by a single question, that question carries the whole reasoning load on its own — it must NOT be answerable by restating the insight's summary.""",
}


def _fill(template, per_insight, qtype):
    """Substitute the count-dependent blocks; n=2 reproduces the original text."""
    if per_insight not in _LADDER_SPLIT:
        raise SystemExit(f"ERROR: unsupported per-insight question count: {per_insight} (expected 1 or 2).")
    if qtype == "mcq":
        task_lead = f"{'two (2)' if per_insight == 2 else 'one (1)'} multiple-choice question{'s' if per_insight == 2 else ''}"
        final_request = (
            f"**Please generate {'two (2) multiple-choice questions with options, keyed answers' if per_insight == 2 else 'one (1) multiple-choice question with options, keyed answer'}"
            " and grading rubric" + ("s" if per_insight == 2 else "") +
            f" for each insight, following the above instructions, and return {'them' if per_insight == 2 else 'it'}"
            " as the strict JSON array specified above.**"
        )
        examples = _MCQ_QUESTION_EXAMPLES[per_insight]
    else:
        task_lead = f"{'two (2)' if per_insight == 2 else 'one (1)'} open-ended question{'s' if per_insight == 2 else ''}"
        final_request = (
            f"**Please generate {'two (2) open-ended questions with reference answers' if per_insight == 2 else 'one (1) open-ended question with a reference answer'}"
            " and grading rubric" + ("s" if per_insight == 2 else "") +
            f" for each insight, following the above instructions, and return {'them' if per_insight == 2 else 'it'}"
            " as the strict JSON array specified above.**"
        )
        examples = _OE_QUESTION_EXAMPLES[per_insight]
    return (
        template
        .replace("{ladder_split}", _LADDER_SPLIT[per_insight])
        .replace("{task_lead}", task_lead)
        .replace(
            "{coverage_open}",
            "**The questions should together cover different aspects of the insight and its derivation. "
            if per_insight == 2
            else "**The question should probe how the insight was derived rather than restate its summary. ",
        )
        .replace("{subject}", "questions" if per_insight == 2 else "question")
        .replace("{question_examples}", examples)
        .replace("{final_request}", final_request)
    )


def mcq_rubric_prompt(per_insight=2):
    return _fill(_MCQ_RUBRIC_TEMPLATE, per_insight, "mcq")


def oe_rubric_prompt(per_insight=2):
    return _fill(_OE_RUBRIC_TEMPLATE, per_insight, "oe")


# LAB-Bench LitQA2-style literature recall: the deliberate inverse of the
# self-contained rule above. The stem identifies only the minimal setting and
# asks for a specific reported result, so the item is answerable only by an
# agent that knows (or can retrieve) this article — which is exactly the
# difficulty profile the official benchmark has and a premise-complete stem
# destroys.
_MCQ_RECALL_TEMPLATE = """
I am building a literature-recall benchmark from a published scientific article — the same item format as the LAB-Bench LitQA2 subset.

**This is the OPPOSITE of a self-contained quiz, and that is deliberate (HARD REQUIREMENT):**

* The answer must require knowledge of what THIS article reported. A reader who never saw the article must NOT be able to derive the answer from the question text, and must not be able to eliminate three distractors by pure reasoning.
* The question names only the minimal identifying setting (organism, cell type, intervention, disease model, cohort, method) and then asks for ONE specific reported result: a number with its unit, a named molecule/gene/pathway, a direction of change, or the outcome of a specific experiment.
* Do NOT put the observations, measurements, comparison logic, or the reasoning chain into the stem. A premise-complete stem turns the item back into reading comprehension, which is exactly what this benchmark must avoid.
* Ask about a result the article establishes as its own; do not ask about textbook knowledge or a prior fact the article merely cites. If a competent scientist who never read the article could eliminate every distractor by logic, the item is invalid — pick a different fact.

**Options (HARD REQUIREMENTS):**

* Exactly four options, exactly ONE correct.
* All four options homogeneous in kind and shape: all numbers in the same unit, all gene/protein names, all directional claims, or all experimental outcomes. Never mix a number with a molecule name.
* The three distractors must be plausible to an expert who has NOT read the article: real entities, nearby values, or the opposite direction of a related effect.
* Keep the four options within ~1.3x of each other in length; the keyed option must not be systematically the longest or the most specific.
* The correct option must state exactly what the article reports (for numbers: the reported value and unit, rounded only to the article's own precision).

**No source references in the question text:** forbidden are "the article", "the paper", "this study", "the review", "the authors", "according to", "as described", "reported in", "Figure X", "Table X", "Section X", and equivalents.

{ladder_split}

**Output contract (strict JSON, same schema as the rest of the pipeline).** Return ONLY a strict JSON array, one object per provided insight, in the same order, each with "insight_index" matching the numbering below:

[
  {
    "insight_index": 1,
    "questions": [
      {
        "question": "<minimal identifying setting + the one specific reported result being asked for>",
        "answer": "<single letter of the keyed option>",
        "question_type": "<one of extraction|comparison|causal|multi_hop|counterfactual|experimental>",
        "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
        "rubric": {
          "correct_reasoning": "<what the article reported and why the keyed option is exactly that>",
          "distractor_analysis": {"<each non-keyed letter>": "<which adjacent result or misreading that option corresponds to>"}
        }
      }
    ]
  }
]

Two worked examples of the target shape (invented domain — copy the FORM, not the content):

[
  {
    "insight_index": 1,
    "questions": [
      {
        "question": "A soil bacterium was evolved in the laboratory under stepwise antibiotic selection. To which of the following antibiotics did the evolved lineage acquire resistance?",
        "answer": "C",
        "question_type": "extraction",
        "options": {"A": "meropenem", "B": "gentamicin", "C": "ciprofloxacin", "D": "ampicillin"},
        "rubric": {
          "correct_reasoning": "The evolved lineage acquired resistance to ciprofloxacin only; the other three antibiotics were tested and remained effective.",
          "distractor_analysis": {"A": "meropenem was the selection agent for the ancestral strain in an earlier experiment, not this lineage.", "B": "gentamicin susceptibility was unchanged in the evolved lineage.", "D": "ampicillin resistance was never observed in this evolution experiment."}
        }
      },
      {
        "question": "In an scRNA-seq atlas of the aging mouse ovary, what fraction of granulosa cells scored positive for the inflammatory signature?",
        "answer": "A",
        "question_type": "extraction",
        "options": {"A": "13.9% of granulosa cells", "B": "2.7% of granulosa cells", "C": "34.4% of granulosa cells", "D": "61.2% of granulosa cells"},
        "rubric": {
          "correct_reasoning": "The atlas reports 13.9% of granulosa cells positive for the signature in aged ovaries.",
          "distractor_analysis": {"B": "2.7% is the proportion reported for a different cell type in the same figure.", "C": "34.4% is the proportion of positive cells in the young comparator.", "D": "61.2% is the oocyte-associated value, not the granulosa value."}
        }
      }
    ]
  }
]

## Extracted insights (their summaries, how they were derived, and supporting paragraphs):

**Below is the source material for generating the questions.** For each insight, identify the single most specific, least guessable reported fact — prefer a quantitative result or a named entity unique to this article — and build the recall question around it. The insights are:

{insights}

{final_request}
"""

_RECALL_LADDER = {
    2: """* Per insight, create exactly TWO questions, both in recall style, targeting two DIFFERENT reported facts (for example one quantitative result and one named entity or directional finding). Neither may be answerable from the other.""",
    1: """* Per insight, create exactly ONE question, targeting the single most specific and least guessable reported fact of that insight — prefer a quantitative result or a named entity unique to this article over a general conceptual claim.""",
}

# Candidate-pool mode: N questions per insight in one call, deliberately spread
# across the difficulty ladder. Downstream, every candidate is put to the solver
# panel with the official LAB-Bench evaluation and only the ones the panel gets
# wrong survive — the pool exists so the filter has something to select from,
# since a single two-question draw per insight almost never yields hard items.
_POOL_LADDER_TEMPLATE = """* Per insight, generate exactly {n} questions that SPAN the difficulty ladder — not {n} paraphrases of one idea:
  - at least one "extraction" question (a specific stated fact);
  - at least one "comparison" question (contrast two stated conditions);
  - at least two "multi_hop" or "causal" questions that require COMBINING THREE OR MORE quantitative premises from the stem, where no single premise yields the answer;
  - at least one "counterfactual" or "experimental" question with interacting conditions (perturb two things at once; predict what separates the outcomes).
* The harder questions must not be answerable by pattern-matching a single premise: the keyed option has to be reachable only after the composition. Aim for at least two questions a careful but hurried reader would plausibly get wrong — for example, where the answer depends on keeping track of which baseline each percentage refers to, on distinguishing a compositional readout from a per-cell one, or on an interaction between two interventions."""


def mcq_pool_prompt(n=6):
    """Candidate-pool variant: `n` questions per insight spanning the ladder.

    Same self-containment rules as mcq_rubric_prompt; only the per-insight
    allowance and the difficulty-spread instruction differ (n = 3..8)."""
    if not 3 <= n <= 8:
        raise SystemExit(f"ERROR: unsupported pool size: {n} (expected 3..8).")
    return (_MCQ_RUBRIC_TEMPLATE
            .replace("{ladder_split}", _POOL_LADDER_TEMPLATE.replace("{n}", str(n)))
            .replace("{task_lead}", f"{n} multiple-choice questions")
            .replace("{coverage_open}",
                     f"**The {n} questions should together cover different aspects of the insight "
                     "and its derivation, weighted towards the higher tiers. ")
            .replace("{subject}", "questions")
            .replace("{question_examples}", _MCQ_QUESTION_EXAMPLES[2])
            .replace("{final_request}",
                     f"**Please generate {n} multiple-choice questions with options, keyed answers "
                     "and grading rubrics for each insight, following the above instructions, and "
                     "return them as the strict JSON array specified above.**"))


def mcq_recall_prompt(per_insight=1):
    """LAB-Bench LitQA2-style recall MCQ prompt (deliberately NOT self-contained)."""
    if per_insight not in _RECALL_LADDER:
        raise SystemExit(f"ERROR: unsupported per-insight question count: {per_insight} (expected 1 or 2).")
    task_lead = (f"{'two (2)' if per_insight == 2 else 'one (1)'} literature-recall "
                 f"multiple-choice question{'s' if per_insight == 2 else ''}")
    final_request = (
        f"**Please generate {task_lead} with four options, one keyed answer and a grading rubric "
        "for each insight, following the above instructions, and return them as the strict JSON array "
        "specified above.**"
    )
    return (_MCQ_RECALL_TEMPLATE
            .replace("{ladder_split}", _RECALL_LADDER[per_insight])
            .replace("{task_lead}", task_lead)
            .replace("{final_request}", final_request))


MCQ_RUBRIC_PROMPT = mcq_rubric_prompt(2)
OE_RUBRIC_PROMPT = oe_rubric_prompt(2)
