"""Run a solver model over generated questions and grade the answers.

Implements the README's recommended cleanup pass — measuring how many
questions a strong LLM answers correctly WITHOUT the article. The solver
sees ONLY the question text (self-contained by construction of
insights_to_questions.py --structured).

Grading:
  - MCQ: exact letter-set match against the keyed answer (plus per-letter
    partial credit), no LLM involved.
  - OE: an LLM grader labels each rubric fact PRESENT/PARTIAL/MISSING/
    INCORRECT (G-Eval protocol from geval_prompts) and returns an overall
    1-5 rating in <rating></rating> tags.

All models are served through the gateway (utils/llm_gateway.py). Results
are saved after every question and reused on re-run (resume-safe).

Example:
  python solve_and_grade.py --questions_json <dir>/mcq_questions.json \
      --qtype mcq --solver MiniMax-M3 --out <dir>/mcq_solved.json
  python solve_and_grade.py --questions_json <dir>/oe_questions.json \
      --qtype oe --solver MiniMax-M3 --grader gpt-5.6-luna \
      --out <dir>/oe_solved.json
"""

import os
import re
import json
import argparse
import time

from utils import llm_gateway

MCQ_SOLVER_PROMPT = """Answer the following multiple-choice question on your own.
Think it through if needed, then end your reply with a final line of exactly:
ANSWER: <letter(s)>        (e.g. "ANSWER: B" or "ANSWER: A,C")

Question:
{question}

{options}
"""

OE_SOLVER_PROMPT = """Answer the following open-ended scientific question on your own.
Reason through the stated observations and give a complete, well-justified answer.

Question:
{question}
"""

OE_GRADE_PROMPT = """You are grading a PhD student's answer to an open-ended scientific reasoning question.
You will be given the question, the student's answer, the ground-truth (GT) answer, and the GT answer
decomposed into atomic facts (F1..Fn).

**Evaluation steps:**
1) For each fact Fi, classify the student's coverage as:
   - PRESENT: fact included with the same meaning, clearly supported in the student's own reasoning.
   - PARTIAL: correct meaning but vague, hedged (e.g. "likely", "for example"), supported only by
     generic background recall, or buried in an uncommitted list of plausible options.
   - MISSING: fact not mentioned.
   - INCORRECT: wrong or contradicts some GT fact.
2) Determine the overall correctness score (1-5):
{scoring_guide}
3) Extra information that does not contradict the GT does not affect the score.

**Output format (follow exactly):**
One line per fact: "F1: PRESENT" (or PARTIAL/MISSING/INCORRECT), then a final line:
<rating>N</rating>
with N the integer 1-5 score. No other text.

---
Question:
{question}

Student's answer:
{answer}

GT answer:
{gt_answer}

GT facts:
{facts}
"""

DEFAULT_SCORING_GUIDE = """   - 5: ALL facts PRESENT, none MISSING/INCORRECT.
   - 4: MOST facts PRESENT, none INCORRECT, some MISSING allowed.
   - 3: SOME facts PRESENT, at least one PARTIAL/MISSING; minor contradictions allowed.
   - 2: NO fact fully PRESENT, some PARTIAL.
   - 1: facts MISSING or contradicted."""


