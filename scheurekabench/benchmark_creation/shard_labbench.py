"""Split a LAB-Bench file into shards, and merge the shards' caches back.

Scoring a large pool with solve_labbench.py is serialized per solver: one
question whose gateway call stalls for minutes delays every question behind
it. Sharding turns that into K independent processes whose stalls overlap
(PIPELINE.md §5.5). Caches are keyed by the bench *filename* stem, so shards
never touch each other's cache files — but that also means the shard caches
must be mapped back onto global question indices afterwards, which is what
--merge does.

  # 1) slice the pool (writes shard/litqa2_pool_s<i>.json + _eval.json)
  python shard_labbench.py --bench data/labbench/litqa2_pool.json --shards 6 --split

  # 2) one process per shard, all solvers
  for s in 0 1 2 3 4 5; do
    python solve_labbench.py data/labbench/shard/litqa2_pool_s$s.json \
        --solver deepseek-v4-flash,qwen3.7-max &
  done; wait

  # 3) back into the main cache (and delete the shard scaffolding)
  python shard_labbench.py --bench data/labbench/litqa2_pool.json --shards 6 \
      --merge --models deepseek-v4-flash,qwen3.7-max [--clean]

Each shard's local cache is pre-seeded from the main cache on --split when
answers already exist, so re-slicing never re-asks a question the main cache
can answer. --merge refuses to overwrite an existing main-cache entry with a
different reply unless --overwrite is given.

The manifest is shared by every bench in the shard directory, so two batches
that shard concurrently overwrite each other's entry; --merge therefore trusts
it only when it names the bench being merged and otherwise rebuilds the bounds
from that bench's own shard files.
"""

import os
import re
import json
import math
import shutil
import argparse


def shard_paths(bench_path, shards=0):
    """(dir, main stem, [shard stems]) — names the split and merge agree on."""
    d = os.path.join(os.path.dirname(os.path.abspath(bench_path)), "shard")
    stem = os.path.splitext(os.path.basename(bench_path))[0]
    return d, stem, [f"{stem}_s{i}" for i in range(shards)]


