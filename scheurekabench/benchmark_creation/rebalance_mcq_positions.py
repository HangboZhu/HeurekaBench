"""Rebalance the keyed-answer positions across an MCQ pack.

Generating questions one insight at a time leaves the model unable to balance
answer positions pack-wide, so the key drifts into a single slot — measured
7/10 keyed at B on the Aging-Res-0911 pack after three hardening rounds, i.e.
a constant-"B" guess scoring the top of the target band without any reasoning.
Length parity and prompt-side position hints only go so far; this fixes the
distribution deterministically after the fact.

Option position carries no information about correctness, so for every question
the options are permuted to move the keyed option(s) into a target slot, and
the permutation is applied everywhere a letter is referenced:

  * the "answer" key
  * the "options" mapping
  * every "rubric.distractor_analysis" key
  * letter references inside rubric prose ("option B", "(C)", "option A's …")
  * the rendered legacy '<qtype>_question' text

Stems are never touched. Writes the pack back in place and re-exports the eval
file, so run it after any MCQ generation/loop pass:

  python benchmark_creation/rebalance_mcq_positions.py <dir>/mcq_questions.json
"""

import argparse
import json
import os
import re
import sys

from insights_to_questions import render_legacy_text
from export_eval_questions import export_eval

LETTERS = "ABCD"


def build_permutation(keyed, target):
    """{old_letter: new_letter}: keyed letters go to their target slots, the
    rest fill the remaining slots in order."""
    old_letters = list(keyed) + [l for l in LETTERS if l not in keyed]
    new_letters = list(target) + [l for l in LETTERS if l not in target]
    return dict(zip(old_letters, new_letters))


LETTER_REF_RE = re.compile(r"\b(?:option|options|choice|answer)s?\s*\(?([A-D])\)?|\(([A-D])\)")


def remap_prose(text, perm):
    """Rewrite option-letter references in rubric prose under `perm`. One pass
    only: a second pass would permute the already-rewritten letters again."""
    if not text:
        return text

    def repl(match):
        if match.group(1):  # "option B" / "options (B)"
            return match.group(0).replace(match.group(1), perm[match.group(1)], 1)
        return f"({perm[match.group(2)]})"  # "(C)"

    return LETTER_REF_RE.sub(repl, text)


def rebalance_pack(data, qtype="mcq"):
    """Rebalance every question in a {paper: {insight: {...}}} pack in place."""
    slots = [(paper_id, insight_id, q)
             for paper_id, paper in data.items()
             for insight_id, insight in paper.items()
             for q in (insight.get(f"{qtype}_questions") or [])]
    moved = 0
    for idx, (_, _, q) in enumerate(slots):
        options = q.get("options") or {}
        keyed = [l for l in LETTERS
                 if l in {a.strip().upper() for a in str(q.get("answer", "")).split(",")}]
        if not options or not keyed or len(keyed) == len(LETTERS):
            continue
        # Cycle the keyed options across A/B/C/D so no slot is over-represented.
        target = [LETTERS[(idx + offset) % len(LETTERS)] for offset in range(len(keyed))]
        if keyed == target:
            continue
        perm = build_permutation(keyed, target)
        q["options"] = {perm[old]: text for old, text in options.items()}
        q["answer"] = ",".join(perm[a] for a in keyed)
        rubric = q.get("rubric") or {}
        analysis = rubric.get("distractor_analysis")
        if isinstance(analysis, dict):
            rubric["distractor_analysis"] = {
                perm.get(k.strip().upper(), k): v for k, v in analysis.items()
            }
        for field in ("correct_reasoning", "scoring_guide"):
            if isinstance(rubric.get(field), str):
                rubric[field] = remap_prose(rubric[field], perm)
        if isinstance(rubric.get("distractor_analysis"), dict):
            rubric["distractor_analysis"] = {
                k: remap_prose(v, perm) if isinstance(v, str) else v
                for k, v in rubric["distractor_analysis"].items()
            }
        moved += 1
    # Re-render the legacy text so evaluate_agent_answer.py sees the new letters.
    for _, paper in data.items():
        for _, insight in paper.items():
            questions = insight.get(f"{qtype}_questions") or []
            if questions:
                insight[f"{qtype}_question"] = render_legacy_text(questions, qtype)
    return moved


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("questions_json", help="path to <qtype>_questions.json (updated in place)")
    ap.add_argument("--qtype", default="mcq", choices=["mcq"])
    args = ap.parse_args()

    with open(args.questions_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    moved = rebalance_pack(data, args.qtype)
    with open(args.questions_json, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"Rebalanced {moved} question(s) in '{args.questions_json}'.")
    n_eval, eval_path = export_eval(args.questions_json, args.qtype)
    print(f"Eval export: {n_eval} bare {args.qtype} question(s) -> '{eval_path}'")


if __name__ == "__main__":
    main()
