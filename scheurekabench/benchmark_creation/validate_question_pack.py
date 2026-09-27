"""Validate an exported question pack against the field-level compliance baseline.

Encodes the checks that the Aging-Res OE audit (2026-09-13) applied by hand, so
every future pack can be verified the same way:

  HARD (exit 1)
    * item keys: oe -> question/answer/rubric ; mcq -> + options ;
      protocolqa -> question/ideal/distractors/rubric
    * rubric.facts: 3-5 non-empty facts (oe)
    * rubric.scoring_guide (oe) / correct_reasoning + distractor_analysis (mcq)
      / ideal_justification + distractor_analysis (protocolqa)
    * answer non-empty; no duplicate question stems
    * protocolqa: exactly 3-6 distractors, none empty, duplicated or equal to
      the ideal; the remedy is <= 220 chars and the question ends with "?"
  WARN (exit 0, listed)
    * stem longer than the ~900-char guidance (479 for protocolqa — the
      official subset's own maximum)
    * source leakage ("the article/according to/Figure X/..." — must never
      point at the paper the pack was derived from)
    * pairwise stem similarity above --similarity-warn (near-duplicate pairs)
    * protocolqa: a remedy that names no step (the official set does this in
      93/108 items, so it is a style warning, not a defect)

Usage:
  python benchmark_creation/validate_question_pack.py <dir>/oe_questions_eval.json --qtype oe
  python benchmark_creation/validate_question_pack.py <bench>/protocolqa_bench.json \\
      --qtype protocolqa --bench <bench>/protocolqa_bench.json

The `--bench` flag adds the one check the eval file cannot make: every step
number a remedy cites must exist in that entry's protocol. A remedy that names
step 44 of a block that ends at step 38 refers to something the respondent
cannot see, which is a hallucination rather than a hard question.
"""

import argparse
import difflib
import itertools
import json
import os
import re
import sys

# The step-citation rule lives with the exporter, which already enforces it as a
# skip when building a pack; sharing the implementation keeps the validator from
# drifting away from what the exporter does.
from export_labbench import STEP_CITED_RE, defined_steps

LEAK_RE = re.compile(
    r"\b(the article|the paper|the review|the study|according to|as described|as reported|"
    r"figure\s*\d|table\s*\d|supplementary|the authors)\b",
    re.IGNORECASE,
)
STEM_LIMIT = 900
# Official ProtocolQA maxima (measured over the 108-item release).
PROTOCOLQA_QUESTION_LIMIT = 479
PROTOCOLQA_IDEAL_LIMIT = 220
PROTOCOLQA_DISTRACTORS = (3, 6)

