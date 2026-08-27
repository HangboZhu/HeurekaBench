import re
import json
import argparse
import subprocess
import threading
import queue
import time
import sys
import os
from biomni_prompts.mcq_initial_prompt import mcq_initial_prompt
from biomni_prompts.oe_initial_prompt import oe_initial_prompt

def parse_mcq_question(text):
    # answer group is optional so that answer-stripped (sanitized) dataset copies still parse
    pattern = r"\*\*Question(\d+):\*\*\s*(.*?)\s*((?:[A-D]\).*?(?:\s+))+)\*\*Answer\1:\*\*\s*([A-D](?:,[A-D])*)?"

    matches = re.findall(pattern, text, re.DOTALL)

    questions_dict = {}
    for num, q_text, options_block, ans in matches:
        options = dict(re.findall(r"([A-D])\)\s*(.*)", options_block))
        questions_dict[f"Question{num}"] = {
            "question": q_text.strip(),
            "options": options,
            # "answer": ans
        }

    return questions_dict

def parse_oe_questions(text):
    # Pattern to match QuestionN blocks (ignore answers)
    pattern = r"\*\*Question(\d+):\*\*\s*(.*?)(?=\s*\*\*Answer\d+:|\Z)"

    matches = re.findall(pattern, text, re.DOTALL)

    questions_dict = {}
    for num, q_text in matches:
        questions_dict[f"Question{num}"] = {
           "question": q_text.strip()
        }
    return questions_dict

