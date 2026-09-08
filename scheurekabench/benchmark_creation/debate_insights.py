"""Debate-based insight verification for papers WITHOUT code repositories.

Replaces the code-verification stage (code_insights.py + match_insights.py) of
the original pipeline: for each draft insight extracted by insights.py, 2-3
LLM debaters independently scrutinize it against the paper text, exchange
rebuttals, and a judge issues a final accept/revise/reject ruling. A final
agent review audits the surviving insights.

All debater/judge/reviewer models are served through the OpenAI-compatible
gateway configured in .env (BASE_URL + OPENAI_KEY + MODEL_NAME). Defaults are
resolved from env (CLI wins): debaters <- DEBATE_MODELS or the first two
MODEL_NAME entries, judge <- JUDGE_MODEL or the last MODEL_NAME entry,
reviewer <- REVIEW_MODEL or the local claude code agent ("claude_code"; pass
a gateway model name instead, or "none" to skip).

Outputs per paperX/:
  - debate_transcript_<model_call>.json: full audit trail (resume-safe)
  - insights_debate_<model_call>.json:   machine verdict, molintbench schema
  - insights.json:                       written unless one already exists
                                         (pass --force to overwrite), so a
                                         hand-curated file is never clobbered
"""

import os
import json
import re
import time
import argparse

from insights import extract_text_from_pdf_cleaned
from match_insights import parse_insights_v2
from prompts.debate_prompts import (
    debater_opening_prompt,
    debater_rebuttal_prompt,
    judge_prompt,
    agent_review_prompt,
)
from utils import llm_gateway
from utils.claude_code_client import query_claude_code

EXTRACTION_BACKENDS = ("gpt", "claude", "claude_code", "gateway")


def resolve_roles(debaters_arg, judge_arg, reviewer_arg):
    """Resolve debate roles: CLI > env var > MODEL_NAME-derived default."""
    pool = llm_gateway.model_pool()

    debaters = debaters_arg or os.getenv("DEBATE_MODELS")
    if debaters:
        debaters = [m.strip() for m in debaters.split(",") if m.strip()]
    else:
        debaters = pool[:2]

    judge = judge_arg or os.getenv("JUDGE_MODEL") or (pool[-1] if pool else None)
    reviewer = reviewer_arg or os.getenv("REVIEW_MODEL") or "claude_code"

    if not debaters:
        raise SystemExit(
            "ERROR: no debater models configured. Set MODEL_NAME (comma-separated) in .env "
            "or pass --debaters model_a,model_b."
        )
    if len(debaters) < 2 or len(set(debaters)) != len(debaters):
        raise SystemExit("ERROR: provide 2-3 distinct debater models, e.g. --debaters model_a,model_b.")
    if len(debaters) > 3:
        raise SystemExit(f"ERROR: at most 3 debaters, got {len(debaters)}: {debaters}.")
    if not judge:
        raise SystemExit("ERROR: no judge model configured. Set MODEL_NAME or JUDGE_MODEL in .env, or pass --judge.")
    if reviewer not in ("none", "claude_code") and not reviewer.strip():
        raise SystemExit("ERROR: reviewer must be a gateway model name, 'claude_code', or 'none'.")
    return debaters, judge, reviewer