# Heuristic detectors for inferences the evidence cannot license. Each entry is
# (compiled pattern, message). Derived from the aging-res-0919 external review,
# which rejected 9/10 items for exactly these error classes; see
# prompts/insight2question_rubric_prompts.py "Evidential validity".
_PCT = r"[+\u2212-]?\d+(?:\.\d+)?\s*%"
# Wording that explicitly denies comparability is the *correct* treatment, not
# the error, so the F-vs-R² check is suppressed when it appears nearby.
_NONCOMPARABLE_RE = re.compile(
    r"(?:cannot be (?:directly )?compared|not (?:directly )?comparable|not interchangeable|"
    r"different statistic types|not the same (?:kind|type) of (?:statistic|quantity))",
    re.IGNORECASE,
)
EVIDENTIAL_CHECKS = [
    # Arithmetic combining percentages from different experiments/baselines.
    # The operator must sit against the first percentage: a bare minus inside a
    # later number ("… −28.3% and … −35.2%") is two observations, not a sum.
    (re.compile(rf"{_PCT}\s*(?:[-\u2212]\s*|\bminus\b|\bsubtract\w*\b|\bless\b|\bleaving\b|\bdifference\b)"
                rf"[^.;]{{0,30}}?{_PCT}", re.IGNORECASE),
     "combines two percentages arithmetically — only valid if they share a baseline"),
    (re.compile(r"\b(?:pp|percentage points?)\b", re.IGNORECASE),
     "states a difference of percent-changes as percentage points"),
    (re.compile(r"\bresiduals?\s+of\s+(?:roughly\s+|about\s+|approximately\s+)?[+\u2212-]?\d",
                re.IGNORECASE),
     "derives a numeric residual by combining effects from different contexts"),
    # P-value used to order a pathway.
    (re.compile(r"\b(?:smaller|larger|lower|higher|more significant|less significant)\s+P[-\s]?value",
                re.IGNORECASE),
     "ranks by P-value magnitude — P-values do not order pathway position"),
    (re.compile(r"P[-\s]?value\s+(?:reversal|asymmetry|inversion|ordering)", re.IGNORECASE),
     "reads a P-value pattern as evidence about cascade architecture"),
    # Comparing different statistic types.
    (re.compile(r"\bR\s*²?\s*(?:=|of\s*)?\s*[0-9.]+\s*[^.;]{0,40}?(?:matches?|comparable to|versus|exceeds?)"
                r"[^.;]{0,40}?\bF\s*=", re.IGNORECASE),
     "compares an R² with an F statistic — different quantities"),
    # Confounded manipulation: a cassette/vector/vehicle is named without the
    # control arm that would isolate it.
    (re.compile(r"\b(?:cassette|transgene|plasmid|vector|vehicle|T2A)\b", re.IGNORECASE),
     "names a cassette/vector/vehicle manipulation — check the confounder is controlled, "
     "not credited as the cause"),
    # Over-claiming necessity/uniqueness from sufficiency evidence.
    (re.compile(r"\b(?:obligatory|indispensable|the only route|sole(?:ly)? (?:gate|route)|"
                r"most direct gate|unique gate|necessary and sufficient|definitively (?:proves?|establishes?))\b",
                re.IGNORECASE),
     "asserts necessity/uniqueness — a sufficiency experiment cannot establish this"),
    # Universal exclusion from a negative assay. "at all" is excluded: it
    # negates a claim ("provide no construct at all"), it does not over-claim.
    (re.compile(r"\brules? out\b(?:(?!\bat all\b)[^.;]){0,60}?\b(?:any|all|every)\b", re.IGNORECASE),
     "treats a negative result as excluding all alternatives"),
    # Per-cell rate inferred from a compositional fraction.
    (re.compile(r"\bper[-\s]cell\b[^.;]{0,50}?\b(?:rate|production|output)\b", re.IGNORECASE),
     "claims a per-cell production rate — expressing-cell fractions are compositional"),
]


def _sentence_of(text, pos):
    """The [.!?]-delimited sentence containing `pos` (semicolons stay inside:
    the repairs phrase a limitation and its claims in one colon/semicolon run)."""
    start = max(text.rfind(ch, 0, pos) for ch in ".!?\n") + 1
    ends = [text.find(ch, pos) for ch in ".!?"]
    ends = [e for e in ends if e != -1]
    end = min(ends) + 1 if ends else len(text)
    return text[start:end]


# A claim is *correctly* stated when what surrounds it denies it ("does NOT
# license … obligatory", "not per-cell production", "neither control rules
# out …"). These cues are deliberately tight: a loose one such as "without" or
# a bare "no" would suppress the real defects the checks exist to catch
# (e.g. "yet no APOE was detected … rules out direct binding … any").
_NEGATED_CLAIM_RE = re.compile(
    r"\b(?:do(?:es)?|did|can|could|would)\s*not\s+(?:license|establish|support|determine|"
    r"justify|show|prove|exclude|rule)"
    r"|\bnot\s+licens\w+|\bcannot\s+be\b|\bnot\s+established\b|\bis\s+undefined\b|\bneither\b",
    re.IGNORECASE,
)


