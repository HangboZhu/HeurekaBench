"""Score an exported LAB-Bench file with the official LAB-Bench evaluation.

Replicates the grading pipeline of Future-House/lab-bench's CI (promptfoo
config + .github/assert.py) on top of our exported bench:

  prompt   "Q: {question}\n\nOptions:\nA) ...\n...\nE) unsure\n\nAnswer:"
  parse    first capital letter followed by a closing paren — regex
           "([A-Z])\\)" with re.DOTALL — falling back to the first character
           of the first whitespace token, uppercased
  score    letter == ideal letter -> 1.0 Correct
           letter == unsure letter -> 0.1 Unsure (1.0 when ideal is "NULL",
           which never happens in our exports)
           otherwise -> 0.0 Incorrect

The "E) unsure" option mirrors the official unsure mechanism. Solving is
closed-book (the questions are self-contained by construction — same policy
as the difficulty loop's solver). Every solver's per-question raw output,
parsed letter and score land in labbench_solved_<model>.json and are re-used
on rerun (resume by question index).

Format repair: the official prompt asks for a bare letter, so models that
answer in markdown ("**Answer: C**") defeat the official parser (no "X)"
match; the first-token fallback then yields "*" and the answer scores 0.0 —
an artifact of output formatting, not of knowledge. When the strict parse
yields no valid option letter, the solver is asked once to restate its
previous answer as a single letter and the official parser/grading run again
on that reply. Both numbers are kept: score_strict (raw reply, exactly what
the official CI would get) and score (post-repair, used for the easy-question
decision).

--drop-all-correct then removes the questions EVERY solver answered correctly
(official score 1.0 across the board) as too easy, writing
<bench>_filtered.json plus the matching filtered sidecar.

  python solve_labbench.py data/labbench/litqa2_bench.json \
      --solver MiniMax-M3,deepseek-v4-flash --drop-all-correct
"""

import os
import re
import json
import random
import argparse
from utils import llm_gateway

UNSURE_TEXT = "unsure"


def build_prompt(question, options, unsure_letter):
    """Official promptfoo prompt shape, with the unsure option appended."""
    lines = [f"{letter}) {text}" for letter, text in options.items()]
    lines.append(f"{unsure_letter}) {UNSURE_TEXT}")
    return (f"Q: {question}\n\nOptions:\n" + "\n".join(lines) + "\n\nAnswer:")


def extract_answer(output):
    """Official answer parser (assert.py): 'X)' first, else first token char."""
    if not output:
        return ""
    match = re.search(r"([A-Z])\)", output, re.DOTALL)
    if match:
        return match.group(1)
    tokens = output.split()
    return tokens[0][0].upper() if tokens else ""


def grade(letter, ideal_letter, unsure_letter):
    """Official scoring: 1.0 Correct / 0.1 Unsure / 0.0 Incorrect.

    Official semantics for ideal == "NULL" (unanswerable questions): only the
    unsure option scores 1.0; any committed letter scores 0.0."""
    result = (letter or "").strip().upper()
    if str(ideal_letter).strip().upper() == "NULL":
        return (1.0, "Correct") if result == unsure_letter else (0.0, "Incorrect")
    if result and result == ideal_letter:
        return 1.0, "Correct"
    if result == unsure_letter:
        return 0.1, "Unsure"
    return 0.0, "Incorrect"


def synth_sidecar(bench, seed):
    """Build the letter ordering for a bench without a sidecar.

    The official LAB-Bench files store ideal + distractors with no letters, so
    the ordering the official harness presents is not recoverable from disk.
    Positions are shuffled deterministically (seed + question id) to keep the
    key out of a fixed slot while staying reproducible."""
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    sidecar = []
    for i, entry in enumerate(bench):
        items = [entry["ideal"]] + list(entry["distractors"])
        order = list(range(len(items)))
        random.Random(f"{seed}:{entry.get('id', i)}").shuffle(order)
        options = {letters[pos]: items[src] for pos, src in enumerate(order)}
        answer = "NULL" if str(entry["ideal"]).strip().upper() == "NULL" \
            else letters[order.index(0)]
        sidecar.append({"id": entry.get("id", str(i)),
                        "options": options, "answer": answer})
    return sidecar