def bounds_for(n, shards):
    return [(math.floor(i * n / shards), math.floor((i + 1) * n / shards))
            for i in range(shards)]


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def split(bench_path, shards, models):
    d, stem, shard_stems = shard_paths(bench_path, shards)
    os.makedirs(d, exist_ok=True)
    bench = load_json(bench_path)
    sidecar = load_json(os.path.splitext(bench_path)[0] + "_eval.json")
    if sidecar is None:
        raise SystemExit(f"ERROR: sidecar for {bench_path} not found.")
    bounds = bounds_for(len(bench), shards)
    for i, (a, b) in enumerate(bounds):
        dst = os.path.join(d, f"{shard_stems[i]}.json")
        dst_side = os.path.join(d, f"{shard_stems[i]}_eval.json")
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(bench[a:b], f, indent=1, ensure_ascii=False)
        with open(dst_side, "w", encoding="utf-8") as f:
            json.dump(sidecar[a:b], f, indent=1, ensure_ascii=False)
        print(f"shard {i}: global [{a}, {b}) -> {dst}")
    with open(os.path.join(d, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"bench": os.path.abspath(bench_path), "shards": shards,
                   "bounds": bounds, "models": models}, f, indent=1)
    for model in models:
        main = load_json(os.path.join(os.path.dirname(os.path.abspath(bench_path)),
                                      f"labbench_solved_{stem}_{model}.json"), {})
        for i, (a, b) in enumerate(bounds):
            seeded = {str(gi - a): main[str(gi)] for gi in range(a, b)
                      if str(gi) in main}
            path = os.path.join(d, f"labbench_solved_{shard_stems[i]}_{model}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(seeded, f, indent=2, ensure_ascii=False)
            print(f"  seeded {os.path.basename(path)} from main cache: {len(seeded)}")


def resolve_bounds(bench_path, d, stem, n_questions):
    """Slice bounds for merging: this bench's, never another batch's.

    The manifest is one shared file per shard directory, so a concurrent batch
    that splits in the same directory overwrites it. Trusting it blindly
    re-indexes this bench's cache through the other bench's bounds — a 108-item
    baseline manifest silently shifted a 102-item pool's answers by one slot per
    shard. Only the manifest that names this bench is used; otherwise the bounds
    are rebuilt from this bench's own shard files."""
    manifest = load_json(os.path.join(d, "manifest.json")) or {}
    if os.path.abspath(manifest.get("bench", "")) == os.path.abspath(bench_path):
        bounds = manifest.get("bounds") or []
        if bounds and bounds[-1][1] == n_questions:
            return bounds
        print(f"WARNING: manifest bounds reach {bounds[-1][1] if bounds else None} "
              f"but {os.path.basename(bench_path)} has {n_questions} questions; "
              f"rebuilding the bounds from the shard files.")
    elif manifest:
        print(f"WARNING: shard/manifest.json belongs to another bench "
              f"({os.path.basename(manifest.get('bench', ''))}) — a concurrent batch "
              f"shards in the same directory. Rebuilding the bounds from this "
              f"bench's shard files.")
    shards = 0
    for name in os.listdir(d):
        m = re.fullmatch(rf"{re.escape(stem)}_s(\d+)\.json", name)
        if m:
            shards = max(shards, int(m.group(1)) + 1)
    if shards == 0:
        raise SystemExit(f"ERROR: no shard files for {stem} in {d} — run --split first.")
    print(f"  using {shards} shards over {n_questions} questions.")
    return bounds_for(n_questions, shards)


def merge(bench_path, models, overwrite, clean):
    d, stem, _ = shard_paths(bench_path)
    if not os.path.exists(os.path.join(d, "manifest.json")):
        raise SystemExit(f"ERROR: no manifest in {d} — run --split first.")
    n_questions = len(load_json(bench_path))
    bounds = resolve_bounds(bench_path, d, stem, n_questions)
    bench_dir = os.path.dirname(os.path.abspath(bench_path))
    for model in models:
        main_path = os.path.join(bench_dir, f"labbench_solved_{stem}_{model}.json")
        main = load_json(main_path, {})
        added = replaced = kept = conflicts = 0
        for i, (a, b) in enumerate(bounds):
            shard = load_json(os.path.join(
                d, f"labbench_solved_{stem}_s{i}_{model}.json"), {})
            for local, record in shard.items():
                if not 0 <= int(local) < b - a:
                    raise SystemExit(f"ERROR: shard {i} has local index {local}, "
                                     f"outside its slice of {b - a} questions — "
                                     f"shard files and the manifest disagree.")
                gi = str(a + int(local))
                if gi not in main:
                    main[gi] = record
                    added += 1
                elif main[gi] == record:
                    kept += 1
                elif overwrite:
                    main[gi] = record
                    replaced += 1
                else:
                    conflicts += 1
        with open(main_path, "w", encoding="utf-8") as f:
            json.dump(main, f, indent=2, ensure_ascii=False)
        print(f"{model}: merged into {main_path} — added {added}, identical {kept}, "
              f"replaced {replaced}, conflicts left alone {conflicts}; "
              f"coverage {len(main)}/{n_questions}")
    if clean:
        for name in os.listdir(d):
            if name == "manifest.json":
                continue
            os.remove(os.path.join(d, name))
        shutil.rmtree(d, ignore_errors=True)
        print(f"removed shard scaffolding in {d}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bench", required=True,
                        help="Main bench file the shards are slices of")
    parser.add_argument("--shards", type=int, default=6)
    parser.add_argument("--split", action="store_true")
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--models", default="",
                        help="Comma-separated solver models to (pre-)seed/merge")
    parser.add_argument("--overwrite", action="store_true",
                        help="On --merge, let a shard reply replace a differing "
                             "main-cache entry")
    parser.add_argument("--clean", action="store_true",
                        help="On --merge, delete the shard scaffolding afterwards")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.split == args.merge:
        raise SystemExit("ERROR: pass exactly one of --split / --merge.")
    if args.split:
        split(args.bench, args.shards, models)
    else:
        if not models:
            raise SystemExit("ERROR: --merge needs --models.")
        merge(args.bench, models, args.overwrite, args.clean)


if __name__ == "__main__":
    main()
