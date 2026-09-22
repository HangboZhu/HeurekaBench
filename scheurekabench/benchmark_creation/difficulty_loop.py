"""Iterative difficulty tuning for generated questions.

Implements the README's cleanup pass as a closed loop:
  1. Solve every question with a reference solver (no article access) and
     grade it (MCQ: exact letter-set match; OE: LLM grader vs rubric facts).
  2. Questions the solver handled too easily (MCQ correct / OE rating >=
     --keep-rating) are regenerated HARDER (conclusion+reason MCQ options,
     multi-premise OE derivations); already-hard questions are copied verbatim.
     The regen prompt quotes every rubric fact a solver CONTRADICTED or only
     hedged on (proven misconceptions to exploit); with several --solver
     models the error union across solvers is used, so replacements are
     hardened against every reference solver's known weak points.
  3. Repeat until the aggregate score lands inside the target range
     (MCQ exact-match in [--target-min, --target-max]; OE mean rating in
     [--oe-target-min, --oe-target-max]) or --max-rounds is reached.

Each round's question set is snapshotted to <qtype>_questions_round<N>.json;
the round closest to target becomes the canonical <qtype>_questions.json
(plus the legacy text rendering and .txt). Regeneration runs through the
same backends as insights_to_questions.py (--model_call); solving/grading go
through the gateway (utils/llm_gateway.py).

Example:
  python difficulty_loop.py --qtype mcq \
      --questions_json <dir>/mcq_questions.json \
      --insight_json_path <dir>/insights.json \
      --model_call claude_code --solver MiniMax-M3 \
      --target-min 0.5 --target-max 0.7
  python difficulty_loop.py --qtype oe \
      --questions_json <dir>/oe_questions.json \
      --insight_json_path <dir>/insights.json \
      --model_call claude_code --solver MiniMax-M3 --grader gpt-5.6-luna \
      --oe-target-min 2.5 --oe-target-max 3.5 \
      --seed_solved "MiniMax-M3=<dir>/oe_solved_minimax.json"
"""

import os
import json
import copy
import argparse
import time

from insights_to_questions import (
    extract_json,
    generate_raw,
    validate_structured,
    find_source_leakage,
    render_legacy_text,
    render_rubric_block,
)
from prompts.insight2question_rubric_prompts import mcq_rubric_prompt, oe_rubric_prompt
from solve_and_grade import solve_mcq, grade_mcq, solve_oe, grade_oe
from export_eval_questions import export_eval


def qtext(q, q_type):
    """Compact JSON-ish rendering of one question for the regen prompt."""
    if q_type == "mcq":
        body = json.dumps(
            {"question": q["question"], "options": q["options"], "answer": q["answer"],
             "rubric": q.get("rubric", {})},
            indent=1, ensure_ascii=False,
        )
    else:
        body = json.dumps(
            {"question": q["question"], "answer": q["answer"], "rubric": q.get("rubric", {})},
            indent=1, ensure_ascii=False,
        )
    return body


def outcome(q, rec, q_type):
    """'CORRECT' / 'WRONG' / 'UNANSWERED' for MCQ; '<n>/5' or 'UNANSWERED' for OE."""
    if not (rec.get("raw_answer") or "").strip():
        return "UNANSWERED"
    if q_type == "mcq":
        return "CORRECT" if rec["grade"]["correct"] else "WRONG"
    return f"{rec['grade']['rating']}/5"


def question_outcome(recs, q_type):
    """Multi-solver summary for one question, e.g.
    'CORRECT by 2/3 (MiniMax-M3, glm-5.3-flash); deepseek-v4-flash: WRONG'."""
    parts = []
    for model, rec in recs.items():
        parts.append(f"{model}: {outcome(None, rec, q_type)}")
    return " | ".join(parts) if parts else "UNANSWERED"


def too_easy(rec, q_type, keep_rating):
    if not (rec.get("raw_answer") or "").strip():
        return False
    if q_type == "mcq":
        return bool(rec["grade"]["correct"])
    return rec["grade"]["rating"] >= keep_rating


def question_too_easy(recs, q_type, keep_rating):
    """A question counts as too easy when ANY reference solver handled it."""
    return any(too_easy(rec, q_type, keep_rating) for rec in recs.values())


