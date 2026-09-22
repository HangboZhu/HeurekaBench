import os
import shutil
import subprocess


def query_claude_code(prompt, timeout=1200, max_turns=2):
    """Run one generation prompt through the local Claude Code CLI in headless mode.

    Mirrors the agent-side integration in run_biomni/run_biomni.py: the prompt is
    passed via stdin and the final answer is returned as plain text, so this can
    replace the openai/anthropic API calls in the benchmark-creation pipeline
    (model_call="claude_code"). Diagnostics go to stderr, stdout carries only the
    answer. NO_PROXY handling keeps gateway traffic (ANTHROPIC_BASE_URL) working
    when a system http_proxy is set.
    """
    claude_path = shutil.which("claude") or "claude"
    # --tools "": the CLI is an agent, and every caller here wants raw
    # generation. Left with tools, it sometimes answers a "return strict JSON"
    # prompt by *writing a file* instead of replying, and stdout then carries
    # the tool call ("[Tool call: Write]\n{\"file_path\":...,\"content\":\"...\"}")
    # rather than the answer (observed 2026-09-17 on the MCQ regeneration).
    cmd = [claude_path, "-p", "--output-format", "text", "--max-turns", str(max_turns), "--tools", ""]
    model = os.getenv("CLAUDE_CODE_MODEL")
    if model:
        cmd += ["--model", model]

    env = os.environ.copy()
    # Route the CLI's API traffic directly: a flaky system proxy (http_proxy
    # et al.) breaks the gateway connection with ECONNRESET — same rationale
    # as trust_env=False in utils/llm_gateway.py.
    for var in ("http_proxy", "https_proxy", "all_proxy",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env.pop(var, None)
    no_proxy = env.get("NO_PROXY", env.get("no_proxy", ""))
    env["NO_PROXY"] = env["no_proxy"] = ",".join(p for p in ["127.0.0.1", "localhost", no_proxy] if p)

    # The gateway fails intermittently (non-zero exit / empty output), so retry once
    for attempt in (1, 2):
        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            print(f"Claude Code call timed out after {timeout} seconds (attempt {attempt}/2).")
            continue

        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()

        print(
            f"Claude Code attempt {attempt}/2 failed "
            f"(exit {result.returncode}): {result.stderr[:300]}"
        )

    return ""
