"""Quick calibration: check whether the negation-guarded grader prompt catches
reversed, negated, and missing-fact answers. Runs 3 questions × 5 answer types
= 15 grading calls (~15-30 min)."""
import json, re, sys, time
sys.path.insert(0, 'benchmark_creation')
from utils import llm_gateway as g
from geval_prompts.eval_prompts import get_g_eval_prompt

prompt_tmpl, sys_msg = get_g_eval_prompt('basic')

ev = json.load(open('../molintbench/Aging-Res/oe_questions_eval.json', encoding='utf-8'))

# Pick 3 diverse questions: #1 (intersection), #10 (senescence panel collapse), #19 (population)
indices = [0, 9, 18]  # 0-base

def make_answers(q):
    gt = q['answer']
    # reversed: negate the main claim
    rev = gt.replace(' increased ', ' decreased ').replace(' rose ', ' fell ')
    rev = rev.replace(' present ', ' absent ').replace(' reduces ', ' increases ')
    rev = rev.replace(' More consistent ', ' Less consistent ').replace(' positively ', ' negatively ')
    # keyword-negated: insert "no evidence for" before each key claim
    import re as _re
    neg = _re.sub(r'(p16ink4a|Cdkn2a|Lgals3|Trem2|Tmem173|Tyrobp)\s+(\S+)',
                  r'no evidence that \1 \2', gt)
    # missing-facts: drop the last paragraph
    parts = gt.rsplit('\n\n', 1)
    miss = parts[0] if len(parts) > 1 else gt[:len(gt)//2]
    return [
        ('gold', gt),
        ('reversed', rev),
        ('negated', neg),
        ('missing-facts', miss),
        ('empty', ''),
    ]


def grade(question, answer, gt_answer, label):
    prompt = prompt_tmpl.format(answer=answer, gt_answer=gt_answer)
    for attempt in (1, 2):
        raw = g.chat(prompt, model='gpt-5.6-sol', max_tokens=64, attempts=1)
        import re
        m = re.search(r'<rating>\s*([1-5])\s*</rating>', raw or '')
        if m:
            return int(m.group(1)), raw[:100]
        print(f'    attempt {attempt}: no rating tag in "{raw[:80]}..."')
        time.sleep(10)
    return None, ''


print(f'Testing {len(indices)} questions × 5 answer types = {len(indices)*5} calls...\n')
failures = 0
for idx in indices:
    q = ev[idx]
    print(f'=== #0-base={idx} (stem {len(q["question"])} chars) ===')
    for label, answer in make_answers(q):
        t0 = time.time()
        rating, raw = grade(q['question'], answer, q['answer'], label)
        dt = time.time() - t0
        expected = {'gold': 5, 'reversed': (1, 2, 3), 'negated': (1, 2, 3), 'missing-facts': (1, 2, 3), 'empty': 1}
        ok = rating == expected[label] if isinstance(expected[label], int) else rating in expected[label]
        status = '✅' if ok else '❌'
        if not ok: failures += 1
        print(f'  {status} {label:15s} → {rating} ({dt:.0f}s) raw={raw[:60]}')
    print()

if failures:
    print(f'FAIL: {failures} false acceptances (guard not working).')
else:
    print('PASS: all answer types correctly classified.')