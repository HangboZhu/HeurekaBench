"""Recovery path: assemble insights.json from the debate transcript.

The gateway's anthropic endpoint is currently timing out on paper-sized
prompts, so Insights 9-10 cannot be judged right now. Everything already
judged is final; rebuild the same outputs process_paper() would write
(final insights -> agent review -> insights_debate_*.json -> insights.json)
without re-running the debate. A later resume run can still add 9-10 and
rewrite insights.json with --force.

Run from scheurekabench/ with CLAUDE_CODE_MODEL=MiniMax-M3 in the env.
"""
import json
import os
import sys

sys.path.insert(0, 'benchmark_creation')
import debate_insights as D  # noqa: E402
from insights import extract_text_from_pdf_cleaned  # noqa: E402

RES = '../molintbench/Aging-Res'
PAPER_ID = 'Aging-Res'
transcript_path = os.path.join(RES, 'debate_transcript_claude_code.json')
transcript = json.load(open(transcript_path, encoding='utf-8'))
insights = D.parse_insights_v2(os.path.join(RES, 'insights_paragraphs_claude_code.txt'))
paper_text = extract_text_from_pdf_cleaned(os.path.join(RES, 'paper.pdf'))

final = {}
for idx in range(1, len(insights) + 1):
    key = f'Insight{idx}'
    judgement = transcript.get(key, {}).get('judge')
    if not judgement or judgement.get('verdict') == 'reject':
        continue
    final[key] = {
        'summary': judgement['summary'],
        'how': judgement['how'],
        'relevant': judgement['relevant'],
    }
print('verified insights:', ', '.join(final), flush=True)

pending = [k for k in final if not transcript.get(k, {}).get('review')]
if pending:
    print(f'agent review with claude_code (CLAUDE_CODE_MODEL={os.getenv("CLAUDE_CODE_MODEL")}) ...', flush=True)
    by_id = {int(k.replace('Insight', '')): rec for k, rec in final.items()}
    reviews = D.review_final_insights('claude_code', paper_text, by_id)
    if reviews is None:
        for k in final:
            transcript.setdefault(k, {})['review'] = {'pass': None, 'issues': 'review output unparseable'}
        print('review unparseable; kept but flagged')
    else:
        for iid in by_id:
            transcript.setdefault(f'Insight{iid}', {})['review'] = reviews.get(
                iid, {'pass': None, 'issues': 'not covered by reviewer output'})
        print('review parsed for', len(reviews), 'insights')
    D.save_json(transcript_path, transcript)

flagged = [k for k in final if (transcript.get(k, {}).get('review') or {}).get('pass') is False]
if flagged:
    print('review flagged (kept):', ', '.join(flagged))

D.save_json(os.path.join(RES, 'insights_debate_claude_code.json'), {PAPER_ID: final})
out_path = os.path.join(RES, 'insights.json')
if os.path.exists(out_path):
    print(f"'{out_path}' exists; preserved (use --force-style overwrite to replace)")
else:
    D.save_json(out_path, {PAPER_ID: final})
    print(f"wrote {len(final)} verified insights to '{out_path}'")
