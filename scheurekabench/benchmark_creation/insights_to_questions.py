import openai
import anthropic
import os
import time
import re
import argparse
import json
import dotenv
from prompts.insight2question_prompt import insight2question_prompt_text as insight2mcq_question_prompt_text
from prompts.insight2open_question_prompt import insight2question_prompt_text as insight2open_question_prompt_text
from prompts.insight2question_rubric_prompts import (mcq_rubric_prompt, oe_rubric_prompt,
                                                    mcq_recall_prompt, mcq_pool_prompt)
from utils.claude_code_client import query_claude_code
from utils import llm_gateway
from debate_insights import extract_json

# Load environment variables from .env file
dotenv.load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-4o"
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
CLAUDE_MODEL = "claude-sonnet-4-20250514"
CLAUDE_MAX_TOKENS = {
    "claude-3-5-haiku-latest": 8192,
    "claude-opus-4-20250514": 16384,
    "claude-sonnet-4-20250514": 16384,
}

def clean_text(text):
    text = re.sub(r'^.*?(?:(?:, [A-Z][a-z]+){3,}).*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'(bioRxiv|Springer|Elsevier|doi:|arXiv|All rights reserved|et\xa0al.).*', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\S+@\S+', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()

def query_gpt(full_prompt):
    try:
        response = openai.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.3,
            max_tokens=16384
        )
        return response.choices[0].message.content
    except Exception as e:
        print("GPT API call failed:", e)
        return ""

def query_claude(full_prompt):
    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=CLAUDE_MAX_TOKENS[CLAUDE_MODEL],
            temperature=0.3,
            messages=[{"role": "user", "content": full_prompt}]
        )
        return response.content[0].text
    except Exception as e:
        print("Claude API call failed:", e)
        return ""

def render_legacy_text(questions, q_type):
    """Render structured questions as the legacy '{qtype}_question' string that
    evaluate_agent_answer.py parses with its Question/Answer regexes."""
    parts = []
    for i, q in enumerate(questions, 1):
        if q_type == "mcq":
            options = "\n".join(f"{letter}) {text}" for letter, text in q["options"].items())
            parts.append(f"**Question{i}:** {q['question']}\n{options}\n**Answer{i}:** {q['answer']}")
        else:
            parts.append(f"**Question{i}:** {q['question']}\n\n**Answer{i}:** {q['answer']}")
    return "\n\n\n".join(parts)

def render_rubric_block(rubric, q_type):
    lines = []
    if q_type == "mcq":
        if rubric.get("correct_reasoning"):
            lines.append(f"**Why correct:** {rubric['correct_reasoning']}")
        for letter, why in (rubric.get("distractor_analysis") or {}).items():
            lines.append(f"**Why {letter} is wrong:** {why}")
    else:
        facts = rubric.get("facts") or []
        if facts:
            lines.append("**Grading facts:**")
            lines.extend(f"- {fact}" for fact in facts)
        if rubric.get("scoring_guide"):
            lines.append(f"**Scoring guide:** {rubric['scoring_guide']}")
    return "\n".join(lines)

def validate_structured(parsed, q_type):
    """Normalize the LLM JSON into [{"insight_index": int|None, "questions": [...]}],
    dropping malformed entries. Returns None if nothing usable."""
    if not isinstance(parsed, list):
        return None
    cleaned = []
    for item in parsed:
        if not isinstance(item, dict) or not isinstance(item.get("questions"), list):
            continue
        try:
            idx = int(item.get("insight_index"))
        except (TypeError, ValueError):
            idx = None
        questions = []
        for q in item["questions"]:
            if not isinstance(q, dict) or not str(q.get("question") or "").strip():
                continue
            entry = {
                "question": str(q["question"]).strip(),
                "answer": str(q.get("answer") or "").strip(),
            }
            q_type_tag = str(q.get("question_type") or "").strip().lower()
            if q_type_tag:  # difficulty-ladder tag (extraction/comparison/causal/
                entry["question_type"] = q_type_tag  # multi_hop/counterfactual/experimental)
            if q_type == "mcq":
                options = q.get("options")
                if not isinstance(options, dict) or not options:
                    continue
                entry["options"] = {str(k).strip(): str(v).strip() for k, v in options.items()}
            entry["rubric"] = q.get("rubric") if isinstance(q.get("rubric"), dict) else {}
            questions.append(entry)
        if questions:
            cleaned.append({"insight_index": idx, "questions": questions})
    return cleaned or None


