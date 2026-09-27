"""Re-run one bench's solver cache to fill the gaps a flaky gateway left.

Why this exists: solve_labbench.py treats an empty gateway reply as *unjudged*
— it is never cached as a wrong answer — so a degraded window leaves holes that
only a later run can fill. A straight re-run is expensive for two reasons this
driver removes:

* a fresh process pays one full round of read timeouts on the anthropic route
  for models that route is known to fail for (`deepseek-v4-flash` always times
  out there; the note lives in `utils/llm_gateway.py`), so the route is
  pre-marked dead;
* solve() resumes from the cache file, so repeated invocations only attempt the
  questions that are still missing.

Run it with paths relative to your current directory (the bench path is resolved
before the script chdirs into the workdir); repeat until the coverage stops
moving. Anything still unjudged afterwards is recorded as UNJUDGED and must be
excluded from subsets — a missing answer is not evidence that a question is hard.

  cd <repo>
  python .agents/skills/protocolqa-bench-production/scripts/fill_gaps.py \
      scheurekabench/benchmark_creation/data/labbench/shard/protocolqa_pool_0926b_s3.json \
      --solver deepseek-v4-flash
"""

import argparse
import os
import sys


def default_workdir():
    """<repo>/scheurekabench/benchmark_creation, derived from this script's path.

    A cwd-relative default breaks when the caller stands inside
    benchmark_creation itself; the skill lives at
    <repo>/.agents/skills/<name>/scripts/, so the repo root is four parents up."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))
    return os.path.join(repo, "scheurekabench", "benchmark_creation")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bench", help="bench or shard JSON to top up")
    parser.add_argument("--solver", default="deepseek-v4-flash")
    parser.add_argument("--workdir", default=None,
                        help="benchmark_creation directory (default: derived from "
                             "the script location, so any cwd works)")
    parser.add_argument("--timeout", type=int, default=40,
                        help="seconds per gateway read attempt (default 40: a hung "
                             "question must fail fast, the rerun will retry it)")
    parser.add_argument("--keep-anthropic-route", action="store_true",
                        help="do not pre-mark the anthropic route as dead")
    args = parser.parse_args()

    os.environ.setdefault("GATEWAY_TIMEOUT", str(args.timeout))
    bench = os.path.abspath(args.bench)       # resolved before the chdir below
    workdir = os.path.abspath(args.workdir) if args.workdir else default_workdir()
    sys.path.insert(0, workdir)
    os.chdir(workdir)

    from utils import llm_gateway                       # noqa: E402
    if not args.keep_anthropic_route:
        dead = getattr(llm_gateway, "_anthropic_dead", None)
        if dead is not None:
            dead.add(args.solver)

    import solve_labbench as sl                         # noqa: E402
    cache = sl.solve(bench, os.path.splitext(bench)[0] + "_eval.json", args.solver)
    print(f"RESULT {os.path.basename(bench)}: {args.solver} has {len(cache)} answers")


if __name__ == "__main__":
    main()