def iter_questions(questions_json, qtype):
    """Yield (paper_id, insight_id, q_idx, question_dict) in file order."""
    with open(questions_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    for paper_id, paper in data.items():
        for insight_id, insight in paper.items():
            for idx, q in enumerate(insight.get(f"{qtype}_questions", []) or [], 1):
                yield paper_id, insight_id, idx, q


def parse_mcq_answer(text):
    """Letter set from the last 'ANSWER: X[,Y]' line (or a bare-letters reply), uppercased."""
    if not text:
        return set()
    matches = re.findall(r"ANSWER\s*[:\-]?\s*([A-D](?:\s*[,\s]\s*[A-D])*)", text, re.IGNORECASE)
    if matches:
        return set(re.findall(r"[A-D]", matches[-1].upper()))
    stripped = text.strip().strip('.`*\'"')
    if re.fullmatch(r"[A-D](?:\s*[,\s]\s*[A-D])*", stripped):
        return set(re.findall(r"[A-D]", stripped.upper()))
    return set()


def parse_grade(text):
    """(rating 1-5 or 0, {fact_no: label}) from the grader response."""
    rating = 0
    m = re.search(r"<rating>\s*([1-5])\s*</rating>", text or "")
    if m:
        rating = int(m.group(1))
    labels = {
        int(no): label.upper()
        for no, label in re.findall(r"\bF(\d+)\s*[:\-]\s*(PRESENT|PARTIAL|MISSING|INCORRECT)", text or "", re.IGNORECASE)
    }
    return rating, labels


def solve_mcq(q, solver):
    options = "\n".join(f"{letter}) {text}" for letter, text in q["options"].items())
    prompt = MCQ_SOLVER_PROMPT.format(question=q["question"], options=options)
    return llm_gateway.chat(prompt, model=solver)


def grade_mcq(q, raw_answer):
    pred = parse_mcq_answer(raw_answer)
    gt = set(re.findall(r"[A-D]", (q.get("answer") or "").upper()))
    tp = len(pred & gt)
    return {
        "pred": ",".join(sorted(pred)),
        "gt": ",".join(sorted(gt)),
        "correct": pred == gt and bool(gt),
        "recall": tp / len(gt) if gt else 0.0,
        "precision": tp / len(pred) if pred else 0.0,
    }


def solve_oe(q, solver):
    return llm_gateway.chat(OE_SOLVER_PROMPT.format(question=q["question"]), model=solver)


def grade_oe(q, raw_answer, grader):
    rubric = q.get("rubric") or {}
    facts = rubric.get("facts") or []
    prompt = OE_GRADE_PROMPT.format(
        scoring_guide=rubric.get("scoring_guide") or DEFAULT_SCORING_GUIDE,
        question=q["question"],
        answer=raw_answer,
        gt_answer=q.get("answer", ""),
        facts="\n".join(facts) if facts else "(no decomposition provided; extract facts from the GT yourself)",
    )
    for attempt in (1, 2, 3):
        raw = llm_gateway.chat(prompt, model=grader)
        rating, labels = parse_grade(raw)
        if rating:
            return rating, labels, raw
        print(f"    Grader output unparseable (attempt {attempt}/3).")
    return 0, {}, raw or "(empty)"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions_json", type=str, required=True,
                        help="mcq_questions.json / oe_questions.json from insights_to_questions.py --structured")
    parser.add_argument("--qtype", type=str, required=True, choices=["mcq", "oe"])
    parser.add_argument("--solver", type=str, required=True, help="Gateway model that answers the questions")
    parser.add_argument("--grader", type=str, default=None,
                        help="[oe] Gateway model that grades against the rubric facts")
    parser.add_argument("--out", type=str, required=True, help="Result JSON path (resume-safe)")
    args = parser.parse_args()
    if args.qtype == "oe" and not args.grader:
        parser.error("--grader is required for --qtype oe.")

    results = {}
    if os.path.exists(args.out):
        with open(args.out, "r", encoding="utf-8") as f:
            results = json.load(f)
        print(f"Resuming: {len(results)} answered question(s) already cached in '{args.out}'.")

    for paper_id, insight_id, idx, q in iter_questions(args.questions_json, args.qtype):
        key = f"{paper_id}/{insight_id}/Q{idx}"
        rec = results.get(key) or {}
        if not rec.get("raw_answer"):
            print(f"{key}: solving with '{args.solver}'...")
            if args.qtype == "mcq":
                rec["raw_answer"] = solve_mcq(q, args.solver)
            else:
                rec["raw_answer"] = solve_oe(q, args.solver)
            results[key] = rec
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            time.sleep(1)

        if args.qtype == "mcq":
            if "grade" not in rec:
                rec["grade"] = grade_mcq(q, rec["raw_answer"])
                results[key] = rec
                with open(args.out, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
            g = rec["grade"]
            print(f"{key}: pred={g['pred']} gt={g['gt']} -> {'CORRECT' if g['correct'] else 'wrong'}")
        else:
            if "grade" not in rec:
                print(f"{key}: grading with '{args.grader}'...")
                rating, labels, raw = grade_oe(q, rec["raw_answer"], args.grader)
                rec["grade"] = {"rating": rating, "fact_labels": {f"F{n}": l for n, l in sorted(labels.items())}}
                rec["grader_raw"] = raw
                results[key] = rec
                with open(args.out, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"{key}: rating={rec['grade']['rating']} facts={rec['grade']['fact_labels']}")

    # Summary
    if args.qtype == "mcq":
        graded = [r["grade"] for r in results.values() if r.get("grade")]
        n = len(graded)
        print(f"\n=== MCQ summary ({args.solver}) ===")
        print(f"graded: {n} | exact-match: {sum(g['correct'] for g in graded)}/{n}"
              f" ({100 * sum(g['correct'] for g in graded) / n:.0f}%)" if n else "no graded answers")
        if n:
            print(f"partial-credit recall: {sum(g['recall'] for g in graded) / n:.2f} | "
                  f"precision: {sum(g['precision'] for g in graded) / n:.2f}")
    else:
        graded = [r["grade"] for r in results.values() if r.get("grade")]
        ratings = [g["rating"] for g in graded if g["rating"]]
        print(f"\n=== OE summary (solver {args.solver}, grader {args.grader}) ===")
        if ratings:
            print(f"graded: {len(ratings)} | mean rating: {sum(ratings) / len(ratings):.2f}/5 | "
                  f"distribution: " + " ".join(f"{v}:{ratings.count(v)}" for v in sorted(set(ratings))))
        label_counts = {}
        for g in graded:
            for label in g.get("fact_labels", {}).values():
                label_counts[label] = label_counts.get(label, 0) + 1
        if label_counts:
            print(f"fact coverage: {label_counts}")


if __name__ == "__main__":
    main()
