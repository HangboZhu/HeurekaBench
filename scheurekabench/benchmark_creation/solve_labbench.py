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

--keep-hard is the opposite edge: it keeps only the questions NO solver
answered correctly, writing <bench>_hard.json. With a three-model panel, only
the questions the first two models both missed need the third model's verdict,
so score it on just those (a small bench sliced from the pool) and pass
--keep-hard --cascade to judge the ordered panel accordingly. --judge-only
re-applies a filter from the caches already on disk without calling any model.

  python solve_labbench.py data/labbench/litqa2_pool.json \
      --solver MiniMax-M3,deepseek-v4-flash --drop-all-correct
  python solve_labbench.py data/labbench/litqa2_pool.json \
      --solver MiniMax-M3,deepseek-v4-flash,qwen3.7-max --keep-hard --cascade \
      --judge-only
"""

import os
import re
import json
import time
import difflib
import random
import argparse
from utils import llm_gateway
from export_labbench import dump_official

UNSURE_TEXT = "unsure"


def build_prompt(question, options, unsure_letter, protocol=None):
    """Official promptfoo prompt shape, with the unsure option appended.

    A ProtocolQA question is asked about the protocol it ships with, and the
    official harness feeds it as one string — `input.question = self.protocol +
    input.question` (labbench/ProtocolQA/task.py). Reproducing that concatenation
    is what makes the ProtocolQA score comparable to the LitQA2 one: without it
    the question would be asked about a protocol the model never sees."""
    lines = [f"{letter}) {text}" for letter, text in options.items()]
    lines.append(f"{unsure_letter}) {UNSURE_TEXT}")
    prompt = f"{protocol}\n\n{question}" if protocol else question
    return f"Q: {prompt}\n\nOptions:\n" + "\n".join(lines) + "\n\nAnswer:"


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
    """One re-ask for outputs the official parser cannot turn into a letter.

    Goes through `ask`, so a gateway failure here cannot be mistaken for the
    model refusing to name a letter."""
    letters = ", ".join(sorted(options) + [unsure_letter])
    return ask(REPAIR_PROMPT.format(previous=previous_raw.strip()[:2000],
                                    letters=letters), solver)


def ask(prompt, solver, rounds=2):
    """Chat with one extra round of retries; "" means every route failed.

    llm_gateway.chat already retries 3x per key and returns "" when the
    gateway is down. An empty reply is an infrastructure failure, not a wrong
    answer, so it must never be scored as "the model got this one wrong" —
    that would manufacture fake hard questions in the difficulty filter."""
    for attempt in range(1, rounds + 1):
        raw = llm_gateway.chat(prompt, model=solver)
        if raw.strip():
            return raw
        print(f"[{solver}] empty reply (round {attempt}/{rounds}) — gateway failure?",
              flush=True)
        if attempt < rounds:
            time.sleep(10)
    return ""


def solve(bench_path, sidecar_path, solver):
    """Solve every question with one model; cache per index, resume-safe.

    Returns {idx: {"raw", "letter", "score", "reason", ...}}; entries whose
    strict parse failed also carry "score_strict"/"reason_strict" and the
    repair attempt's raw reply.

    Questions whose call failed at the gateway are deliberately left OUT of
    the cache (and so out of the result) instead of being recorded as 0.0
    Incorrect: they stay pending and a rerun picks them up."""
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

    failures = 0
    for idx, (entry, ev) in enumerate(zip(bench, sidecar)):
        unsure = unsure_letter_for(ev["options"])
        valid = set(ev["options"]) | {unsure}
        cached = cache.get(str(idx))
        # A usable record needs a non-empty ORIGINAL reply. A record with an
        # empty raw but a repair letter is garbage from an older run: the
        # question was never actually put to the model (the call failed), so
        # the "restate your previous answer" re-ask answered nothing real.
        if cached is not None and (cached.get("raw") or "").strip() and (
                cached["letter"] in valid or cached.get("repair_letter")):
            continue
        prompt = build_prompt(entry["question"], ev["options"], unsure,
                              protocol=entry.get("protocol"))
        # A cached reply is re-used as-is (it may pre-date the repair step, or
        # its repair may have failed at the gateway — the text is still the
        # model's answer, only its format was unusable).
        raw = (cached or {}).get("raw") or ""
        if not raw.strip():
            raw = ask(prompt, solver)
        if not raw.strip():
            failures += 1
            print(f"[{solver}] {idx + 1}/{len(bench)} -> GATEWAY FAILED "
                  f"(left pending, rerun to retry)", flush=True)
            continue
        letter = extract_answer(raw)
        record = {"raw": raw, "letter": letter,
                  "score": grade(letter, ev["answer"], unsure)[0],
                  "reason": grade(letter, ev["answer"], unsure)[1]}
        if letter not in valid:
            record["score_strict"], record["reason_strict"] = \
                record["score"], record["reason"]
            repaired = repair_letter(raw, ev["options"], unsure, solver)
            if not repaired.strip():
                failures += 1
                print(f"[{solver}] {idx + 1}/{len(bench)} -> GATEWAY FAILED on "
                      f"the format-repair re-ask (left pending, rerun to retry)",
                      flush=True)
                continue
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
    if failures:
        print(f"[{solver}] {failures} question(s) left pending after gateway "
              f"failures — rerun this command to retry them.", flush=True)
    return cache


def report(solver_caches):
    """Per-solver official metrics over the common question set."""
    print("\n== Official LAB-Bench metrics (per solver) ==")
    for solver, cache in solver_caches.items():
        n = len(cache)
        if not n:
            # Every call failed at the gateway: an empty cache is a fact about
            # the infrastructure, not a 0-score result.
            print(f"  {solver}: no answers (all calls left pending at the gateway)")
            continue
        strict = [v.get("score_strict", v["score"]) for v in cache.values()]
        eff = [v["score"] for v in cache.values()]
        repaired = sum(1 for v in cache.values() if "repair_letter" in v)
        correct = sum(1 for v in cache.values() if v["reason"] == "Correct")
        unsure = sum(1 for v in cache.values() if v["reason"] == "Unsure")
        print(f"  {solver}: mean={sum(eff)/n:.3f} (strict "
              f"{sum(strict)/n:.3f}) correct={correct}/{n} unsure={unsure}/{n} "
              f"format-repaired={repaired}/{n}")


def dedupe_similar(bench, kept, threshold):
    """Drop near-duplicate stems from a kept index list, keeping the first.

    A candidate pool generated in rounds holds paraphrases of the same symptom
    within a block ("you notice low yield" restated three ways); after the
    difficulty filter those paraphrases can survive together, and a pack that
    asks the same question twice is worth less than one that asks it once."""
    if threshold <= 0:
        return kept, []
    kept_out, dropped = [], []
    for idx in kept:
        stem = str(bench[idx].get("question", ""))
        if any(difflib.SequenceMatcher(None, stem, str(bench[j].get("question", ""))
                                       ).ratio() >= threshold for j in kept_out):
            dropped.append(idx)
            continue
        kept_out.append(idx)
    return kept_out, dropped


def write_subset(bench_path, sidecar_path, solver_caches, keep_fn, suffix, label,
                 cascade=False, dedupe_similarity=0.0):
    """Write the questions whose per-solver official scores satisfy `keep_fn`.

    `keep_fn(scores)` gets the list of official scores of the solvers that have
    an answer for the question and decides whether it stays. Two rules matter:

    * "not aced by every solver" (`min(scores) < 1.0`) — the "easy question"
      filter: a question every model answers is useless;
    * "no solver answered it correctly" (`max(scores) < 1.0`) — the strict
      hard-core set, which is what "keep only the hard questions" means.

    `cascade=True` reads solver_caches as an ORDERED panel in which later
    models were only asked about the questions the earlier ones missed (the
    cheap way to run `--keep-hard` with three or more models: a question any
    earlier model aced is already out, so nobody after it needs to see it).
    Judging then walks the panel in order and stops at the first solver that
    aced the question; a question is UNJUDGED only when a solver that *should*
    have answered it (all earlier ones missed it) has no entry.

    With cascade=False a question missing from ANY solver's cache (gateway
    failure, see solve()) is not judged: it is dropped and reported, because an
    absent answer is not evidence that a question is hard."""
    with open(bench_path, "r", encoding="utf-8") as f:
        bench = json.load(f)
    with open(sidecar_path, "r", encoding="utf-8") as f:
        sidecar = json.load(f)

    kept, dropped, unjudged = [], [], []
    for idx in range(len(bench)):
        if cascade:
            aced, gap = False, False
            for cache in solver_caches.values():
                record = cache.get(str(idx))
                if record is None:
                    gap = not aced          # later members are not asked about
                    break                   # questions an earlier member aced
                if record["score"] >= 1.0:
                    aced = True
                    break
            if gap:
                unjudged.append(idx)
            elif aced:
                dropped.append(idx)
            else:
                kept.append(idx)
            continue
        scores = [cache[str(idx)]["score"] for cache in solver_caches.values()
                  if str(idx) in cache]
        if len(scores) < len(solver_caches):
            unjudged.append(idx)
            continue
        (kept if keep_fn(scores) else dropped).append(idx)

    stem = os.path.splitext(bench_path)[0]
    bench_out = f"{stem}_{suffix}.json"
    side_out = f"{stem}_{suffix}_eval.json"
    kept, dupes = dedupe_similar(bench, kept, dedupe_similarity)
    for path, rows, wanted in ((bench_out, bench, kept), (side_out, sidecar, kept)):
        dump_official(path, [rows[i] for i in wanted])
    print(f"\n{label}: kept {len(kept)}/{len(bench)}, dropped {len(dropped)}")
    if dupes:
        print(f"Near-duplicate stems removed (similarity >= {dedupe_similarity}): "
              f"{len(dupes)} {[i + 1 for i in dupes]}")
    if unjudged:
        why = ("cascade gaps: a solver that should have judged them has no answer"
               if cascade else
               "not every solver has an answer — gateway failures")
        print(f"UNJUDGED ({why}; rerun to score them, they are NOT part of the "
              f"subset): {len(unjudged)} {[i + 1 for i in unjudged]}")
    print(f"Kept question ids (1-based): {[i + 1 for i in kept]}")
    print(f"Subset bench   -> {bench_out}")
    print(f"Subset sidecar -> {side_out}")


def drop_all_correct(bench_path, sidecar_path, solver_caches, cascade=False,
                     dedupe_similarity=0.0):
    """Remove questions every solver scored 1.0 on; write _filtered files."""
    write_subset(bench_path, sidecar_path, solver_caches,
                 lambda scores: min(scores) < 1.0, "filtered",
                 "All-correct (too easy) filter — keeps anything a solver missed",
                 cascade, dedupe_similarity)


def keep_hard(bench_path, sidecar_path, solver_caches, cascade=False,
              dedupe_similarity=0.0):
    """Keep only questions NO solver answered correctly; write _hard files."""
    write_subset(bench_path, sidecar_path, solver_caches,
                 lambda scores: max(scores) < 1.0, "hard",
                 "Hard-core filter — keeps only questions every solver missed",
                 cascade, dedupe_similarity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bench_json", type=str,
                        help="Exported LAB-Bench file (from export_labbench.py)")
    parser.add_argument("--solver", type=str, default="",
                        help="Comma-separated solver models; default = MODEL_NAME pool")
    parser.add_argument("--drop-all-correct", action="store_true",
                        help="Drop questions every solver answered correctly")
    parser.add_argument("--keep-hard", action="store_true",
                        help="Keep ONLY questions no solver answered correctly "
                             "(every solver scored < 1.0) -> <bench>_hard.json")
    parser.add_argument("--cascade", action="store_true",
                        help="With --keep-hard: read --solver as an ORDERED "
                             "panel whose later members were only asked about "
                             "the questions the earlier ones missed (see "
                             "write_subset); without it every solver must have "
                             "answered every kept question")
    parser.add_argument("--judge-only", action="store_true",
                        help="Filter from the existing caches without calling "
                             "any model (no new answers are requested)")
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
    parser.add_argument("--dedupe-similarity", type=float, default=0.0,
                        help="Drop a kept question whose stem is this similar "
                             "(difflib ratio) to one already kept — paraphrases of "
                             "the same symptom otherwise survive the filter together "
                             "(0 = keep all)")
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

    solver_caches = {}
    for solver in solvers:
        if args.judge_only:
            stem = os.path.splitext(os.path.basename(bench_path))[0]
            cache_path = os.path.join(os.path.dirname(os.path.abspath(bench_path)),
                                      f"labbench_solved_{stem}_{solver}.json")
            if not os.path.exists(cache_path):
                raise SystemExit(f"ERROR: --judge-only but no cache for {solver} "
                                 f"({cache_path}); run without it to score first.")
            with open(cache_path, "r", encoding="utf-8") as f:
                solver_caches[solver] = json.load(f)
            print(f"{solver}: judging from cache {cache_path} "
                  f"({len(solver_caches[solver])} answers)")
        else:
            solver_caches[solver] = solve(bench_path, sidecar_path, solver)
    report(solver_caches)
    if args.cascade and not args.keep_hard:
        raise SystemExit("ERROR: --cascade only applies to --keep-hard (the "
                         "'everyone scored 1.0' rule cannot be cascaded).")
    if args.drop_all_correct:
        drop_all_correct(bench_path, sidecar_path, solver_caches, args.cascade,
                         args.dedupe_similarity)
    if args.keep_hard:
        keep_hard(bench_path, sidecar_path, solver_caches, args.cascade,
                  args.dedupe_similarity)
    if args.keep_band:
        lo, hi = args.keep_band
        write_subset(bench_path, sidecar_path, solver_caches,
                     lambda scores: lo <= sum(scores) / len(scores) <= hi,
                     args.band_suffix, f"Difficulty band [{lo}, {hi}] filter",
                     dedupe_similarity=args.dedupe_similarity)


if __name__ == "__main__":
    main()
