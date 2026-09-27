"""Generate LAB-Bench ProtocolQA items from an extracted protocol block.

The unit of generation is a block of procedure steps from protocol.json (see
extract_protocol.py), not an insight: a ProtocolQA item is a troubleshooting
question about the protocol text the respondent is handed, so the block IS the
source material and the `protocol` field of every exported entry. The prompt
and the item shape are documented in
prompts/insight2question_rubric_prompts.py::_PROTOCOLQA_TEMPLATE; the shape
constraints (question <= 479 chars, ideal <= 220 with a step reference, 3
distractors, one question per protocol) are read off the 108-item official
release.

Written next to protocol.json:

  protocolqa_questions.json   canonical, nested for provenance:
                              {paper_id: {Block3: {title, scope, doi,
                               step_range, protocol, protocolqa_questions:[…]}}}
  protocolqa_questions.txt    human-readable rendering
  protocolqa_questions_eval.json   flat list of {question, ideal, distractors,
                              rubric} in file order (identity = index 1..N)

One LLM call per block by default, which is what keeps responses small enough
that the transport does not drop their tail (the failure mode that makes
--per-insight 6 unusable for the MCQ pipeline); `--per-block` may ask for more
than one item per call, but two is the practical ceiling. Candidate-pool
production repeats the whole run in separate directories rather than raising
the count per call.

  python protocol_to_questions.py --protocol_json_path <dir>/protocol.json \
      --model_call gateway --per-block 1
"""

import os
import re
import json
import argparse
from collections import Counter

from prompts.insight2question_rubric_prompts import protocolqa_prompt
from insights_to_questions import generate_checked

BLOCK_PLACEHOLDER = "{insights}"        # the template's source-material slot
REQUIRED_KEYS = ("question", "ideal", "distractors")


def block_source(block, index, paper):
    """Render one protocol block as the prompt's source material.

    The respondent's text is quoted verbatim and marked as such, so the model
    cannot mistake the grounding notes below it for part of the protocol."""
    parts = [
        f"#### Protocol block #{index}",
        "",
        f"Source: {paper.get('title', '')} ({paper.get('doi', '')}), "
        f"steps {block.get('step_range') or 'unnumbered'}.",
        "",
        "**Protocol text — this is exactly what the respondent will read:**",
        "",
        block["protocol"],
    ]
    rows = block.get("troubleshooting") or []
    if rows:
        parts += [
            "",
            "**Reference notes, for your eyes only — the respondent does NOT see "
            "this.** These are the journal's own troubleshooting entries for this "
            "step range; a remedy they endorse is the safest reference answer:",
            "",
        ]
        for row in rows:
            parts.append(f"- step {row['step']}: problem: {row['problem']} | "
                         f"possible reason: {row['reason']} | solution: {row['solution']}")
    return "\n".join(parts)


def collect_questions(items, block_keys):
    """Attach each generated question to its block, dropping stray blocks.

    The model may return blocks in any order and may invent an index; an item
    whose index matches no block is reported rather than silently filed."""
    by_block, stray = {}, []
    for item in items or []:
        index = item.get("insight_index")
        if index is None or not (1 <= index <= len(block_keys)):
            stray.append(item)
            continue
        by_block.setdefault(block_keys[index - 1], []).extend(item["questions"])
    return by_block, stray