def evidential_warnings(items):
    """Flag stems/answers that assert an unsupported inference type."""
    out = []
    for i, item in enumerate(items, 1):
        for field in ("question", "answer"):
            text = str(item.get(field, ""))
            for pattern, message in EVIDENTIAL_CHECKS:
                m = pattern.search(text)
                if not m:
                    continue
                sentence = _sentence_of(text, m.start())
                if _NEGATED_CLAIM_RE.search(sentence):
                    continue  # the text is denying the inference, not making it
                if re.search(r"\bnot\s+$", text[max(0, m.start() - 8):m.start()]):
                    continue  # "…, not per-cell protein output": the phrase itself is negated
                out.append(f'#{i} [{field}]: {message} — "{m.group(0)[:70]}"')
            # An F statistic and an R² in the same item compared in words is the
            # error even when they are not adjacent (a single regex misses it) —
            # unless the text explicitly says the two are not comparable, which
            # is the correct treatment.
            if (not _NONCOMPARABLE_RE.search(text)
                    and re.search(r"\bF\s*[>=]", text) and re.search(r"\bR\s*²", text)
                    and re.search(r"\b(?:matches?|comparable|versus|larger|smaller|magnitude|"
                                  r"equivalent|similar)\b", text, re.IGNORECASE)):
                out.append(f"#{i} [{field}]: mentions both an F statistic and an R² together with a "
                           f"magnitude comparison — they are not comparable quantities")
    return out


def validate(items, qtype, similarity_warn):
    hard, warn = [], []

    expected_keys = {
        "mcq": ["answer", "options", "question", "rubric"],
        "oe": ["answer", "question", "rubric"],
        "protocolqa": ["distractors", "ideal", "question", "rubric"],
    }[qtype]
    stem_limit = PROTOCOLQA_QUESTION_LIMIT if qtype == "protocolqa" else STEM_LIMIT

    for i, item in enumerate(items, 1):
        keys = sorted(item.keys())
        if keys != expected_keys:
            hard.append(f"#{i}: fields {keys} != {expected_keys}")
        if qtype != "protocolqa" and not str(item.get("answer", "")).strip():
            hard.append(f"#{i}: empty answer")
        rubric = item.get("rubric") or {}
        if not isinstance(rubric, dict):
            hard.append(f"#{i}: rubric is not an object")
            rubric = {}
        if qtype == "protocolqa":
            hard += _protocolqa_hard(i, item, rubric)
        elif qtype == "oe":
            facts = rubric.get("facts")
            if not isinstance(facts, list) or not (3 <= len(facts) <= 5):
                hard.append(f"#{i}: rubric.facts must hold 3-5 entries, got {len(facts) if isinstance(facts, list) else facts!r}")
            elif any(not str(f).strip() for f in facts):
                hard.append(f"#{i}: empty rubric fact")
            if not str(rubric.get("scoring_guide", "")).strip():
                hard.append(f"#{i}: missing rubric.scoring_guide")
        else:
            if not str(rubric.get("correct_reasoning", "")).strip():
                hard.append(f"#{i}: missing rubric.correct_reasoning")
            analysis = rubric.get("distractor_analysis") or {}
            options = item.get("options") or {}
            # The rubric is defined over the DISTRACTORS, so the analysis must
            # cover exactly the options the keyed answer does not select
            # (answer "A,C" -> analysis for B and D).
            keyed = {a.strip().upper() for a in str(item.get("answer", "")).split(",") if a.strip()}
            distractors = {k for k in options if k.strip().upper() not in keyed}
            if {k.strip().upper() for k in analysis} != {k.strip().upper() for k in distractors}:
                hard.append(f"#{i}: distractor_analysis {sorted(analysis)} does not cover exactly "
                            f"the non-keyed options {sorted(distractors)}")
        q = str(item.get("question", ""))
        if len(q) > stem_limit:
            warn.append(f"#{i}: stem {len(q)} chars (> {stem_limit})")
        m = LEAK_RE.search(q)
        if m:
            warn.append(f'#{i}: possible source leak "{m.group(0)}"')
        if qtype == "protocolqa" and not STEP_CITED_RE.search(str(item.get("ideal", ""))):
            warn.append(f"#{i}: the remedy names no step (official items do in 93/108)")

    seen = {}
    for i, item in enumerate(items, 1):
        norm = " ".join(str(item.get("question", "")).lower().split())
        if norm in seen:
            hard.append(f"#{i}: duplicate of #{seen[norm]}")
        seen[norm] = i

    pairs = []
    for (i, a), (j, b) in itertools.combinations(enumerate(items), 2):
        ratio = difflib.SequenceMatcher(None, str(a.get("question", "")), str(b.get("question", ""))).ratio()
        if ratio >= similarity_warn:
            pairs.append((ratio, i + 1, j + 1))
    for ratio, i, j in sorted(pairs, reverse=True):
        warn.append(f"#{i}/#{j}: stem similarity {ratio:.3f} (>= {similarity_warn})")

    warn += evidential_warnings(items)

    if qtype == "mcq":
        warn += option_length_warnings(items)

    return hard, warn


