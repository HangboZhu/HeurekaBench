"""Pick-pass vs retest-pass scores (and letter agreement) for every subset.

Why two passes: solver caches are keyed by bench file name + question index, so
copying a subset to `<stem>_<suffix>_retest.json` gives the second run an empty
cache. The gap between the two passes is the single-sample noise a difficulty
claim has to be read against — MiniMax-M3 flips roughly two thirds of its
answers between passes, deepseek-v4-flash about one in ten. That is why the
difficulty statement names the solver, and why subsets must never be *chosen*
by an M3-only pass.

Expects, next to the pool bench:

  <stem>.json / <stem>_eval.json                  candidate pool
  <stem>_<suffix>.json                            each difficulty subset
  <stem>_<suffix>_retest.json                     its rerun copy
  labbench_solved_<stem>_<model>.json             pick-pass answers
  labbench_solved_<stem>_<suffix>_retest_<model>.json   retest-pass answers

The script never chdirs, so any cwd works as long as the --pool path is right:

  cd <repo>
  python .agents/skills/protocolqa-bench-production/scripts/subset_retest_stats.py \
      --pool scheurekabench/benchmark_creation/data/labbench/protocolqa_pool_0926b.json \
      --models MiniMax-M3,deepseek-v4-flash
"""

import argparse
import json
import os


def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def stats(pool, subset, pick_cache, retest_cache, index_map):
    pick = [pick_cache.get(str(i)) for i in index_map]
    retest = [retest_cache.get(str(j)) for j in range(len(subset))]
    pairs = [(a, b) for a, b in zip(pick, retest) if a and b]
    same = sum(1 for a, b in pairs if a["letter"] == b["letter"])
    pick_mean = sum(a["score"] for a in pick if a) / max(1, sum(1 for a in pick if a))
    retest_mean = sum(b["score"] for b in retest if b) / max(1, sum(1 for b in retest if b))
    return pick_mean, retest_mean, same, len(pairs), \
        sum(1 for a in pick if a), sum(1 for b in retest if b)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pool", required=True, help="candidate pool JSON")
    parser.add_argument("--models", default="MiniMax-M3,deepseek-v4-flash")
    parser.add_argument("--suffixes", default="dsonly,filtered,official,hard")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    suffixes = [s.strip() for s in args.suffixes.split(",") if s.strip()]
    stem, _ = os.path.splitext(args.pool)
    workdir = os.path.dirname(os.path.abspath(args.pool))
    stem_name = os.path.basename(stem)

    pool = load(args.pool)
    pool_index = {(e["question"], e["ideal"]): i for i, e in enumerate(pool)}

    for suffix in suffixes:
        subset_path = f"{stem}_{suffix}.json"
        if not os.path.exists(subset_path):
            print(f"[{suffix}] missing {subset_path}")
            continue
        subset = load(subset_path)
        index_map = [pool_index[(e["question"], e["ideal"])] for e in subset]
        print(f"\n=== _{suffix}: {len(subset)} items ===")
        for model in models:
            pick_cache = load(f"{workdir}/labbench_solved_{stem_name}_{model}.json")
            retest_path = (f"{workdir}/labbench_solved_{stem_name}_{suffix}"
                           f"_retest_{model}.json")
            if not os.path.exists(retest_path):
                print(f"  {model}: no retest cache ({os.path.basename(retest_path)})")
                continue
            pick_mean, retest_mean, same, compared, n_pick, n_retest = stats(
                pool, subset, pick_cache, load(retest_path), index_map)
            print(f"  {model}: pick={pick_mean:.3f} (n={n_pick}) "
                  f"retest={retest_mean:.3f} (n={n_retest}) "
                  f"agreement={same}/{compared} ({100 * same / max(1, compared):.0f}%)")


if __name__ == "__main__":
    main()