def run_round(questions_data, q_type, solvers, grader, seed=None):
    """Solve + grade every question with EVERY solver.
    Returns ({key: {model: rec}}, aggregate score = mean of per-solver scores).
    `seed` ({model: {key: rec}}) supplies cached solve_and_grade results —
    seeded questions are neither re-solved nor re-graded, but their recorded
    grades still count toward this round's aggregate score."""
    seed = seed or {}
    results = {}
    per_solver = {m: [0, 0, 0] for m in solvers}  # mcq: [correct, answered, _]; oe: [ratings, answered, _]
    for paper_id, paper in questions_data.items():
        for insight_id, insight in paper.items():
            for idx, q in enumerate(insight.get(f"{q_type}_questions", []) or [], 1):
                key = f"{paper_id}/{insight_id}/Q{idx}"
                results[key] = {}
                for solver in solvers:
                    cached = seed.get(solver, {}).get(key)
                    if cached and (cached.get("raw_answer") or "").strip() and cached.get("grade"):
                        rec = cached
                        print(f"  {key}: reusing cached '{solver}' answer (grade {outcome(q, rec, q_type)}).")
                    else:
                        print(f"  {key}: solving with '{solver}'...")
                        solve = solve_mcq if q_type == "mcq" else solve_oe
                        raw = solve(q, solver)
                        if not raw.strip():  # gateway hiccup: one retry after a pause
                            time.sleep(10)
                            raw = solve(q, solver)
                        rec = {"raw_answer": raw}
                    if (rec.get("raw_answer") or "").strip():
                        if q_type == "mcq":
                            if "grade" not in rec:
                                rec["grade"] = grade_mcq(q, rec["raw_answer"])
                            per_solver[solver][0] += bool(rec["grade"]["correct"])
                            per_solver[solver][1] += 1
                        else:
                            if "grade" not in rec:
                                rating, labels, grader_raw = grade_oe(q, rec["raw_answer"], grader)
                                rec["grade"] = {"rating": rating,
                                                "fact_labels": {f"F{n}": l for n, l in sorted(labels.items())}}
                                rec["grader_raw"] = grader_raw
                            if rec["grade"].get("rating"):
                                per_solver[solver][0] += rec["grade"]["rating"]
                                per_solver[solver][1] += 1
                            print(f"    {solver} rating={rec['grade'].get('rating')}")
                    results[key][solver] = rec
                    if not (cached and "grade" in cached):
                        time.sleep(1)
    scores = [s[0] / s[1] if s[1] else 0.0 for s in per_solver.values()]
    for solver, s in per_solver.items():
        print(f"  [{solver}] score this round: {s[0] / s[1] if s[1] else 0.0:.2f}")
    return results, (sum(scores) / len(scores) if scores else 0.0)


def collect_errors(recs, q):
    """Proven solver weak points on one question: ground-truth facts a solver
    CONTRADICTED (INCORRECT) or only hedged on (PARTIAL), across all solvers.
    These are the misconceptions a replacement question should exploit."""
    facts = (q.get("rubric") or {}).get("facts") or []
    errors = []
    for model, rec in sorted(recs.items()):
        labels = (rec.get("grade") or {}).get("fact_labels") or {}
        for fno, label in sorted(labels.items(), key=lambda kv: int(kv[0][1:])):
            n = int(fno[1:])
            text = facts[n - 1] if 0 < n <= len(facts) else "(fact text unavailable)"
            if label == "INCORRECT":
                errors.append(f"[{model}] flatly contradicted GT fact F{n}: {text!r}")
            elif label == "PARTIAL":
                errors.append(f"[{model}] only hedged on GT fact F{n}: {text!r}")
    return errors