def _protocolqa_hard(index, item, rubric):
    """Field rules for a ProtocolQA entry (official envelope: ideal + 3-6
    free-text distractors, no letter key)."""
    out = []
    ideal = str(item.get("ideal") or "").strip()
    distractors = item.get("distractors")
    if not ideal:
        out.append(f"#{index}: empty ideal")
    elif len(ideal) > PROTOCOLQA_IDEAL_LIMIT:
        out.append(f"#{index}: ideal is {len(ideal)} chars "
                   f"(> {PROTOCOLQA_IDEAL_LIMIT}, the official maximum)")
    if not isinstance(distractors, list):
        out.append(f"#{index}: distractors is not a list")
        distractors = []
    clean = [str(d).strip() for d in distractors]
    lo, hi = PROTOCOLQA_DISTRACTORS
    if not lo <= len(clean) <= hi:
        out.append(f"#{index}: {len(clean)} distractor(s); the official set holds {lo}-{hi}")
    if any(not d for d in clean):
        out.append(f"#{index}: empty distractor")
    if len(set(clean)) != len(clean):
        out.append(f"#{index}: duplicate distractors")
    if ideal and any(d == ideal for d in clean):
        out.append(f"#{index}: the ideal is repeated among the distractors")
    if not str(item.get("question", "")).rstrip().endswith("?"):
        out.append(f"#{index}: question does not end with '?'")
    if not str(rubric.get("ideal_justification", "")).strip():
        out.append(f"#{index}: missing rubric.ideal_justification")
    if not (rubric.get("distractor_analysis") or {}):
        out.append(f"#{index}: missing rubric.distractor_analysis")
    return out


def step_reference_checks(bench):
    """HARD checks that need the protocol text, so they read the bench file:

    every step number a remedy or distractor cites must be defined in that
    entry's own protocol block. A citation of step 44 inside a block that ends
    at step 38 points at something the respondent cannot see — that is a broken
    item, not a hard one. (Officially the numbering is global to each protocol,
    and our blocks keep the paper's numbering, so a block covering 16-38
    defines exactly 16-38.)"""
    out = []
    for i, entry in enumerate(bench, 1):
        defined = defined_steps(entry.get("protocol") or "")
        if not defined:
            continue
        span = f"it holds steps {min(defined)}-{max(defined)}"
        for field in ("ideal", "distractors"):
            values = [entry.get(field)] if field == "ideal" else (entry.get(field) or [])
            for value in values:
                for cited in STEP_CITED_RE.findall(str(value)):
                    if int(cited) not in defined:
                        out.append(f"#{i} [{field}]: cites step {cited}, which this "
                                   f"protocol block does not define ({span}) — "
                                   f'"{str(value)[:70]}"')
    return out


