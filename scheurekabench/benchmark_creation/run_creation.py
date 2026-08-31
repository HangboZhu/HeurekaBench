"""Unified benchmark-creation entry point.

--mode code    (default) Original pipeline: papers WITH code repositories.
               insights.py -> code_insights.py -> match_insights.py
               -> utils/combine_codes.py -> utils/convert_to_nb.py
--mode debate  Pipeline for papers WITHOUT code repositories.
               insights.py -> debate_insights.py (multi-LLM debate + judge
               + agent review) which writes insights.json directly.

Each underlying script keeps its own CLI and resume behavior; this wrapper
only chains them and aborts on the first failing step. Question generation
(insights_to_questions.py) intentionally stays a separate command, because in
code mode a human curates insights.json in between.
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run_step(description, cmd):
    print(f"\n=== {description} ===")
    print(" ".join(cmd), flush=True)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"ERROR: step '{description}' failed with exit code {result.returncode}. Aborting.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base_dir", type=str, required=True,
                        help="Directory with paper1/, paper2/, ... subdirectories")
    parser.add_argument("--mode", type=str, default="code", choices=["code", "debate"],
                        help="code: papers with code repositories (original pipeline); "
                             "debate: papers without code, verified via multi-LLM debate")
    parser.add_argument("--model_call", type=str, default="gpt",
                        choices=["gpt", "claude", "claude_code", "gateway"])
    # Debate-mode pass-through arguments (ignored in code mode). When omitted,
    # debate_insights.py resolves the roles from .env (MODEL_NAME and friends).
    parser.add_argument("--debaters", type=str, default=None,
                        help="[debate mode] Comma-separated 2-3 gateway model names "
                             "(default: DEBATE_MODELS from .env, else first two MODEL_NAME entries)")
    parser.add_argument("--judge", type=str, default=None,
                        help="[debate mode] Gateway model name for the final ruling "
                             "(default: JUDGE_MODEL from .env, else the last MODEL_NAME entry)")
    parser.add_argument("--reviewer", type=str, default=None,
                        help="[debate mode] A gateway model name, 'claude_code' (local agent), or 'none' "
                             "(default: REVIEW_MODEL from .env, else 'claude_code')")
    parser.add_argument("--rounds", type=int, default=2,
                        help="[debate mode] Statement rounds per insight (round 1 = openings)")
    parser.add_argument("--strict-review", action="store_true",
                        help="[debate mode] Drop insights failing the final agent review")
    parser.add_argument("--force", action="store_true",
                        help="[debate mode] Overwrite an existing hand-curated insights.json")
    args = parser.parse_args()

    py = sys.executable
    common = ["--base_dir", args.base_dir, "--model_call", args.model_call]

    if args.mode == "code":
        run_step("Insight extraction (InsightExtractor)",
                 [py, os.path.join(HERE, "insights.py")] + common)
        run_step("Code description (CodeDescriber)",
                 [py, os.path.join(HERE, "code_insights.py")] + common)
        run_step("Code-insight matching + multi-step code generation (CodeMatcher/CodeGenerator)",
                 [py, os.path.join(HERE, "match_insights.py")] + common)
        run_step("Combining generated code into scripts",
                 [py, os.path.join(HERE, "utils", "combine_codes.py"),
                  "--base_dir", args.base_dir, "--output_root", args.base_dir,
                  "--model_call", args.model_call])
        run_step("Converting scripts to notebooks",
                 [py, os.path.join(HERE, "utils", "convert_to_nb.py")] + common)
        print("\nCode mode finished. Manually verify the generated code, curate insights.json, "
              "then run insights_to_questions.py (Step 2 in README).")
    else:
        run_step("Insight extraction (InsightExtractor)",
                 [py, os.path.join(HERE, "insights.py")] + common)
        debate_cmd = [py, os.path.join(HERE, "debate_insights.py")] + common + [
            "--rounds", str(args.rounds),
        ]
        if args.debaters:
            debate_cmd += ["--debaters", args.debaters]
        if args.judge:
            debate_cmd += ["--judge", args.judge]
        if args.reviewer:
            debate_cmd += ["--reviewer", args.reviewer]
        if args.strict_review:
            debate_cmd.append("--strict-review")
        if args.force:
            debate_cmd.append("--force")
        run_step("Debate-based insight verification (debaters -> judge -> agent review)", debate_cmd)
        print("\nDebate mode finished. insights.json is ready; "
              "run insights_to_questions.py (Step 2 in README).")


if __name__ == "__main__":
    main()
