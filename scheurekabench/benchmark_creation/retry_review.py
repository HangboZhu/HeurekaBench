"""One-off: redo the final agent review for one paper and patch its transcript.

Usage: python retry_review.py <paper_dir>
Used when a review output was unparseable (debate_insights.py marks those
pass=None and a plain re-run will not retry them).
"""
import sys
import json

from debate_insights import review_final_insights, save_json
from insights import extract_text_from_pdf_cleaned

paper_dir = sys.argv[1].rstrip("/")
final = json.load(open(f"{paper_dir}/insights_debate_claude_code.json"))[paper_dir.split("/")[-1]]
by_id = {int(k.replace("Insight", "")): rec for k, rec in final.items()}
paper_text = extract_text_from_pdf_cleaned(f"{paper_dir}/paper.pdf")

reviews = None
for attempt in (1, 2, 3):
    print(f"review attempt {attempt}/3...")
    reviews = review_final_insights("claude_code", paper_text, by_id)
    if reviews is not None:
        break
if reviews is None:
    raise SystemExit(f"review for {paper_dir} still unparseable after 3 attempts")

transcript_path = f"{paper_dir}/debate_transcript_claude_code.json"
transcript = json.load(open(transcript_path))
for iid in by_id:
    key = f"Insight{iid}"
    transcript.setdefault(key, {})["review"] = reviews.get(
        iid, {"pass": None, "issues": "not covered by reviewer output"})
save_json(transcript_path, transcript)
failed = [k for k, v in reviews.items() if v["pass"] is False]
print(f"review ok; failed insights: {failed or 'none'}")