def option_length_warnings(items):
    """Flag packs the keyed answer can be picked from by length alone.

    Observed on both Aging-Res packs (2026-09-17): the keyed option was the
    longest in 19/20 and 9/10 questions, averaging ~1.6x the distractors —
    a shortcut that plausibly contributes to the saturating MCQ scores."""
    if not items:
        return []
    longest_hits, keyed_lens, distractor_lens, flagged = 0, [], [], []
    for i, item in enumerate(items, 1):
        options = item.get("options") or {}
        if not options:
            continue
        keyed = {a.strip().upper() for a in str(item.get("answer", "")).split(",") if a.strip()}
        keyed_opts = [len(v) for k, v in options.items() if k.strip().upper() in keyed]
        dist_opts = [len(v) for k, v in options.items() if k.strip().upper() not in keyed]
        if not keyed_opts or not dist_opts:
            continue
        keyed_lens.append(sum(keyed_opts) / len(keyed_opts))
        distractor_lens.append(sum(dist_opts) / len(dist_opts))
        if min(keyed_opts) >= max(dist_opts):
            longest_hits += 1
            flagged.append(i)

    out = []
    n = len(keyed_lens)
    mean_keyed = sum(keyed_lens) / n
    mean_dist = sum(distractor_lens) / n
    if mean_keyed > 1.25 * mean_dist:
        out.append(f"keyed options average {mean_keyed:.0f} chars vs {mean_dist:.0f} for distractors "
                   f"(> 1.25x): the answer is guessable by length")
    if longest_hits > n / 2:
        out.append(f"keyed option is the longest in {longest_hits}/{n} questions "
                   f"(#{', #'.join(map(str, flagged))}): answer is guessable by length")

    # Positional skew: a constant guess must not score well. Chance is 25% per
    # slot, so > 40% in one slot is a shortcut (observed: 8/10 keyed at B).
    positions = {}
    for item in items:
        for letter in str(item.get("answer", "")).upper().replace(" ", "").split(","):
            if letter:
                positions[letter] = positions.get(letter, 0) + 1
    counted = sum(positions.values())
    if counted:
        top_letter, top_count = max(positions.items(), key=lambda kv: kv[1])
        if top_count > 0.4 * counted:
            out.append(f"keyed answers concentrate on {top_letter}: {top_count}/{counted} "
                       f"({top_count / counted:.0%}, chance 25%): answer is guessable by position")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack", help="path to <qtype>_questions_eval.json")
    ap.add_argument("--qtype", choices=["oe", "mcq", "protocolqa"], required=True)
    ap.add_argument("--bench", default=None,
                    help="For protocolqa: the exported bench file with the same "
                         "order, enabling the step-reference checks that need the "
                         "protocol text")
    ap.add_argument("--similarity-warn", type=float, default=0.40,
                    help="warn when two stems score at or above this similarity (default 0.40)")
    args = ap.parse_args()

    items = json.load(open(args.pack, encoding="utf-8"))
    if not isinstance(items, list):
        sys.exit(f"ERROR: {args.pack} must hold a bare JSON list.")
    hard, warn = validate(items, args.qtype, args.similarity_warn)

    if args.bench:
        bench = json.load(open(args.bench, encoding="utf-8"))
        if not isinstance(bench, list):
            sys.exit(f"ERROR: {args.bench} must hold a bare JSON list.")
        if len(bench) != len(items):
            sys.exit(f"ERROR: {args.bench} holds {len(bench)} entries but {args.pack} "
                     f"holds {len(items)} — they must be index-aligned.")
        hard += step_reference_checks(bench)
        print(f"step-reference check against {len(bench)} protocol(s) in "
              f"{os.path.basename(args.bench)}")

    lens = sorted(len(str(it.get("question", ""))) for it in items)
    print(f"pack: {args.pack} | {len(items)} {args.qtype} items | stem chars p50={lens[len(lens)//2]} max={lens[-1]}")
    for msg in hard:
        print(f"HARD  {msg}")
    for msg in warn:
        print(f"WARN  {msg}")
    if not hard and not warn:
        print("PASS  all field-level checks and warnings clear")
    elif not hard:
        print(f"PASS (with {len(warn)} warning(s))")
    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
