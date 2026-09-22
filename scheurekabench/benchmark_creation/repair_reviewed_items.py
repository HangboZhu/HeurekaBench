"""Repair specific questions that an external scientific review rejected.

The difficulty loop hardens questions by demanding cross-context quantitative
comparisons and extra counterfactual hops. Nothing in the generation prompts
bounded what the *evidence* licenses, so hardening produced gold answers that
assert conclusions the data cannot support: subtracting percentages measured
against different baselines, ranking pathway position by P-value, comparing an
F statistic with an R², reading necessity out of a sufficiency experiment.
An external review of the aging-res-0919 pack rejected 9 of 10 items that way.

This script repairs individual items instead of the whole pack:

  1. for one insight, it builds the standard rubric prompt (which now carries
     the "Evidential validity" HARD REQUIREMENTS block) around that insight's
     summary / how / relevant plus its CURRENT question;
  2. it injects the reviewer's critique of that item verbatim and a repair
     task: keep the insight and the reasoning tier, but make the keyed answer
     exactly what the evidence licenses — scoping the question to what the
     data isolates, or asking what measurement would resolve the ambiguity;
  3. it validates and writes the item back in place, re-rendering the legacy
     text and re-exporting the eval + answer-free prompt files.

Every repaired item is persisted immediately, so a gateway stall mid-run is
resumable: re-running skips items already repaired (unless --force).

  python benchmark_creation/repair_reviewed_items.py \
      --questions_json <dir>/oe_questions.json --qtype oe \
      --critiques <dir>/review_critiques.json --model_call claude_code
"""

import argparse
import json
import os

import time

from insights_to_questions import insight_prompt_block, generate_checked, render_legacy_text
from prompts.insight2question_rubric_prompts import mcq_rubric_prompt, oe_rubric_prompt
from export_eval_questions import export_eval, export_prompts_only

REPAIR_INSTRUCTIONS = """
---

### ADDITIONAL TASK FOR THIS RUN: REPAIR A REJECTED QUESTION

The question above for **Insight #{idx}** failed an external scientific review. The reviewer's
critique, verbatim:

> {critique}

Your task is to **rewrite that one question** (with its reference answer and rubric) so that it
survives this critique, while staying on the same insight and keeping its reasoning tier.

Hard rules for the replacement:

* **The keyed answer must be exactly what the stated evidence licenses — not one step stronger.**
  Re-read the "Evidential validity" HARD REQUIREMENTS above; this item was rejected for violating
  one of them. Quote the offending inference to yourself and design it out.
* **Do not fix the problem by making the question trivial.** A legitimate repair either
  (a) scopes the question to what the stated design actually isolates, or
  (b) asks what the evidence does and does not license, or
  (c) asks what additional measurement/control would resolve the ambiguity and what each outcome
  would establish. Option (c) is often the hardest and is preferred when the original question
  demanded a determination the data cannot make.
* **Never assert a determination the data cannot support**, in the question, the reference answer
  OR any rubric fact. If the original conclusion is unsupported, the replacement's facts must state
  the limitation explicitly (e.g. "X is not identifiable from these data because ...").
* **Every quantitative premise must be traceable**: a number that appears in the source material,
  or computed only from numbers that share a baseline and are all stated in the stem. Never invent
  a number, never present an assumption as an observation, and never combine quantities across
  different baselines, contexts, cell types or species into a new number.
* Keep every architectural constraint: **3-5 rubric facts** at semantic granularity, stem under
  ~900 characters, ONE core reasoning target, self-contained (the respondent sees only the stem),
  and a "question_type" tag at the same tier or higher.

{question_spec}

Return the strict JSON array with exactly one object, "insight_index": {idx}, holding the single
replacement question.
"""


def build_question_spec(qtype, per_insight):
    """The same output-shape reminder the main prompt gives, for one question."""
    if qtype == "mcq":
        return ("The replacement is a multiple-choice question: four options A-D, each a "
                "conclusion+reasoning pair, with `correct_reasoning` and a `distractor_analysis` "
                "covering exactly the non-keyed options.")
    return ("The replacement is an open-ended question with a reference answer and a rubric of "
            "3-5 `facts` plus a `scoring_guide`.")