def write_outputs(output_dir, paper, blocks, generated):
    """Canonical nested file, text rendering and flat eval export."""
    paper_id = paper["paper_id"]
    nested = {}
    flat = []
    for key, block in blocks.items():
        questions = generated.get(key, [])
        nested[key] = {
            "title": block["title"],
            "scope": block.get("scope", ""),
            "doi": paper.get("doi", ""),
            "step_range": block.get("step_range"),
            "numbered": block.get("numbered", True),
            "protocol": block["protocol"],
            "protocolqa_questions": questions,
        }
        for q in questions:
            flat.append({"question": q["question"], "ideal": q["ideal"],
                         "distractors": q["distractors"],
                         "rubric": q.get("rubric", {})})
    payload = {paper_id: nested}

    json_path = os.path.join(output_dir, "protocolqa_questions.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    eval_path = os.path.join(output_dir, "protocolqa_questions_eval.json")
    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump(flat, f, indent=2, ensure_ascii=False)

    txt_path = os.path.join(output_dir, "protocolqa_questions.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        for key, entry in nested.items():
            f.write(f"## {paper_id} / {key} — {entry['title']} "
                    f"(steps {entry['step_range']})\n\n")
            f.write(entry["protocol"] + "\n\n")
            for i, q in enumerate(entry["protocolqa_questions"], 1):
                f.write(f"**Q{i} [{q.get('question_type', '?')}]:** {q['question']}\n\n")
                f.write(f"**Ideal:** {q['ideal']}\n\n")
                for j, d in enumerate(q["distractors"], 1):
                    f.write(f"  distractor {j}: {d}\n")
                rubric = q.get("rubric") or {}
                if rubric.get("ideal_justification"):
                    f.write(f"\n**Why the ideal is right:** {rubric['ideal_justification']}\n")
                for k, why in (rubric.get("distractor_analysis") or {}).items():
                    f.write(f"**Why distractor {k} is wrong:** {why}\n")
                if rubric.get("source_evidence"):
                    f.write(f"**Source:** {rubric['source_evidence']}\n")
                f.write("\n" + "-" * 78 + "\n\n")
    return len(flat), json_path, txt_path, eval_path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--protocol_json_path", required=True)
    parser.add_argument("--model_call", default="gateway",
                        choices=["gpt", "claude", "claude_code", "gateway"])
    parser.add_argument("--per-block", type=int, default=1,
                        help="Questions per protocol block per call (1-4; 2 is the "
                             "practical ceiling before responses risk tail truncation)")
    parser.add_argument("--include-unnumbered", action="store_true",
                        help="Also generate for blocks with no numbered steps (the Cu "
                             "paper's option-written Procedure 3). Off by default: the "
                             "reference remedies of this subset name a step number in "
                             "96 of 108 official items.")
    parser.add_argument("--blocks", default="",
                        help="Comma-separated subset of block keys, for reruns "
                             "(e.g. Block3,Block7)")
    args = parser.parse_args()

    with open(args.protocol_json_path, "r", encoding="utf-8") as f:
        paper = json.load(f)
    paper_id = paper["paper_id"]
    output_dir = os.path.dirname(os.path.abspath(args.protocol_json_path))

    wanted = [b.strip() for b in args.blocks.split(",") if b.strip()]
    blocks = {
        key: block for key, block in paper["blocks"].items()
        if (block.get("numbered") or args.include_unnumbered)
        and (not wanted or key in wanted)
    }
    if not blocks:
        raise SystemExit("ERROR: no blocks to generate from — every block is either "
                         "unnumbered (pass --include-unnumbered) or filtered out.")
    skipped = [key for key in paper["blocks"] if key not in blocks]
    print(f"{paper_id}: {len(blocks)} block(s) to generate from; skipped {skipped} "
          f"(unnumbered or filtered).")

    prompt = protocolqa_prompt(args.per_block)
    block_keys = list(blocks)
    generated, stats = {}, Counter()
    for index, key in enumerate(block_keys, 1):
        block = blocks[key]
        context = f"{paper_id}/{key} ({block['title'][:40]})"
        items = generate_checked(prompt, block_source(block, index, paper),
                                 args.model_call, "protocolqa", output_dir, context,
                                 placeholder=BLOCK_PLACEHOLDER, allow_empty=True)
        mapped, stray = collect_questions(items, block_keys)
        for q in mapped.get(key, []):
            stats["questions"] += 1
            stats[f"type:{q.get('question_type', 'untagged')}"] += 1
            if not str(q["question"]).rstrip().endswith("?"):
                stats["not-a-question"] += 1
            if not re.search(r"[Ss]teps?\s*\d", q["ideal"]):
                stats["ideal-without-step-reference"] += 1
            if len(q["distractors"]) != 3:
                stats[f"distractors:{len(q['distractors'])}"] += 1
        if stray:
            stats["stray-block-index"] += len(stray)
            print(f"  WARNING: {len(stray)} item(s) came back with a block index "
                  f"outside 1..{len(block_keys)}; discarded.")
        generated.update(mapped)
        if not mapped.get(key):
            stats["blocks-without-questions"] += 1
            print(f"  {context}: no question returned (model judged the block "
                  f"ungrounded, or the block index was wrong).")

    count, json_path, txt_path, eval_path = write_outputs(output_dir, paper, blocks,
                                                         generated)
    print(f"\nGenerated {count} ProtocolQA item(s) over {len(block_keys)} block(s).")
    print(f"  canonical -> {json_path}")
    print(f"  readable  -> {txt_path}")
    print(f"  flat eval -> {eval_path}")
    print(f"  mix: {dict(stats)}")
    if stats["blocks-without-questions"]:
        print("  NOTE: blocks with no question are legitimate when the block offers no "
              "defensible problem-remedy pair, but a high share hints the prompt's "
              "grounding rules are too strict for this paper.")


if __name__ == "__main__":
    main()