def _write_stream_event(fout, line):
    """Parse one stream-json event line and write a human-readable transcript entry.

    Assistant text blocks (and the final result) are written under
    '===== Ai Message =====' headers so that extract_agent_answer.py
    can pick up the <solution>...</solution> tags downstream, exactly
    like the Biomni agent outputs did.
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        fout.write(line.rstrip("\n") + "\n\n")
        return

    etype = event.get("type")

    if etype == "system":
        if event.get("subtype") not in (None, "thinking_tokens"):
            fout.write(f"===== System ===== {event.get('subtype', '')}\n\n")
    elif etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text":
                fout.write(f"===== Ai Message =====\n{block.get('text', '').strip()}\n\n")
            elif block.get("type") == "tool_use":
                tool_input = json.dumps(block.get("input", {}))
                fout.write(f"===== Tool Use ===== {block.get('name', '')}\n{tool_input[:1000]}\n\n")
    elif etype == "user":
        for block in event.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                content = block.get("content", "")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                fout.write(f"===== Tool Result =====\n{str(content)[:1000]}\n\n")
    elif etype == "result":
        fout.write(f"===== Ai Message =====\n{event.get('result', '').strip()}\n\n")
        if event.get("is_error"):
            fout.write(f"===== Error ===== claude returned an error result\n\n")

    fout.flush()

def run_claude_code(prompt, output_path, claude_args):
    """Run one question through the local Claude Code CLI in headless mode.

    The prompt is passed via stdin and the run is streamed with
    --output-format stream-json, so the transcript is written to
    output_path incrementally (useful for long agentic runs).
    """
    cmd = [
        claude_args["claude_path"],
        "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--permission-mode", claude_args["permission_mode"],
        "--max-turns", str(claude_args["max_turns"]),
    ]
    if claude_args["model"] is not None:
        cmd += ["--model", claude_args["model"]]

    # Make sure traffic to the local API gateway (ANTHROPIC_BASE_URL, e.g.
    # 127.0.0.1) never goes through a (possibly dead) system http_proxy.
    env = os.environ.copy()
    no_proxy = env.get("NO_PROXY", env.get("no_proxy", ""))
    env["NO_PROXY"] = env["no_proxy"] = ",".join(p for p in ["127.0.0.1", "localhost", no_proxy] if p)

    with open(output_path, "w") as fout:
        fout.write(f"Claude Code command: {' '.join(cmd)}\nModel: {claude_args['model'] or 'claude default'}\nWorkdir: {claude_args['workdir']}\n\n")
        fout.flush()

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=claude_args["workdir"],
            env=env,
        )
        proc.stdin.write(prompt)
        proc.stdin.close()

        # Reader threads push output lines to a queue so we can enforce a deadline
        # while still streaming the transcript to the output file.
        lines = queue.Queue()
        def _read(stream, tag):
            for line in stream:
                lines.put((tag, line))
            lines.put((tag, None))
        threading.Thread(target=_read, args=(proc.stdout, "stdout"), daemon=True).start()
        threading.Thread(target=_read, args=(proc.stderr, "stderr"), daemon=True).start()

        deadline = time.time() + claude_args["timeout"]
        open_readers = 2
        timed_out = False
        while open_readers > 0:
            remaining = deadline - time.time()
            if remaining <= 0:
                timed_out = True
                break
            try:
                tag, line = lines.get(timeout=min(5, max(remaining, 0.1)))
            except queue.Empty:
                continue
            if line is None:
                open_readers -= 1
                continue
            if tag == "stdout":
                _write_stream_event(fout, line)
            else:
                fout.write(f"[claude stderr] {line}")
                fout.flush()

        if timed_out:
            proc.kill()
            fout.write(f"\n===== Error ===== Timed out after {claude_args['timeout']} seconds, process killed.\n")
        else:
            proc.wait()
            if proc.returncode != 0:
                fout.write(f"\n===== Error ===== claude exited with code {proc.returncode}\n")
        fout.flush()

def run_agent(insight_dict, output_dir, q_type, claude_args):

    data_part_prompt = ""
    data_path2desc = insight_dict['data']
    for path, desc in data_path2desc.items():
        data_part_prompt += f"Path: {path}\nDescription: {desc}\n\n"

    data_part_prompt = data_part_prompt.strip()

    if q_type == 'oe':
        qs_dict = parse_oe_questions(insight_dict[f'{q_type}_question'])
    else:
        qs_dict = parse_mcq_question(insight_dict[f'{q_type}_question'])

    if len(qs_dict) == 0:
        print(f"No {q_type} questions found, skipping.")
        return
    for q_id, q_dict in qs_dict.items():
        output_path = os.path.join(output_dir, f"{q_id}_output.txt")
        if os.path.exists(output_path):
            print(f"Output {output_path} already exists, skipping.")
            continue

        if q_type == 'oe':
            curr_prompt = oe_initial_prompt.format(
                data_path=data_part_prompt,
                question=q_dict['question'],
            )
        else:
            curr_prompt = mcq_initial_prompt.format(
                data_path=data_part_prompt,
                question=q_dict['question'],
                answer_choices='\n'.join([f"{k}) {v}" for k, v in q_dict['options'].items()])
            )

        # save current prompt to file
        with open(os.path.join(output_dir, f'{q_id}_{q_type}_prompt.txt'), 'w') as f:
            f.write(curr_prompt)

        print(f"Running Claude Code on {output_path} ...")
        run_claude_code(curr_prompt, output_path, claude_args)

def main(dataset_json_path, output_dir, q_type, claude_args):
    with open(dataset_json_path, 'r') as f:
        dataset_dict = json.load(f)


    for p_id, p_dict in dataset_dict.items():
        print(f"Processing paper: {p_id}")
        p_dir = os.path.join(output_dir, p_id)

        for i_id, i_dict in p_dict.items():
            try:
                i_dir = os.path.join(p_dir, i_id)
                os.makedirs(i_dir, exist_ok=True)
                if f"{q_type}_question" in i_dict:
                    run_agent(i_dict, i_dir, q_type, claude_args)
                else:
                    print(f"No {q_type} questions found for {p_id} - {i_id}, skipping.")
            except Exception as e:
                print(f"Error processing {p_id} - {i_id}: {e}")
                import traceback
                traceback.print_exc()

if __name__ == "__main__":

    # store all output into a output_file named output.txt
    parser = argparse.ArgumentParser(description='Run local Claude Code agent on datasets.')
    parser.add_argument('--dataset_json', type=str, required=True, help='Path to the dataset JSON file.')
    parser.add_argument('--output_dir', type=str, required=False, help='Path to save the output.', default='./output')
    parser.add_argument('--q_type', type=str, required=True, help='Question type: mcq or oe.')
    parser.add_argument('--claude_path', type=str, required=False, help='Path to the claude CLI binary.', default='claude')
    parser.add_argument('--claude_model', type=str, required=False, help='Claude Code model to use (e.g. sonnet, opus). Defaults to the CLI default model.', default=None)
    parser.add_argument('--workdir', type=str, required=False, help='Working directory for the Claude Code agent (where relative data paths resolve). Defaults to the parent directory of the dataset JSON folder.', default=None)
    parser.add_argument('--permission_mode', type=str, required=False, help='Claude Code permission mode.', default='bypassPermissions', choices=['default', 'acceptEdits', 'plan', 'bypassPermissions'])
    parser.add_argument('--max_turns', type=int, required=False, help='Max agentic turns per question for Claude Code.', default=50)
    parser.add_argument('--timeout', type=int, required=False, help='Timeout in seconds per question.', default=1800)
    args = parser.parse_args()

    if args.workdir is None:
        # dataset lives in <bench_root>/benchmark/<file>.json, so relative data paths
        # (e.g. benchmark/scdata/...) resolve from <bench_root>
        args.workdir = os.path.dirname(os.path.dirname(os.path.abspath(args.dataset_json)))

    model_dir = args.claude_model.replace('/', '_') if args.claude_model else 'default'
    main(args.dataset_json, os.path.join(args.output_dir, f'claude_code_{model_dir}', args.q_type), args.q_type, claude_args={
        "claude_path": args.claude_path,
        "model": args.claude_model,
        "workdir": args.workdir,
        "permission_mode": args.permission_mode,
        "max_turns": args.max_turns,
        "timeout": args.timeout,
    })