# Questions must be answerable without the source article (self-containment rule
# in prompts/insight2question_rubric_prompts.py). These patterns indicate a
# question still leans on the paper instead of standing alone.
SOURCE_LEAK_RE = re.compile(
    r"\b(?:the\s+article|the\s+paper|this\s+paper|the\s+review|this\s+review|"
    r"the\s+study|this\s+study|the\s+text|the\s+passage|the\s+material|"
    r"according\s+to|as\s+described|described\s+in|mentioned|the\s+authors|"
    r"reported\s+in|figure\s+\d+|table\s+\d+|section)\b",
    re.IGNORECASE,
)


def find_source_leakage(items):
    """Flag structured questions that reference the source article.
    Returns [(insight_index, question_no, matched_text, question_snippet)]."""
    leaks = []
    for item in items or []:
        for no, q in enumerate(item.get("questions", []), 1):
            m = SOURCE_LEAK_RE.search(q.get("question", ""))
            if m:
                leaks.append((item.get("insight_index"), no, m.group(0), q["question"][:60]))
    return leaks

def generate_raw(full_prompt, model_call):
    """One LLM call through the selected backend."""
    if model_call == "gpt":
        return query_gpt(full_prompt)
    elif model_call == "claude":
        return query_claude(full_prompt)
    elif model_call == "gateway":
        model = os.getenv("QUESTION_MODEL") or (llm_gateway.model_pool() or [None])[0]
        if not model:
            print("No gateway model configured: set MODEL_NAME (or QUESTION_MODEL) in .env.")
            return ""
        return llm_gateway.chat(full_prompt, model=model)
    else:
        raw = ""
        for attempt in (1, 2, 3):  # claude_code backend fails intermittently
            raw = query_claude_code(full_prompt, timeout=1800)
            if raw:
                break
            print(f"Empty claude_code response (attempt {attempt}/3).")
        return raw


def insight_prompt_block(entries, start_index=1):
    """Render insights as the '#### Insight #N' block the rubric prompts expect."""
    return "\n\n".join(
        f"#### Insight #{start_index + offset}\n\n"
        f"* Summary: {content['summary']}\n\n"
        f"* How it was derived: {content['how']}\n\n"
        f"* Associated paragraphs from the paper: {content['relevant']}"
        for offset, (_, content) in enumerate(entries)
    )


def generate_checked(prompt_template, block, model_call, q_type, output_dir, context,
                     parse_attempts=3):
    """One generation pass + source-leak guard (one corrective regeneration,
    keeping whichever draft leaks less). Returns the structured items, or
    exits with the raw response saved when nothing parses.

    Parse failures are retried up to `parse_attempts` times: the transport
    occasionally drops the tail of long responses, and the same request
    usually comes back intact on a retry — aborting the whole paper because
    one stochastic call got truncated wastes the calls already spent."""
    full_prompt = prompt_template.replace("{insights}", block)
    raw = ""
    for attempt in range(1, parse_attempts + 1):
        raw = generate_raw(full_prompt, model_call)
        items = validate_structured(extract_json(raw), q_type)
        if items is not None:
            break
        print(f"{context}: unparseable structured output "
              f"(attempt {attempt}/{parse_attempts}); retrying...")
    if items is None:
        raw_path = os.path.join(output_dir, f"{q_type}_questions_raw.txt")
        with open(raw_path, "w") as f:
            f.write(raw or "(empty LLM response)")
        raise SystemExit(
            f"ERROR: no parseable structured questions for {context}; "
            f"raw output saved to '{raw_path}'. Existing output files were left untouched."
        )

    leaks = find_source_leakage(items)
    if leaks:
        print(f"{context}: {len(leaks)} question(s) reference the source article; regenerating once with feedback...")
        feedback = "\n".join(
            f'- insight {iid} question {no}: "{match}" in "{snippet}..."'
            for iid, no, match, snippet in leaks
        )
        retry_prompt = full_prompt + (
            "\n\n### CORRECTION FEEDBACK ON YOUR PREVIOUS DRAFT\n"
            "Your previous draft contained questions that violate the self-containment rule: "
            "they presuppose or reference the source article, which the reader does not have. "
            "Regenerate ALL questions for ALL insights as fully self-contained, fixing at least "
            "these violations:\n" + feedback
        )
        items2 = validate_structured(extract_json(generate_raw(retry_prompt, model_call)), q_type)
        leaks2 = find_source_leakage(items2)
        if items2 is not None and len(leaks2) < len(leaks):
            items, leaks = items2, leaks2
    if leaks:
        print(f"WARNING: {len(leaks)} question(s) still reference the source (manual cleanup needed):")
        for iid, no, match, snippet in leaks:
            print(f'  - insight {iid} question {no}: "{match}" in "{snippet}..."')
    return items


