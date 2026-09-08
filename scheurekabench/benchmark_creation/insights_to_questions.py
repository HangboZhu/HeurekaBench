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
from prompts.insight2question_rubric_prompts import MCQ_RUBRIC_PROMPT, OE_RUBRIC_PROMPT
from utils.claude_code_client import query_claude_code
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
    else:
        raw = ""
        for attempt in (1, 2, 3):  # claude_code backend fails intermittently
            raw = query_claude_code(full_prompt, timeout=1800)
            if raw:
                break
            print(f"Empty claude_code response (attempt {attempt}/3).")
        return raw


def main_structured(insight_json_path, model_call, q_type):
    """Structured generation: every question carries question / answer / rubric.

    Writes '<qtype>_questions' (list of dicts) plus a legacy '{qtype}_question'
    text rendering so evaluate_agent_answer.py keeps working unchanged."""
    with open(insight_json_path, "r") as f:
        insights_data = json.load(f)

    output_dir = os.path.dirname(insight_json_path)
    output_path = os.path.join(output_dir, f"{q_type}_questions.json")
    output_txt_path = os.path.join(output_dir, f"{q_type}_questions.txt")

    output_dict = {}
    all_questions = ""
    for paper_id, paper_content in insights_data.items():
        insight_text4prompt = ""
        for insight_idx, (insight_id, insight_content) in enumerate(paper_content.items()):
            summary = insight_content["summary"]
            how = insight_content["how"]
            relevant = insight_content["relevant"]

            insight_text4prompt += f"#### Insight #{insight_idx + 1}\n\n* Summary: {summary}\n\n* How it was derived: {how}\n\n* Associated paragraphs from the paper: {relevant}\n\n\n"

        insight_text4prompt = insight_text4prompt.strip()
        prompt_template = MCQ_RUBRIC_PROMPT if q_type == "mcq" else OE_RUBRIC_PROMPT
        # replace (not str.format): the templates embed literal JSON braces
        full_prompt = prompt_template.replace("{insights}", insight_text4prompt)

        raw = generate_raw(full_prompt, model_call)
        items = validate_structured(extract_json(raw), q_type)
        if items is None:
            raw_path = os.path.join(output_dir, f"{q_type}_questions_raw.txt")
            with open(raw_path, "w") as f:
                f.write(raw or "(empty LLM response)")
            raise SystemExit(
                f"ERROR: no parseable structured questions for paper {paper_id}; "
                f"raw output saved to '{raw_path}'. Existing output files were left untouched."
            )

        # Self-containment guard: questions referencing the source article get one
        # corrective regeneration; keep whichever draft leaks less.
        leaks = find_source_leakage(items)
        if leaks:
            print(f"{paper_id}: {len(leaks)} question(s) reference the source article; regenerating once with feedback...")
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
        print(f"Structured questions generated for paper {paper_id}.")
        if leaks:
            print(f"WARNING: {len(leaks)} question(s) still reference the source (manual cleanup needed):")
            for iid, no, match, snippet in leaks:
                print(f'  - insight {iid} question {no}: "{match}" in "{snippet}..."')

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
    parser.add_argument("--model_call", type=str, default="gpt", choices=["gpt", "claude", "claude_code"])
    parser.add_argument("--qtype", type=str, choices=["mcq", "oe"])
    parser.add_argument("--structured", action="store_true",
                        help="Generate structured questions (question / answer / grading rubric) as JSON; "
                             "also renders the legacy '{qtype}_question' text for the evaluation scripts")
    args = parser.parse_args()

    if args.structured:
        main_structured(args.insight_json_path, args.model_call, args.qtype)
    else:
        main(args.insight_json_path, args.model_call, args.qtype)
