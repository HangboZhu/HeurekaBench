"""Export the question-only evaluation file for agent review systems.

The canonical <qtype>_questions.json nests every question under its insight and
carries provenance fields (summary / how / relevant — paper-derived text) that
must never reach the model under test. This script flattens the set into
<qtype>_questions_eval.json: a bare JSON list in which each item holds ONLY
question / answer / rubric (plus options for MCQ), in the canonical file's
order.

Identity contract: the list order IS the question id (index 1..N). The nested
original stays on disk unchanged as the provenance/audit copy. The export runs
automatically at the end of insights_to_questions.py --structured and of every
difficulty_loop.py run (final canonical write); it can also be run standalone:

  python export_eval_questions.py <dir>/oe_questions.json --qtype oe
"""

import os
import json
import argparse
from collections import Counter


def export_eval(questions_json_path, q_type):
    """Write <qtype>_questions_eval.json next to the input.

    Returns (item count, output path). Also reports the question-type mix and
    stem-length stats (architecture guardrails from the generation prompts)."""
    with open(questions_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = []
    mix = Counter()
    lengths = []
    for paper in data.values():
        for insight in paper.values():
            for q in insight.get(f"{q_type}_questions", []) or []:
                item = {"question": q["question"]}
                if q_type == "mcq":
                    item["options"] = q["options"]
                item["answer"] = q["answer"]
                item["rubric"] = q.get("rubric", {})
                items.append(item)
                mix[str(q.get("question_type") or "untagged")] += 1
                lengths.append(len(q["question"]))
    out_path = os.path.splitext(questions_json_path)[0] + "_eval.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)
    if lengths:
        ordered = sorted(lengths)
        print(f"Type mix: {dict(mix)} | stem chars p50={ordered[len(ordered)//2]} "
              f"max={ordered[-1]} (target <=~900)")
    return len(items), out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("questions_json", type=str,
                        help="Canonical <qtype>_questions.json to flatten")
    parser.add_argument("--qtype", type=str, required=True, choices=["mcq", "oe"])
    args = parser.parse_args()
    n, out_path = export_eval(args.questions_json, args.qtype)
    print(f"Exported {n} bare {args.qtype} question(s) -> '{out_path}' "
          f"(list order = question id 1..{n}).")


if __name__ == "__main__":
    main()