def build_insight_prompt(idx, paper_id, insight_id, insight, results, q_type, keep_rating, per_insight=2):
    """Base rubric prompt ({insights} = ONE insight + its questions + solver
    performance) + hardening instructions. Small per-insight prompts parse
    far more reliably than one giant all-insights prompt."""
    block = (
        f"#### Insight #{idx}\n\n* Summary: {insight['summary']}\n\n"
        f"* How it was derived: {insight['how']}\n\n"
        f"* Associated paragraphs from the paper: {insight['relevant']}\n\n"
        f"* Current questions and solver performance (solver had NO article access):\n"
    )
    for qno, q in enumerate(insight.get(f"{q_type}_questions", []) or [], 1):
        recs = results.get(f"{paper_id}/{insight_id}/Q{qno}", {})
        block += (
            f"  Q{qno} [solver: {question_outcome(recs, q_type)}]:\n{qtext(q, q_type)}\n"
            f"  Solvers' answers began with: "
            + " || ".join(
                f"{m}: {((rec.get('raw_answer') or '')[:300])!r}" for m, rec in recs.items()
            ) + "\n"
        )
        weak = collect_errors(recs, q)
        if weak:
            block += ("  PROVEN solver weak points on Q%d (facts a solver contradicted or only "
                      "hedged on):\n" % qno + "\n".join(f"    - {w}" for w in weak) + "\n")
    if q_type == "mcq":
        # Regeneration happens one insight at a time, so the model cannot
        # balance answer positions across the pack and left alone it parks
        # them in one slot (observed: 7/10 keyed at B after three rounds, i.e.
        # a constant-B guess scoring the top of the target band). Position
        # carries no information, so assign it here, cycling by insight index.
        block += (
            f"\n\n* **Answer position for the replacement:** if it has a single keyed "
            f"option, place that option at position {'ABCD'[(idx - 1) % 4]} — the pack "
            f"assigns positions so the key is not concentrated in one slot."
        )
    template = mcq_rubric_prompt(per_insight) if q_type == "mcq" else oe_rubric_prompt(per_insight)
    prompt = template.replace("{insights}", block.strip())
    verdict_rule = 'marked CORRECT' if q_type == "mcq" else f'with rating >= {keep_rating}/5'
    n_word = "two" if per_insight == 2 else "one"
    s = "s" if per_insight == 2 else ""
    ladder_note = (
        "Q1 lower tier / Q2 higher tier split"
        if per_insight == 2
        else "the single question must stay the higher tier (counterfactual/multi_hop)"
    )
    prompt += f"""

---

### ADDITIONAL TASK FOR THIS RUN: HARDEN THE QUESTION SET

You saw this insight's CURRENT questions and how a strong solver model (with NO access to the article) performed on them, including the beginning of its reasoning.

* **Replace** every question {verdict_rule} with a NEW, substantially harder question about the same insight. Do not merely reword the old question: change what reasoning it demands.
* **Copy VERBATIM** (question, options, answer, rubric — unchanged) every question the solver got wrong, rated below the threshold, or did not answer.
* To make a question genuinely harder: demand that the respondent combine at least two premises stated in the stem (or compute from the stated quantities); make each distractor defensible under a naive single-premise reading;
"""
    if q_type == "mcq":
        prompt += """ ensure the keyed option is not the only balanced-sounding one, and include a distractor with a right conclusion but wrong reasoning.
"""
    else:
        prompt += """ and design the reference answer so that knowledge-only respondents can produce at most a partial answer.
"""
    prompt += f"""
* The solver's quoted reasoning shows HOW it succeeded. Design the replacement so that exactly that style of reasoning is no longer sufficient — e.g. add a quantitative comparison, an exception the reasoner must notice, or a second hop that the first-pass reading misses.
* Where PROVEN solver weak points are listed (ground-truth facts a solver contradicted or only hedged on), exploit them: design the replacement so its keyed facts directly refute those misconceptions. A solver repeating the same error must score INCORRECT/MISSING on the affected facts, and an answer that merely lists possibilities without committing must stay PARTIAL at best.
* To make a question harder, PREFER raising its reasoning tier — turn an extraction/comparison question into multi_hop or counterfactual ("if condition X were removed / group Y excluded, does the conclusion still hold?") — rather than adding more scoring facts or lengthening the stem.
* Keep every architectural constraint from the main instructions intact in replacements: exactly 3-5 rubric facts per question at semantic granularity (never 6+, never wording-level splits); stems under ~900 characters; one core reasoning target per question; {ladder_note}; keep or upgrade the "question_type" tag.

All other rules from the main instructions (self-containment, option structure, rubric format, exactly {n_word} question{s} per insight, strict JSON array output) remain unchanged.
"""
    return prompt