def _salvage_json(text, start):
    """Best-effort salvage of an array whose head or tail got mangled in
    transit (the gateway/claude CLI occasionally drops leading bytes or
    emits malformed runs). Scans string-aware from `start` (a '{' or '['),
    records every bracket-complete top-level element, parses each element
    INDIVIDUALLY, and returns the list of elements that parse — one broken
    element no longer discards its well-formed siblings. Returns None if no
    complete element survives."""
    base = 1 if text[start] == "[" else 0  # element depth relative to the start
    depth = 0
    in_str = esc = False
    elem_start = None
    elements = []
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            if depth == base and ch == "{":
                elem_start = i
            depth += 1
        elif ch in "]}":
            if depth <= base:  # stray closer: boundary of the dropped outer part
                break
            depth -= 1
            if depth == base and ch == "}" and elem_start is not None:
                elements.append((elem_start, i + 1))
                elem_start = None
    if in_str or not elements:
        return None
    salvaged = []
    for s, e in elements:
        chunk = text[s:e]
        try:
            salvaged.append(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    return salvaged or None


def extract_json(text):
    """Return the first JSON object/array in an LLM response, or None."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if starts:
        start = min(starts)
        end = max(text.rfind("}"), text.rfind("]"))
        if end > start:
            text = text[start:end + 1]
    cleaned = text.strip().strip('`\n ')
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Fallback: salvage the complete top-level elements instead of discarding
    # the whole response — try successive '{'/'[' offsets from the earliest
    # structural character onward (covers head-truncated and tail-mangled
    # outputs; anything before the first surviving element is unrecoverable).
    # Keep the offset that recovers the MOST complete elements: offsets inside
    # a broken head only rescue nested sub-objects (fewer, smaller elements),
    # while the first intact container boundary rescues its every sibling.
    candidates = sorted({m.start() for m in re.finditer(r"[{\[]", text)})[:25]
    best = None  # (count, offset, salvaged)
    for cand in candidates:
        salvaged = _salvage_json(text, cand)
        if salvaged and (best is None or len(salvaged) > best[0]):
            best = (len(salvaged), cand, salvaged)
    if best is not None:
        print(f"extract_json: raw JSON mangled; salvaged {best[0]} complete "
              f"element(s) starting at offset {best[1]}.")
        return best[2]
    return None


def insight_block(insight):
    return (
        f"*Summary:*\n{insight['summary']}\n\n"
        f"*How it was derived:*\n{insight['description']}\n\n"
        f"*Relevant text paragraphs:*\n{insight['relevant_text']}"
    )


def paper_and_insight_section(paper_text, insight):
    return (
        f"### FULL ARTICLE TEXT\n{paper_text}\n\n"
        f"---\n\n### DRAFT INSIGHT UNDER DEBATE\n{insight_block(insight)}"
    )


def opening_statement(model, paper_text, insight):
    prompt = (
        debater_opening_prompt
        + "\n\n---\n\n"
        + paper_and_insight_section(paper_text, insight)
        + f'\n\nYou are Debater "{model}" (substitute this name for "Debater A" in your role above).\n'
    )
    return llm_gateway.chat(prompt, model=model)


def rebuttal_statement(model, paper_text, insight, own_previous, others):
    others_text = "\n\n".join(
        f'#### Statement by Debater "{name}":\n{statement}'
        for name, statement in others.items()
    )
    prompt = (
        debater_rebuttal_prompt
        + "\n\n---\n\n"
        + paper_and_insight_section(paper_text, insight)
        + f'\n\n### YOUR PREVIOUS STATEMENT\n{own_previous}\n\n'
        + f"### LATEST STATEMENTS OF THE OTHER DEBATERS\n{others_text}\n\n"
        + f'You are Debater "{model}" (substitute this name for "Debater A" in your role above).\n'
    )
    return llm_gateway.chat(prompt, model=model)


def run_debate(debaters, rounds, paper_text, insight):
    statements = {m: opening_statement(m, paper_text, insight) for m in debaters}
    transcript_rounds = []
    for rnd in range(1, rounds + 1):
        if rnd > 1:
            updated = {}
            for m in debaters:
                others = {o: s for o, s in statements.items() if o != m}
                updated[m] = rebuttal_statement(m, paper_text, insight, statements[m], others) or statements[m]
            statements = updated
        transcript_rounds.append({
            "round": rnd,
            "statements": {m: s or "(no statement returned)" for m, s in statements.items()},
        })
        time.sleep(1)
    return transcript_rounds


def judge_insight(judge, paper_text, insight, transcript_rounds):
    transcript = ""
    for rnd in transcript_rounds:
        transcript += f"#### Round {rnd['round']}\n"
        for name, statement in rnd["statements"].items():
            transcript += f'\n**Debater "{name}":**\n{statement}\n'
        transcript += "\n"

    prompt = (
        judge_prompt
        + "\n\n---\n\n### DRAFT INSIGHT UNDER DEBATE\n"
        + insight_block(insight)
        + "\n\n### DEBATE TRANSCRIPT\n"
        + transcript.strip()
    )
    for attempt in (1, 2):
        raw = llm_gateway.chat(prompt, model=judge, json_output=True)
        parsed = extract_json(raw)
        if parsed and isinstance(parsed, dict) and parsed.get("verdict"):
            return parsed
        print(f"    Judge output not parseable (attempt {attempt}/2).")
    return None


def normalize_judgement(parsed, insight):
    verdict = str(parsed.get("verdict", "")).strip().lower()
    summary = (parsed.get("summary") or "").strip()
    how = (parsed.get("how") or "").strip()
    relevant = (parsed.get("relevant") or "").strip()
    if verdict not in ("accept", "revise", "reject"):
        verdict = "revise" if summary and how else "reject"
    if verdict in ("accept", "revise"):
        summary = summary or insight["summary"]
        how = how or insight["description"]
        relevant = relevant or insight["relevant_text"]
    return {
        "verdict": verdict,
        "summary": summary,
        "how": how,
        "relevant": relevant,
        "rationale": (parsed.get("rationale") or "").strip(),
        "confidence": parsed.get("confidence"),
    }


def review_final_insights(reviewer, paper_text, final_insights):
    """final_insights: {int_id: {"summary","how","relevant"}} -> {int_id: {"pass","issues"}} or None."""
    listing = ""
    for iid, record in final_insights.items():
        listing += (
            f"#### Insight id: {iid}\n*Summary:*\n{record['summary']}\n\n"
            f"*How it was derived:*\n{record['how']}\n\n"
            f"*Relevant text paragraphs:*\n{record['relevant']}\n\n"
        )
    prompt = (
        agent_review_prompt
        + "\n\n---\n\n### FULL ARTICLE TEXT\n"
        + paper_text
        + "\n\n### FINAL INSIGHTS\n"
        + listing.strip()
    )
    if reviewer == "claude_code":
        raw = query_claude_code(prompt, timeout=1800)
    else:
        raw = llm_gateway.chat(prompt, model=reviewer)

    parsed = extract_json(raw)
    if not isinstance(parsed, list):
        return None
    reviews = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            iid = int(item.get("insight_id"))
        except (TypeError, ValueError):
            continue
        reviews[iid] = {"pass": bool(item.get("pass")), "issues": item.get("issues", "") or ""}
    return reviews


def save_json(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def process_paper(paper_dir, args):
    paper_id = os.path.basename(os.path.normpath(paper_dir))
    pdf_path = os.path.join(paper_dir, "paper.pdf")
    if not os.path.exists(pdf_path):
        print(f"{paper_id}: no paper.pdf found. Skipping.")
        return

    insight_file = os.path.join(paper_dir, f"insights_paragraphs_{args.model_call}.txt")
    if not os.path.exists(insight_file):
        insight_file = os.path.join(paper_dir, "insights_paragraphs_gpt.txt")
    if not os.path.exists(insight_file):
        print(f"{paper_id}: no insights_paragraphs_*.txt found. Run insights.py first. Skipping.")
        return
    insights = parse_insights_v2(insight_file)
    if not insights:
        print(f"{paper_id}: no insights parsed from '{insight_file}'. Skipping.")
        return
    print(f"{paper_id}: {len(insights)} draft insights from '{os.path.basename(insight_file)}'.")

    paper_text = extract_text_from_pdf_cleaned(pdf_path)

    transcript_path = os.path.join(paper_dir, f"debate_transcript_{args.model_call}.json")
    transcript = {}
    if os.path.exists(transcript_path):
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                transcript = json.load(f)
        except json.JSONDecodeError:
            print(f"{paper_id}: existing transcript unreadable, starting fresh.")

    for idx, insight in enumerate(insights, start=1):
        key = f"Insight{idx}"
        record = transcript.get(key, {})
        if record.get("judge"):
            print(f"  {key}: already judged (verdict: {record['judge'].get('verdict')}). Skipping.")
            continue

        if record.get("rounds"):
            transcript_rounds = record["rounds"]
            print(f"  {key}: reusing cached debate rounds, judging only.")
        else:
            print(f"  {key}: debating ({', '.join(args.debaters)}, {args.rounds} round(s))...")
            transcript_rounds = run_debate(args.debaters, args.rounds, paper_text, insight)
            record = {"draft": insight, "rounds": transcript_rounds}

        print(f"  {key}: judging with '{args.judge}'...")
        parsed = judge_insight(args.judge, paper_text, insight, transcript_rounds)
        if parsed is None:
            # No judge key stored, so a re-run retries the judgement (rounds are cached)
            record.pop("judge", None)
            transcript[key] = record
            save_json(transcript_path, transcript)
            print(f"  {key}: judge output unparseable, left pending. Re-run to retry.")
            continue

        record["judge"] = normalize_judgement(parsed, insight)
        transcript[key] = record
        save_json(transcript_path, transcript)
        print(f"  {key}: verdict={record['judge']['verdict']} (confidence: {record['judge']['confidence']}).")

    # Assemble the surviving insights (missing/reject judgements are dropped)
    final = {}
    for idx in range(1, len(insights) + 1):
        key = f"Insight{idx}"
        judgement = transcript.get(key, {}).get("judge")
        if not judgement or judgement["verdict"] == "reject":
            continue
        final[key] = {
            "summary": judgement["summary"],
            "how": judgement["how"],
            "relevant": judgement["relevant"],
        }
    if not final:
        print(f"{paper_id}: no insights survived the debate. Nothing to write.")
        return

    if args.reviewer != "none":
        pending = [k for k in final if not transcript.get(k, {}).get("review")]
        if pending:
            print(f"{paper_id}: final agent review with '{args.reviewer}'...")
            by_id = {int(k.replace("Insight", "")): rec for k, rec in final.items()}
            reviews = review_final_insights(args.reviewer, paper_text, by_id)
            if reviews is None:
                for k in final:
                    transcript.setdefault(k, {})["review"] = {"pass": None, "issues": "review output unparseable"}
                print(f"{paper_id}: agent review unparseable; insights kept but flagged.")
            else:
                for iid in by_id:
                    key = f"Insight{iid}"
                    transcript.setdefault(key, {})["review"] = reviews.get(
                        iid, {"pass": None, "issues": "not covered by reviewer output"}
                    )
            save_json(transcript_path, transcript)

        flagged, dropped = [], []
        for key in list(final):
            review = transcript.get(key, {}).get("review") or {}
            if review.get("pass") is False:
                (dropped if args.strict_review else flagged).append(key)
        for key in dropped:
            del final[key]
        if flagged:
            print(f"{paper_id}: review flagged (kept): {', '.join(flagged)}. See transcript for issues.")
        if dropped:
            print(f"{paper_id}: review failed, dropped (--strict-review): {', '.join(dropped)}.")

    machine_path = os.path.join(paper_dir, f"insights_debate_{args.model_call}.json")
    save_json(machine_path, {paper_id: final})
    print(f"{paper_id}: wrote {len(final)} verified insights to '{machine_path}'.")

    insights_json_path = os.path.join(paper_dir, "insights.json")
    if os.path.exists(insights_json_path) and not args.force:
        print(
            f"{paper_id}: '{insights_json_path}' already exists (possibly hand-curated); preserved. "
            f"Use --force to overwrite."
        )
    else:
        save_json(insights_json_path, {paper_id: final})
        print(f"{paper_id}: wrote '{insights_json_path}' (feed this to insights_to_questions.py).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base_dir", type=str, required=True,
                        help="Directory with paper1/, paper2/, ... each containing paper.pdf (no code/ needed)")
    parser.add_argument("--model_call", type=str, default="gpt", choices=list(EXTRACTION_BACKENDS),
                        help="Backend used for insight extraction; locates insights_paragraphs_<model_call>.txt")
    parser.add_argument("--debaters", type=str, default=None,
                        help="Comma-separated 2-3 gateway model names. Default: DEBATE_MODELS from .env, "
                             "else the first two MODEL_NAME entries.")
    parser.add_argument("--judge", type=str, default=None,
                        help="Gateway model name for the final ruling. Default: JUDGE_MODEL from .env, "
                             "else the last MODEL_NAME entry.")
    parser.add_argument("--reviewer", type=str, default=None,
                        help="Final audit: a gateway model name, 'claude_code' (local agent), or 'none'. "
                             "Default: REVIEW_MODEL from .env, else 'claude_code'.")
    parser.add_argument("--rounds", type=int, default=2,
                        help="Total statement rounds per insight (round 1 = independent openings, later rounds = rebuttals)")
    parser.add_argument("--strict-review", action="store_true",
                        help="Drop insights that fail the final agent review instead of keeping them flagged")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite an existing (possibly hand-curated) insights.json")
    args = parser.parse_args()

    if args.rounds < 1:
        parser.error("--rounds must be >= 1.")
    args.debaters, args.judge, args.reviewer = resolve_roles(args.debaters, args.judge, args.reviewer)
    print(f"Roles -> debaters: {args.debaters} | judge: {args.judge} | reviewer: {args.reviewer}")

    paper_dirs = sorted(
        os.path.join(args.base_dir, d)
        for d in os.listdir(args.base_dir)
        if os.path.isdir(os.path.join(args.base_dir, d))
    )
    print(f"Found {len(paper_dirs)} paper directories in {args.base_dir}.")

    for paper_dir in paper_dirs:
        process_paper(paper_dir, args)


if __name__ == "__main__":
    main()