def write_sample(bench_path, sidecar_path, n, seed):
    """Deterministic subset of the bench + sidecar, written as _sample<N> files."""
    with open(bench_path, "r", encoding="utf-8") as f:
        bench = json.load(f)
    with open(sidecar_path, "r", encoding="utf-8") as f:
        sidecar = json.load(f)
    n = min(n, len(bench))
    picked = sorted(random.Random(seed).sample(range(len(bench)), n))
    stem = os.path.splitext(bench_path)[0]
    bench_out, side_out = f"{stem}_sample{n}.json", f"{stem}_sample{n}_eval.json"
    for path, rows in ((bench_out, [bench[i] for i in picked]),
                       (side_out, [sidecar[i] for i in picked])):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
    print(f"Sampled {n}/{len(bench)} question(s) -> {bench_out}")
    return bench_out, side_out


def unsure_letter_for(options):
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    used = sorted(options.keys())
    return letters[len(used)] if used and len(used) < len(letters) else "Z"


REPAIR_PROMPT = (
    "You previously answered a multiple-choice question with:\n\n{previous}\n\n"
    "Restate your answer as a single letter ({letters}) with no other text."
)


def repair_letter(previous_raw, options, unsure_letter, solver):
    """One re-ask for outputs the official parser cannot turn into a letter."""
    letters = ", ".join(sorted(options) + [unsure_letter])
    return llm_gateway.chat(
        REPAIR_PROMPT.format(previous=previous_raw.strip()[:2000], letters=letters),
        model=solver)


def solve(bench_path, sidecar_path, solver):
    """Solve every question with one model; cache per index, resume-safe.

    Returns {idx: {"raw", "letter", "score", "reason", ...}}; entries whose
    strict parse failed also carry "score_strict"/"reason_strict" and the
    repair attempt's raw reply."""
    stem = os.path.splitext(os.path.basename(bench_path))[0]
    cache_path = os.path.join(os.path.dirname(os.path.abspath(bench_path)),
                              f"labbench_solved_{stem}_{solver}.json")
    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)

    with open(bench_path, "r", encoding="utf-8") as f:
        bench = json.load(f)
    with open(sidecar_path, "r", encoding="utf-8") as f:
        sidecar = json.load(f)

    for idx, (entry, ev) in enumerate(zip(bench, sidecar)):
        unsure = unsure_letter_for(ev["options"])
        valid = set(ev["options"]) | {unsure}
        cached = cache.get(str(idx))
        if cached is not None and (cached["letter"] in valid
                                   or cached.get("repair_letter")):
            continue
        if cached is None:
            prompt = build_prompt(entry["question"], ev["options"], unsure)
            raw = llm_gateway.chat(prompt, model=solver)
            letter = extract_answer(raw)
        else:  # cached with an unparsable letter from before repair existed
            raw, letter = cached["raw"], cached["letter"]
        record = {"raw": raw, "letter": letter,
                  "score": grade(letter, ev["answer"], unsure)[0],
                  "reason": grade(letter, ev["answer"], unsure)[1]}
        if letter not in valid:
            record["score_strict"], record["reason_strict"] = \
                record["score"], record["reason"]
            repaired = repair_letter(raw, ev["options"], unsure, solver)
            rep_letter = extract_answer(repaired)
            record["repair_raw"] = repaired
            record["repair_letter"] = rep_letter
            record["score"], record["reason"] = grade(rep_letter, ev["answer"], unsure)
        cache[str(idx)] = record
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        note = f" [repaired -> {record.get('repair_letter') or '?'}]" \
            if "repair_letter" in record else ""
        print(f"[{solver}] {idx + 1}/{len(bench)} -> {letter or '?'} "
              f"({record['reason']}){note}", flush=True)
    return cache


def report(solver_caches):
    """Per-solver official metrics over the common question set."""
    print("\n== Official LAB-Bench metrics (per solver) ==")
    for solver, cache in solver_caches.items():
        n = len(cache)
        strict = [v.get("score_strict", v["score"]) for v in cache.values()]
        eff = [v["score"] for v in cache.values()]
        repaired = sum(1 for v in cache.values() if "repair_letter" in v)
        correct = sum(1 for v in cache.values() if v["reason"] == "Correct")
        unsure = sum(1 for v in cache.values() if v["reason"] == "Unsure")
        print(f"  {solver}: mean={sum(eff)/n:.3f} (strict "
              f"{sum(strict)/n:.3f}) correct={correct}/{n} unsure={unsure}/{n} "
              f"format-repaired={repaired}/{n}")