def main_structured(insight_json_path, model_call, q_type, per_insight=2, split_calls=False,
                    style="self_contained"):
    """Structured generation: every question carries question / answer / rubric.

    Writes '<qtype>_questions' (list of dicts) plus a legacy '{qtype}_question'
    text rendering so evaluate_agent_answer.py keeps working unchanged.
    `per_insight` is forwarded to the prompt builders (2 = original behaviour).
    `split_calls` asks the LLM once per insight instead of once per paper —
    small prompts come back intact, whereas the single all-insights response
    is long enough that the transport sometimes drops its head (see the
    same rationale in difficulty_loop.build_insight_prompt).
    `style` selects the question-design rule: "self_contained" (default) keeps
    the premise-complete stems, "litqa_recall" emits LAB-Bench LitQA2-style
    literature-recall items (MCQ only) that require knowledge of the article."""
    with open(insight_json_path, "r") as f:
        insights_data = json.load(f)

    output_dir = os.path.dirname(insight_json_path)
    output_path = os.path.join(output_dir, f"{q_type}_questions.json")
    output_txt_path = os.path.join(output_dir, f"{q_type}_questions.txt")

    output_dict = {}
    all_questions = ""
    for paper_id, paper_content in insights_data.items():
        if q_type == "mcq":
            if style == "litqa_recall":
                prompt_template = mcq_recall_prompt(per_insight)
            elif per_insight > 2:  # candidate pool: N questions spanning the ladder
                prompt_template = mcq_pool_prompt(per_insight)
            else:
                prompt_template = mcq_rubric_prompt(per_insight)
        else:
            prompt_template = oe_rubric_prompt(per_insight)
        entries = list(paper_content.items())

        if split_calls:
            items = []
            for idx, entry in enumerate(entries, 1):
                # Numbering restarts inside each single-insight prompt, so the
                # model always sees "Insight #1" and the index is re-anchored here.
                block = insight_prompt_block([entry], start_index=1)
                if q_type == "mcq" and per_insight <= 2:
                    # An LLM asked one question at a time cannot balance answer
                    # positions across the pack, and left alone it concentrates
                    # them (observed: 8/10 keyed at B, i.e. 80% for a constant
                    # guess). Position carries no information, so assign it here.
                    # (Pool mode asks for several questions in one call, where a
                    # single position would collide across them; rebalance_mcq_positions.py
                    # redistributes those deterministically after generation.)
                    block += (
                        f"\n\n### ANSWER POSITION FOR THIS QUESTION\n"
                        f"If this question has a single keyed option, place it at position "
                        f"{'ABCD'[(idx - 1) % 4]}. Options are otherwise ordered freely; the "
                        f"assignment exists only so the key is not concentrated in one slot."
                    )
                parsed = generate_checked(prompt_template, block, model_call, q_type,
                                          output_dir, f"{paper_id}/{entry[0]}")
                questions = parsed[0]["questions"] if parsed else []
                items.append({"insight_index": idx, "questions": questions})
        else:
            block = insight_prompt_block(entries, start_index=1)
            items = generate_checked(prompt_template, block, model_call, q_type,
                                     output_dir, f"paper {paper_id}")

        print(f"Structured questions generated for paper {paper_id}.")

        by_index = {it["insight_index"]: it["questions"] for it in items if it["insight_index"]}
        if not by_index:  # model omitted insight_index: fall back to positional order
            by_index = {i: it["questions"] for i, it in enumerate(items, 1)}
        leftover = [it["questions"] for it in items if not it["insight_index"]]

        paper_txt = f"Paper ID: {paper_id}\n"
        for insight_idx, (insight_id, insight_content) in enumerate(paper_content.items(), 1):
            questions = by_index.get(insight_idx) or (leftover.pop(0) if leftover else [])
            if not questions:
                print(f"WARNING: no questions generated for {paper_id}/{insight_id}.")
            insight_content[f"{q_type}_questions"] = questions
            insight_content[f"{q_type}_question"] = (
                render_legacy_text(questions, q_type) if questions else "No question generated."
            )
            paper_txt += f"\n#### Insight #{insight_idx} ({insight_id})\n*Summary:* {insight_content['summary']}\n"
            for i, q in enumerate(questions, 1):
                if q_type == "mcq":
                    options = "\n".join(f"{letter}) {text}" for letter, text in q["options"].items())
                    paper_txt += f"\n**Question{i}:** {q['question']}\n{options}\n**Answer{i}:** {q['answer']}\n"
                else:
                    paper_txt += f"\n**Question{i}:** {q['question']}\n\n**Answer{i}:** {q['answer']}\n"
                rubric_block = render_rubric_block(q.get("rubric", {}), q_type)
                if rubric_block:
                    paper_txt += f"\n**Rubric{i}:**\n{rubric_block}\n"
            paper_txt += "\n==========\n\n"
        output_dict[paper_id] = paper_content
        all_questions += paper_txt
        time.sleep(60)

    with open(output_txt_path, "w") as out_txt_f:
        out_txt_f.write(all_questions)

    with open(output_path, "w") as out_f:
        json.dump(output_dict, out_f, indent=2)

    # Bare question/answer/rubric export for agent review systems (no insight
    # provenance fields); list order = question id.
    from export_eval_questions import export_eval
    n_eval, eval_path = export_eval(output_path, q_type)
    print(f"Eval export: {n_eval} bare {q_type} question(s) -> '{eval_path}'")


