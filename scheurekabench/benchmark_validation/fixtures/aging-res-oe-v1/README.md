# Fixture: Aging-Res OE v1 (audited sample, 2026-09-13)

Regression fixture for the OE question-pack pipeline. Frozen from the Aging-Res run
(`molintbench/Aging-Res/`) at the state that went through the 2026-09-13 field audit,
so that later edits (stem compression, near-duplicate differentiation, prompt changes)
can be re-evaluated against a fixed reference.

## Contents

| File | What it is |
|---|---|
| `oe_questions_eval.json` | The audited pack: 20 OE items (pre-fix wording), bare `question` / `answer` / `rubric` |
| `oe_questions_solved_round3.json` | Judge calibration: both solvers' blind answers plus the gpt-5.6-sol per-fact grades and 1-5 ratings |
| `baseline.json` | Baseline scores + the audit findings this fixture encodes |

## Baseline

| Solver | n | Mean rating (1-5) | Distribution |
|---|---|---|---|
| MiniMax-M3 | 20 | **3.25** | 1:5, 2:1, 3:4, 4:4, 5:6 |
| deepseek-v4-flash | 17 | **3.82** | 1:1, 3:4, 4:8, 5:4 |

Overall 3.54 (target band 2.5-3.5). Grader: `gpt-5.6-sol`.

Known audit findings frozen into the fixture: 5 stems over the ~900-char guidance
(#1 975, #2 960, #6 923, #10 1063, #13 934) and two near-duplicate pairs
(#1/#2 similarity 0.549, #17/#18 0.480). All 6 field-level compliance checks passed.

## How to run a regression check

1. **Field check** on the pack under test:

   ```bash
   python benchmark_creation/validate_question_pack.py <dir>/oe_questions_eval.json --qtype oe
   ```

   Expect `PASS`. Any HARD line is a regression; the fixture's own findings appear as WARN.

2. **Difficulty check** (should move only in the intended direction — compression and
   differentiation must not change what the question asks):

   ```bash
   python benchmark_creation/solve_and_grade.py --questions_json <dir>/oe_questions.json \
       --qtype oe --solver MiniMax-M3 --grader gpt-5.6-sol --out <dir>/oe_solved_regress_minimax.json
   python benchmark_creation/solve_and_grade.py --questions_json <dir>/oe_questions.json \
       --qtype oe --solver deepseek-v4-flash --grader gpt-5.6-sol --out <dir>/oe_solved_regress_deepseek.json
   ```

   Compare per-solver means against `baseline.json`. Editing that only rewrites the stem
   should keep means within roughly ±0.5 of baseline; a larger drop means the rewrite
   changed the reasoning target, not just its phrasing.

3. **Item-level diff** (optional, stricter): compare per-question ratings in
   `oe_questions_solved_round3.json` with the new solve output; questions whose
   rating moved by ≥2 should be reviewed by hand to confirm the target is unchanged.

## Immutability

Treat this directory as read-only. If a future pack becomes the new reference, add a
sibling `aging-res-oe-v2/` rather than editing v1 — the baseline numbers must stay
comparable across regression runs.