def write_subset(bench_path, sidecar_path, solver_caches, lo, hi, suffix, label):
    """Keep questions whose MEAN official score across solvers lies in [lo, hi].

    `--drop-all-correct` is the hi=0.999 case (every solver at 1.0). A band such
    as 0.45..0.55 keeps exactly the questions the panel splits on, which is the
    calibration target: the resulting pack sits at the official LAB-Bench
    difficulty instead of the everyone-aces or no-one-answers extremes."""
    with open(bench_path, "r", encoding="utf-8") as f:
        bench = json.load(f)
    with open(sidecar_path, "r", encoding="utf-8") as f:
        sidecar = json.load(f)

    kept, dropped = [], []
    for idx in range(len(bench)):
        scores = [cache[str(idx)]["score"] for cache in solver_caches.values()
                  if str(idx) in cache]
        mean = sum(scores) / len(scores) if scores else 1.0
        (kept if lo <= mean <= hi else dropped).append(idx)

    stem = os.path.splitext(bench_path)[0]
    bench_out = f"{stem}_{suffix}.json"
    side_out = f"{stem}_{suffix}_eval.json"
    for path, rows, wanted in ((bench_out, bench, kept), (side_out, sidecar, kept)):
        with open(path, "w", encoding="utf-8") as f:
            json.dump([rows[i] for i in wanted], f, indent=2, ensure_ascii=False)
    print(f"\n{label}: kept {len(kept)}/{len(bench)}, dropped {len(dropped)} "
          f"(mean official score in [{lo}, {hi}])")
    print(f"Kept question ids (1-based): {[i + 1 for i in kept]}")
    print(f"Subset bench   -> {bench_out}")
    print(f"Subset sidecar -> {side_out}")


def drop_all_correct(bench_path, sidecar_path, solver_caches):
    """Remove questions every solver scored 1.0 on; write _filtered files."""
    write_subset(bench_path, sidecar_path, solver_caches, 0.0, 0.999, "filtered",
                 "All-correct (too easy) filter")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bench_json", type=str,
                        help="Exported LAB-Bench file (from export_labbench.py)")
    parser.add_argument("--solver", type=str, default="",
                        help="Comma-separated solver models; default = MODEL_NAME pool")
    parser.add_argument("--drop-all-correct", action="store_true",
                        help="Drop questions every solver answered correctly")
    parser.add_argument("--keep-band", nargs=2, type=float, default=None,
                        metavar=("LO", "HI"),
                        help="Keep questions whose mean official score across the "
                             "solvers lies in [LO, HI] (e.g. 0.45 0.55 keeps the "
                             "questions the panel splits on — the official-difficulty "
                             "calibration target)")
    parser.add_argument("--band-suffix", default="band",
                        help="Filename suffix for the --keep-band subset")
    parser.add_argument("--synth-sidecar", action="store_true",
                        help="If the sidecar is missing (e.g. scoring the official "
                             "LAB-Bench file), build one by shuffling ideal + "
                             "distractors deterministically")
    parser.add_argument("--sample", type=int, default=0,
                        help="Score a deterministic random subset of N questions "
                             "instead of the whole file")
    parser.add_argument("--seed", default="5c41")
    args = parser.parse_args()

    solvers = [s.strip() for s in args.solver.split(",") if s.strip()] or \
        llm_gateway.model_pool()
    if not solvers:
        raise SystemExit("ERROR: no solvers (--solver or MODEL_NAME in .env).")

    sidecar_path = os.path.splitext(args.bench_json)[0] + "_eval.json"
    if not os.path.exists(sidecar_path):
        if not args.synth_sidecar:
            raise SystemExit(f"ERROR: sidecar {sidecar_path} not found — export with "
                             f"export_labbench.py first (or pass --synth-sidecar).")
        with open(args.bench_json, "r", encoding="utf-8") as f:
            sidecar = synth_sidecar(json.load(f), args.seed)
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(sidecar, f, indent=2, ensure_ascii=False)
        print(f"Synthesized sidecar (shuffled positions) -> {sidecar_path}")

    bench_path = args.bench_json
    if args.sample:
        bench_path, sidecar_path = write_sample(bench_path, sidecar_path,
                                                args.sample, args.seed)

    solver_caches = {s: solve(bench_path, sidecar_path, s) for s in solvers}
    report(solver_caches)
    if args.drop_all_correct:
        drop_all_correct(bench_path, sidecar_path, solver_caches)
    if args.keep_band:
        lo, hi = args.keep_band
        write_subset(bench_path, sidecar_path, solver_caches, lo, hi,
                     args.band_suffix, f"Difficulty band [{lo}, {hi}] filter")


if __name__ == "__main__":
    main()