def main(insight_json_path, model_call, q_type):
    with open(insight_json_path, "r") as f:
        insights_data = json.load(f)

    output_dir = os.path.dirname(insight_json_path)
    output_path = os.path.join(output_dir, f"{q_type}_questions.json")
    output_txt_path = os.path.join(output_dir, f"{q_type}_questions.txt")

    all_questions = ""
    output_dict = {}
    for paper_id, paper_content in insights_data.items():
        insight_text4prompt = ""
        for insight_idx, (insight_id, insight_content) in enumerate(paper_content.items()):
            summary = insight_content["summary"]
            how = insight_content["how"]
            relevant = insight_content["relevant"]

            insight_text4prompt += f"#### Insight #{insight_idx + 1}\n\n* Summary: {summary}\n\n* How it was derived: {how}\n\n* Associated paragraphs from the paper: {relevant}\n\n\n"

        insight_text4prompt = insight_text4prompt.strip()
        if q_type == "mcq":
            full_prompt = insight2mcq_question_prompt_text.format(insights=insight_text4prompt)
        else:
            full_prompt = insight2open_question_prompt_text.format(insights=insight_text4prompt)
            
        if model_call == "gpt":
            questions = query_gpt(full_prompt)
        elif model_call == "claude":
            questions = query_claude(full_prompt)
        else:
            questions = query_claude_code(full_prompt, timeout=1800)
        if questions:
            print(f"Question generated for paper {paper_id}.")
        else:
            print(f"No question returned for paper {paper_id}.")
            questions = "No question generated."

        # parse questions into list for each insight; between each insight is ---
        q_per_insight_list = [q.strip() for q in questions.split('---') if q.strip()]

        for idx, (insight_id, insight_content) in enumerate(paper_content.items()):
            if idx < len(q_per_insight_list):
                insight_content[f"{q_type}_question"] = q_per_insight_list[idx]
            else:
                insight_content[f"{q_type}_question"] = "No question generated."
        output_dict[paper_id] = paper_content
        all_questions += f"Paper ID: {paper_id}\n{questions}\n==========\n\n"

        time.sleep(60)
    with open(output_txt_path, "w") as out_txt_f:
        out_txt_f.write(all_questions)

    with open(output_path, "w") as out_f:
        json.dump(output_dict, out_f, indent=2)
    
 
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--insight_json_path", type=str, required=True, help="Path to insights.json file")
    parser.add_argument("--model_call", type=str, default="gpt", choices=["gpt", "claude", "claude_code", "gateway"])
    parser.add_argument("--qtype", type=str, choices=["mcq", "oe"])
    parser.add_argument("--structured", action="store_true",
                        help="Generate structured questions (question / answer / grading rubric) as JSON; "
                             "also renders the legacy '{qtype}_question' text for the evaluation scripts")
    parser.add_argument("--per-insight", type=int, default=2, choices=[1, 2, 3, 4, 5, 6, 7, 8],
                        dest="per_insight",
                        help="Questions generated per insight (default 2). With 1, the single question "
                             "must be the higher tier of the difficulty ladder (counterfactual/multi_hop). "
                             "With 3..8 the candidate-pool prompt is used: N questions spanning the "
                             "ladder in one call, meant to be filtered by solver testing (drop easy).")
    parser.add_argument("--split-calls", action="store_true",
                        help="Ask the LLM once per insight instead of once per paper. Much more reliable "
                             "for large packs: the single all-insights response is long enough that the "
                             "transport sometimes returns it head-truncated.")
    parser.add_argument("--style", type=str, default="self_contained",
                        choices=["self_contained", "litqa_recall"],
                        help="Question-design rule: self_contained (default) embeds every premise in the "
                             "stem; litqa_recall emits LAB-Bench LitQA2-style literature-recall items "
                             "whose answers require knowledge of the source article (MCQ only)")
    args = parser.parse_args()

    if args.style == "litqa_recall" and args.qtype != "mcq":
        raise SystemExit("ERROR: --style litqa_recall supports --qtype mcq only "
                         "(literature recall has no open-ended rubric form).")
    if args.structured:
        main_structured(args.insight_json_path, args.model_call, args.qtype, args.per_insight,
                        args.split_calls, args.style)
    else:
        main(args.insight_json_path, args.model_call, args.qtype)