def regen_insight_questions(prompt, q_type, model_call, raw_dump_path):
    """One regen call + parse + leak-check with one corrective retry.
    Returns the questions list (possibly empty on failure); dumps raw output
    for diagnosis when nothing parses."""
    raw = generate_raw(prompt, model_call)
    if not (raw or "").strip():  # empty gateway response: one retry after a pause
        time.sleep(10)
        raw = generate_raw(prompt, model_call)
    items = validate_structured(extract_json(raw), q_type)
    leaks = find_source_leakage(items)
    if leaks:
        feedback = "\n".join(f'- question {no}: "{match}"' for _, no, match, _ in leaks)
        retry = prompt + (
            "\n\n### CORRECTION FEEDBACK\nYour draft contained questions referencing the source article "
            "(forbidden). Regenerate BOTH questions fully self-contained, fixing:\n" + feedback
        )
        items2 = validate_structured(extract_json(generate_raw(retry, model_call)), q_type)
        if items2 is not None and len(find_source_leakage(items2)) < len(leaks):
            items, leaks = items2, find_source_leakage(items2)
    if items is None:
        with open(raw_dump_path, "w", encoding="utf-8") as f:
            f.write(prompt[-2000:] + "\n\n=== RAW OUTPUT ===\n(empty)")
        return []
    qs = []
    for it in items:
        qs.extend(it["questions"])
    if leaks:  # drop still-leaking questions; caller backfills from the previous round
        leaked = {no for _, no, _, _ in leaks}
        qs = [q for n, q in enumerate(qs, 1) if n not in leaked]
    return qs


def normalize_items(items, questions_data, q_type, prev_data):
    """Guarantee one entry per insight with >=2 questions: pull verbatim
    replacements from the previous round where the regen output is short."""
    by_index = {}
    for it in items or []:
        if it.get("insight_index"):
            by_index[it["insight_index"]] = list(it["questions"])
    normalized = []
    for idx, (paper_id, insight_id, insight) in enumerate(
        [(p, i, ins) for p, paper in questions_data.items() for i, ins in paper.items()], 1
    ):
        new_qs = by_index.get(idx, [])
        prev_qs = (prev_data.get(paper_id, {}).get(insight_id, {}) or {}).get(f"{q_type}_questions", []) or []
        while len(new_qs) < 2 and len(prev_qs) > len(new_qs):
            new_qs.append(prev_qs[len(new_qs)])
        normalized.append({"insight_index": idx, "questions": new_qs})
    return normalized


def apply_items(questions_data, items, q_type):
    """Write regen items back into the canonical questions structure."""
    data = copy.deepcopy(questions_data)
    for (paper_id, insight_id), item in zip(
        [(p, i) for p, paper in data.items() for i in paper], items
    ):
        qs = item["questions"]
        data[paper_id][insight_id][f"{q_type}_questions"] = qs
        data[paper_id][insight_id][f"{q_type}_question"] = (
            render_legacy_text(qs, q_type) if qs else "No question generated."
        )
    return data