def repair_one(questions_data, qtype, idx, flattened, critique, model_call, output_dir,
               per_insight, dry_run=False):
    """Rewrite the question at 1-based `idx` in flattened order. Returns True if changed."""
    paper_id, insight_id = flattened[idx - 1]
    insight = questions_data[paper_id][insight_id]
    questions = insight.get(f"{qtype}_questions") or []
    if len(questions) != 1:
        print(f"  [{idx}] {paper_id}/{insight_id}: expected 1 question, found {len(questions)}; skipping.")
        return False
    current = questions[0]

    block = insight_prompt_block([(insight_id, insight)], start_index=1)
    block += (
        "\n\n* Current question for this insight (the one to replace):\n"
        + json.dumps({"question": current["question"], "answer": current["answer"],
                      "rubric": current.get("rubric", {})},
                     ensure_ascii=False, indent=2)
    )
    block += REPAIR_INSTRUCTIONS.format(idx=1, critique=critique,
                                        question_spec=build_question_spec(qtype, per_insight))

    template = mcq_rubric_prompt(1) if qtype == "mcq" else oe_rubric_prompt(1)
    if dry_run:
        prompt = template.replace("{insights}", block.strip())
        print(prompt)
        return False

    parsed = generate_checked(template, block.strip(), model_call, qtype, output_dir,
                              f"{paper_id}/{insight_id}")
    if not parsed or not parsed[0].get("questions"):
        print(f"  [{idx}] {paper_id}/{insight_id}: no parseable replacement; left unchanged.")
        return False

    replacement = parsed[0]["questions"]
    # the model is told to return exactly one; keep only the first to be safe
    insight[f"{qtype}_questions"] = replacement[:1]
    insight[f"{qtype}_question"] = render_legacy_text(replacement[:1], qtype)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions_json", required=True, help="canonical <qtype>_questions.json (updated in place)")
    ap.add_argument("--qtype", default="oe", choices=["oe", "mcq"])
    ap.add_argument("--critiques", required=True,
                    help='JSON: {"<1-based index>": "<reviewer critique verbatim>"}')
    ap.add_argument("--model_call", default="claude_code", choices=["gpt", "claude", "claude_code", "gateway"])
    ap.add_argument("--per-insight", type=int, default=1, choices=[1, 2], dest="per_insight")
    ap.add_argument("--force", action="store_true",
                    help="Re-repair items already marked as repaired in the sidecar log")
    ap.add_argument("--dry-run", type=int, default=None, metavar="IDX",
                    help="Print the assembled prompt for one item and exit")
    args = ap.parse_args()

    with open(args.questions_json, "r", encoding="utf-8") as f:
        questions_data = json.load(f)
    with open(args.critiques, "r", encoding="utf-8") as f:
        critiques = json.load(f)

    output_dir = os.path.dirname(os.path.abspath(args.questions_json))
    log_path = os.path.join(output_dir, f"repair_log_{args.qtype}.json")
    repaired = {}
    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            repaired = json.load(f)

    flattened = [(p, i) for p, paper in questions_data.items() for i in paper]
    targets = sorted(int(k) for k in critiques)
    if args.dry_run:
        repair_one(questions_data, args.qtype, args.dry_run, flattened,
                   critiques[str(args.dry_run)], args.model_call, output_dir,
                   args.per_insight, dry_run=True)
        return

    changed = 0
    for idx in targets:
        paper_id, insight_id = flattened[idx - 1]
        if repaired.get(str(idx)) and not args.force:
            print(f"  [{idx}] {paper_id}/{insight_id}: already repaired, skipping.")
            continue
        print(f"  [{idx}] {paper_id}/{insight_id}: repairing...")
        ok = repair_one(questions_data, args.qtype, idx, flattened, critiques[str(idx)],
                        args.model_call, output_dir, args.per_insight)
        if ok:
            changed += 1
            repaired[str(idx)] = {"paper_id": paper_id, "insight_id": insight_id}
            # persist immediately so a stall mid-run is resumable
            with open(args.questions_json, "w", encoding="utf-8") as f:
                json.dump(questions_data, f, indent=2, ensure_ascii=False)
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(repaired, f, indent=2, ensure_ascii=False)
        time.sleep(1)

    print(f"Repaired {changed} of {len(targets)} targeted item(s).")
    if changed or args.force:
        write_questions_txt(questions_data, args.qtype, args.questions_json)
        n_eval, eval_path = export_eval(args.questions_json, args.qtype)
        print(f"Eval export: {n_eval} item(s) -> '{eval_path}'")
        n_p, p_path = export_prompts_only(args.questions_json, args.qtype)
        print(f"Answer-free export: {n_p} prompt(s) -> '{p_path}'")


def write_questions_txt(questions_data, qtype, questions_json_path):
    """Refresh the human-readable side-by-side .txt next to the canonical file."""
    from insights_to_questions import render_rubric_block
    txt_path = os.path.splitext(questions_json_path)[0] + ".txt"
    out = ""
    for paper_id, paper in questions_data.items():
        out += f"Paper ID: {paper_id}\n"
        for i, (insight_id, insight) in enumerate(paper.items(), 1):
            out += f"\n#### Insight #{i} ({insight_id})\n*Summary:* {insight['summary']}\n"
            for n, q in enumerate(insight.get(f"{qtype}_questions") or [], 1):
                if qtype == "mcq":
                    options = "\n".join(f"{k}) {v}" for k, v in q["options"].items())
                    out += f"\n**Question{n}:** {q['question']}\n{options}\n**Answer{n}:** {q['answer']}\n"
                else:
                    out += f"\n**Question{n}:** {q['question']}\n\n**Answer{n}:** {q['answer']}\n"
                block = render_rubric_block(q.get("rubric", {}), qtype)
                if block:
                    out += f"\n**Rubric{n}:**\n{block}\n"
            out += "\n==========\n\n"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Rewrote '{txt_path}'.")


if __name__ == "__main__":
    main()
