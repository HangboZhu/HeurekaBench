"""Structured question-generation prompts (Question + Answer + grading Rubric).

Used by insights_to_questions.py --structured: unlike the legacy text-format
prompts, these ask the LLM to return strict JSON in which every question
carries an explicit grading rubric, so the reference answer does not have to
be re-decomposed into atomic facts by the LLM judge at evaluation time.

Rubric design mirrors the G-Eval protocol in geval_prompts/eval_prompts.py:
OE rubrics are atomic, verifiable facts (PRESENT/PARTIAL/MISSING/INCORRECT
checkable) plus a 1-5 scoring guide; MCQ rubrics explain the keyed answer and
every distractor.
"""

MCQ_RUBRIC_PROMPT = """
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

**Question types and difficulty ladder (HARD REQUIREMENT):**

* Every question has exactly ONE primary type, recorded in its "question_type" field, chosen from this fixed list:
  - "extraction" — identify/integrate the stated observations (basic comprehension tier)
  - "comparison" — contrast conditions/entities: what differs, and what explains the difference
  - "causal" — explain the mechanism or causal chain behind the stated observations
  - "multi_hop" — derive a NEW conclusion that requires chaining at least two stated premises (or computing from stated quantities)
  - "counterfactual" — alter or remove a stated condition and determine what follows ("if condition X were removed / if group Y were excluded, does the conclusion still hold, and why (not)?")
  - "experimental" — interpret an experimental design or manipulation: what each comparison isolates, what conclusion the observed pattern licenses
* Per insight, the two questions must NOT be near-duplicates in different words. Follow a two-tier split:
  - **Q1 = lower tier** ("extraction" or "comparison"): core understanding of the stated observations.
  - **Q2 = higher tier** ("causal", "multi_hop", "counterfactual", or "experimental" — prefer "counterfactual" or "multi_hop"): reasoning that goes beyond restating the observations.
* This produces a set-wide difficulty ladder (understanding → integration → multi-step reasoning → counterfactual/conditional change → judgement) instead of N questions at one indistinguishable difficulty.
* **One core reasoning target (HARD REQUIREMENT):** a question may involve several supporting steps, but everything in it must serve ONE main reasoning target — the single insight-proving move. Never mix unrelated competences (number extraction + cause explanation + design critique + conclusion restatement) in one question; otherwise a failure cannot be attributed to any specific ability.

**Your task:**

* I will provide a list of key insights extracted from the article. Each insight contains a summary, the description of how the insight was derived by the authors, and the associated paragraphs from the paper that support this insight.
* For each insight, create two (2) multiple-choice questions that assess students’ ability to reason through the stated observations to reach similar conclusions. **The questions should together cover different aspects of the insight and its derivation. The questions must remain strictly grounded in the provided insight** without introducing hallucinations.
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
      {
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
      }
    ]
  }
]

## Extracted insights (their summaries, how they were derived, and supporting paragraphs):

**Below is the source material for generating the questions.**
Focus more on the derivation of the insights and the associated paragraphs, not just their summaries. The insights are:

{insights}

**Please generate two (2) multiple-choice questions with options, keyed answers and grading rubrics for each insight, following the above instructions, and return them as the strict JSON array specified above.**
"""

OE_RUBRIC_PROMPT = """
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

**Question types and difficulty ladder (HARD REQUIREMENT):**

* Every question has exactly ONE primary type, recorded in its "question_type" field, chosen from this fixed list:
  - "extraction" — identify/integrate the stated observations (basic comprehension tier)
  - "comparison" — contrast conditions/entities: what differs, and what explains the difference
  - "causal" — explain the mechanism or causal chain behind the stated observations
  - "multi_hop" — derive a NEW conclusion that requires chaining at least two stated premises (or computing from stated quantities)
  - "counterfactual" — alter or remove a stated condition and determine what follows ("if condition X were removed / if group Y were excluded, does the conclusion still hold, and why (not)?")
  - "experimental" — interpret an experimental design or manipulation: what each comparison isolates, what conclusion the observed pattern licenses
* Per insight, the two questions must NOT be near-duplicates in different words. Follow a two-tier split:
  - **Q1 = lower tier** ("extraction" or "comparison"): core understanding of the stated observations.
  - **Q2 = higher tier** ("causal", "multi_hop", "counterfactual", or "experimental" — prefer "counterfactual" or "multi_hop"): reasoning that goes beyond restating the observations.
* This produces a set-wide difficulty ladder (understanding → integration → multi-step reasoning → counterfactual/conditional change → judgement) instead of N questions at one indistinguishable difficulty.
* **One core reasoning target (HARD REQUIREMENT):** a question may involve several supporting steps, but everything in it must serve ONE main reasoning target — the single insight-proving move. Never mix unrelated competences (number extraction + cause explanation + design critique + conclusion restatement) in one question; otherwise a failure cannot be attributed to any specific ability.

**Your task:**

* I will provide a list of key insights extracted from the article. Each insight contains a summary, the description of how the insight was derived by the authors, and the associated paragraphs from the paper that support this insight.
* For each insight, create two (2) open-ended questions that assess students’ ability to reason through the stated observations to reach similar conclusions. **The questions should together cover different aspects of the insight and its derivation. The questions must remain strictly grounded in the provided insight** without introducing hallucinations.
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
      {
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
      }
    ]
  }
]

## Extracted insights (their summaries, how they were derived, and supporting paragraphs):

**Below is the source material for generating the questions.**
Focus more on the derivation of the insights and the associated paragraphs, not just their summaries. The insights are:

{insights}

**Please generate two (2) open-ended questions with reference answers and grading rubrics for each insight, following the above instructions, and return them as the strict JSON array specified above.**
"""