def write_questions(data, q_type, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    txt_path = os.path.splitext(path)[0] + ".txt"
    with open(txt_path, "w", encoding="utf-8") as out:
        for paper_id, paper in data.items():
            out.write(f"Paper ID: {paper_id}\n")
            for idx, (insight_id, insight) in enumerate(paper.items(), 1):
                out.write(f"\n#### Insight #{idx} ({insight_id})\n*Summary:* {insight['summary']}\n")
                for i, q in enumerate(insight.get(f"{q_type}_questions", []) or [], 1):
                    if q_type == "mcq":
                        options = "\n".join(f"{letter}) {text}" for letter, text in q["options"].items())
                        out.write(f"\n**Question{i}:** {q['question']}\n{options}\n**Answer{i}:** {q['answer']}\n")
                    else:
                        out.write(f"\n**Question{i}:** {q['question']}\n\n**Answer{i}:** {q['answer']}\n")
                    rubric_block = render_rubric_block(q.get("rubric", {}), q_type)
                    if rubric_block:
                        out.write(f"\n**Rubric{i}:**\n{rubric_block}\n")
                out.write("\n==========\n\n")


def distance(score, lo, hi):
    return 0.0 if lo <= score <= hi else min(abs(score - lo), abs(score - hi))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions_json", type=str, required=True,
                        help="Canonical <qtype>_questions.json (updated in place at the end)")
    parser.add_argument("--insight_json_path", type=str, required=True,
                        help="insights.json backing the questions (summary/how/relevant used in regen)")
    parser.add_argument("--qtype", type=str, required=True, choices=["mcq", "oe"])
    parser.add_argument("--model_call", type=str, default="claude_code",
                        choices=["gpt", "claude", "claude_code", "gateway"])
    parser.add_argument("--solver", type=str, required=True,
                        help="Gateway model(s) used to measure difficulty; comma-separated list "
                             "runs every solver on every question (a question is too easy if ANY "
                             "solver handles it; the aggregate score is the per-solver mean)")
    parser.add_argument("--grader", type=str, default=None, help="[oe] Gateway grader model")
    parser.add_argument("--target-min", type=float, default=0.5, help="[mcq] Lower exact-match target")
    parser.add_argument("--target-max", type=float, default=0.7, help="[mcq] Upper exact-match target")
    parser.add_argument("--oe-target-min", type=float, default=2.5, help="[oe] Lower mean-rating target")
    parser.add_argument("--oe-target-max", type=float, default=3.5, help="[oe] Upper mean-rating target")
    parser.add_argument("--keep-rating", type=int, default=4,
                        help="[oe] Questions rated >= this are regenerated (default 4)")
    parser.add_argument("--seed_solved", type=str, default=None,
                        help="SOLVER=PATH[,SOLVER=PATH...] cached solve_and_grade results "
                             "reused as round-1 answers (no re-solving, grades still count); "
                             "keys missing from the cache are solved normally")
    parser.add_argument("--max-rounds", type=int, default=4, help="Solve/regen iterations (default 4)")
    parser.add_argument("--per-insight", type=int, default=2, choices=[1, 2], dest="per_insight",
                        help="Questions per insight the pack holds (default 2); with 1 the regeneration "
                             "keeps the single question at the higher reasoning tier.")
    args = parser.parse_args()
    if args.qtype == "oe" and not args.grader:
        parser.error("--grader is required for --qtype oe.")
    lo, hi = (args.target_min, args.target_max) if args.qtype == "mcq" else (args.oe_target_min, args.oe_target_max)

    # The questions file already carries summary/how/relevant per insight; the
    # insights.json argument is accepted for CLI symmetry and future use.
    with open(args.questions_json, "r", encoding="utf-8") as f:
        questions_data = json.load(f)

    best = None  # (distance, round, data, score)
    solvers = [m.strip() for m in args.solver.split(",") if m.strip()]
    seed = {}
    if args.seed_solved:
        for pair in args.seed_solved.split(","):
            solver, _, path = pair.partition("=")
            with open(path.strip(), "r", encoding="utf-8") as f:
                cached = json.load(f)
            usable = {k: v for k, v in cached.items()
                      if (v.get("raw_answer") or "").strip() and v.get("grade")}
            seed[solver.strip()] = usable
            print(f"Seeding round 1: {len(usable)} cached '{solver.strip()}' result(s) from '{path.strip()}'")
    for rnd in range(1, args.max_rounds + 1):
        print(f"\n=== Round {rnd}: solving {args.qtype} questions with {solvers} ===")
        results, score = run_round(questions_data, args.qtype, solvers, args.grader,
                                   seed=seed if rnd == 1 else None)
        solved_path = os.path.splitext(args.questions_json)[0] + f"_solved_round{rnd}.json"
        with open(solved_path, "w", encoding="utf-8") as f:  # audit trail for every round
            json.dump(results, f, indent=2, ensure_ascii=False)
        n_valid = sum(
            1 for recs in results.values() for rec in recs.values()
            if (rec.get("raw_answer") or "").strip()
            and (args.qtype == "mcq" or rec.get("grade", {}).get("rating"))
        )
        if n_valid == 0:
            raise SystemExit(
                "ERROR: no question produced a valid answer/grade this round — the solver or "
                "grader model is unavailable (gateway outage?). Aborting so an all-zero score "
                "cannot be mistaken for 'perfectly hard' questions."
            )
        unit = "exact-match" if args.qtype == "mcq" else "mean rating"
        print(f"Round {rnd}: {unit} (mean over solvers) = {score:.2f} (target {lo}-{hi})")
        dist = distance(score, lo, hi)
        if best is None or dist <= best[0]:  # ties -> latest (most hardened) round
            best = (dist, rnd, copy.deepcopy(questions_data), score)
        if dist == 0.0:
            print("Target reached.")
            break
        if rnd == args.max_rounds:
            break

        print(f"Round {rnd}: regenerating too-easy questions per insight with '{args.model_call}'...")
        n_easy = sum(
            1 for recs in results.values()
            if question_too_easy(recs, args.qtype, args.keep_rating)
        )
        items = []
        n_regen = n_kept = 0
        insights_flat = [(p, i, ins) for p, paper in questions_data.items() for i, ins in paper.items()]
        for idx, (paper_id, insight_id, insight) in enumerate(insights_flat, 1):
            qs = insight.get(f"{args.qtype}_questions", []) or []
            needs_regen = any(
                question_too_easy(results.get(f"{paper_id}/{insight_id}/Q{qno}", {}), args.qtype, args.keep_rating)
                for qno in range(1, len(qs) + 1)
            )
            if not needs_regen:
                items.append({"insight_index": idx, "questions": list(qs)})
                n_kept += len(qs)
                continue
            prompt = build_insight_prompt(idx, paper_id, insight_id, insight, results,
                                          args.qtype, args.keep_rating, args.per_insight)
            dump = os.path.join(
                os.path.dirname(args.questions_json), f"{args.qtype}_regen_raw_insight{idx}.txt"
            )
            new_qs = regen_insight_questions(prompt, args.qtype, args.model_call, dump)
            if not new_qs:
                print(f"  Insight {idx}: regen unparseable (raw dumped to '{dump}'); keeping previous questions.")
                new_qs = list(qs)
            items.append({"insight_index": idx, "questions": new_qs})
            n_regen += len(new_qs)
            print(f"  Insight {idx}: {len(new_qs)} question(s) after regen.")

        items = normalize_items(items, questions_data, args.qtype, prev_data=questions_data)
        changed = sum(
            1 for (p, i, ins_old), it in zip(insights_flat, items)
            for q_old, q_new in zip(ins_old.get(f"{args.qtype}_questions", []) or [], it["questions"])
            if q_old.get("question") != q_new.get("question")
        )
        questions_data = apply_items(questions_data, items, args.qtype)
        snap = os.path.splitext(args.questions_json)[0] + f"_round{rnd + 1}.json"
        write_questions(questions_data, args.qtype, snap)
        print(f"Round {rnd}: {n_easy} too-easy question(s), {changed} actually changed; "
              f"snapshot '{snap}'.")
        if changed == 0:
            print(f"Round {rnd}: regeneration produced no changes; stopping to avoid a futile loop.")
            break

    dist, rnd, data, score = best
    print(f"\nBest round: {rnd} ({'exact-match' if args.qtype == 'mcq' else 'mean rating'} "
          f"{score:.2f}, distance to target {dist:.2f}). Writing canonical file.")
    write_questions(data, args.qtype, args.questions_json)
    leaks = find_source_leakage([
        {"insight_index": n, "questions": ins.get(f"{args.qtype}_questions", [])}
        for n, (_, ins) in enumerate(next(iter(data.values())).items(), 1)
    ])
    n_questions = sum(
        len(ins.get(f"{args.qtype}_questions", []) or []) for paper in data.values() for ins in paper.values()
    )
    print(f"Final: {n_questions} {args.qtype} questions | source leaks: {len(leaks)}")

    n_eval, eval_path = export_eval(args.questions_json, args.qtype)  # bare Q/A/rubric for agent review
    print(f"Eval export: {n_eval} bare question(s) -> '{eval_path}'")


if __name__ == "__main__":
    main()
