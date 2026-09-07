"""Shared client for the OpenAI-compatible gateway configured in .env.

All model names come from configuration (MODEL_NAME, or the per-role env
vars documented in .env.example); no model is hardcoded here.

Credentials are tried in fallback order: OPENAI_KEY (or OPENAI_API_KEY)
first, then CLAUDE_KEY — the keys may belong to different account groups
on the gateway, each serving a different model set. Once a key is seen
serving a model, it is preferred for that model on subsequent calls, so
the fallback latency is only paid once per model per run.

Uses the same gateway conventions as evaluate_agent_answer.py: BASE_URL,
with system http_proxy vars bypassed via trust_env=False.
"""

import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

_clients = {}
_preferred_key = {}  # model -> env var name of the key that last served it


def _base_url():
    base_url = os.getenv("BASE_URL")
    if not base_url:
        raise SystemExit("ERROR: gateway backend requires BASE_URL in .env.")
    # Normalize to the API root: strip a trailing /chat/completions, then
    # make sure the path ends with the /v1 prefix OpenAI-compatible
    # gateways serve under (a bare domain would hit the gateway's HTML UI).
    base_url = re.sub(r"/chat/completions/?$", "", base_url.rstrip("/"))
    if not re.search(r"/v\d+$", base_url):
        base_url += "/v1"
    return base_url


def _api_keys():
    """[(env_var, key)] in fallback order; duplicates removed."""
    sources = []
    for var in ("OPENAI_KEY", "OPENAI_API_KEY", "CLAUDE_KEY"):
        value = (os.getenv(var) or "").strip()
        if value and all(value != existing for _, existing in sources):
            sources.append((var, value))
    return sources


def _client_for(api_key):
    if api_key not in _clients:
        import httpx
        import openai

        _clients[api_key] = openai.OpenAI(
            api_key=api_key,
            base_url=_base_url(),
            http_client=httpx.Client(trust_env=False, timeout=600),
        )
    return _clients[api_key]


def client():
    """Client for the primary key (OPENAI_KEY); handy for models.list etc."""
    sources = _api_keys()
    if not sources:
        raise SystemExit("ERROR: gateway backend requires OPENAI_KEY (or OPENAI_API_KEY) in .env.")
    return _client_for(sources[0][1])


def model_pool():
    """Model names configured in MODEL_NAME (comma-separated)."""
    return [m.strip() for m in (os.getenv("MODEL_NAME") or "").split(",") if m.strip()]


# The gateway serves model families on different endpoints: the OpenAI-
# compatible one (OPENAI_KEY account group) for GPT-style models, and the
# Anthropic-compatible one (CLAUDE_KEY group) for claude and third-party
# models such as qwen/glm/MiniMax/deepseek. Routing by family avoids burning
# 503 retries on the wrong endpoint before the key fallback kicks in.
_OPENAI_FAMILY_PREFIXES = ("gpt", "codex", "o1", "o3", "o4")


def _is_openai_family(model):
    return model.lower().startswith(_OPENAI_FAMILY_PREFIXES)


def _claude_key():
    for var, api_key in _api_keys():
        if var == "CLAUDE_KEY":
            return api_key
    sources = _api_keys()
    return sources[-1][1] if sources else None


_anthropic_clients = {}


def _anthropic_client_for(api_key):
    if api_key not in _anthropic_clients:
        # anthropic>=1.2 vendors its HTTP layer as the `httpx2` package and
        # rejects a plain httpx.Client; trust_env=False bypasses the flaky
        # system proxy like the OpenAI-compatible client below.
        import anthropic
        import httpx2

        _anthropic_clients[api_key] = anthropic.Anthropic(
            base_url=os.getenv("BASE_URL", "").rstrip("/"),
            api_key=api_key,
            http_client=httpx2.Client(trust_env=False, timeout=600),
        )
    return _anthropic_clients[api_key]


def _chat_anthropic(prompt, model, temperature, max_tokens):
    """Anthropic-messages call for CLAUDE_KEY-group models (non-GPT families).

    temperature is intentionally not forwarded: this SDK generation and the
    gateway both reject it for newer models ("temperature is deprecated")."""
    api_key = _claude_key()
    if not api_key:
        raise RuntimeError("no CLAUDE_KEY configured for the anthropic endpoint")
    response = _anthropic_client_for(api_key).messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(getattr(block, "text", "") for block in response.content)
    if not text.strip():
        raise RuntimeError("empty response")
    return text


def chat(prompt, model, temperature=0.3, max_tokens=16384, json_output=False, attempts=3):
    """Call `model` on the gateway, trying keys in fallback order.

    Non-GPT families (claude/qwen/glm/MiniMax/deepseek/...) are served on the
    Anthropic-compatible endpoint (CLAUDE_KEY) and are tried there FIRST; the
    OpenAI-compatible key loop remains as the fallback. GPT-style models go
    straight to the OpenAI-compatible loop as before.

    Returns the response text, or "" if every route failed.
    """
    if not _is_openai_family(model):
        for attempt in range(1, attempts + 1):
            try:
                return _chat_anthropic(prompt, model, temperature, max_tokens)
            except Exception as error:  # noqa: BLE001 - report and retry
                print(f"Gateway anthropic-route call to '{model}' failed (attempt {attempt}/{attempts}): {error}")
                time.sleep(5)
        print(f"Falling back from the anthropic endpoint to the OpenAI-compatible loop for '{model}'.")

    sources = _api_keys()
    if not sources:
        raise SystemExit("ERROR: gateway backend requires OPENAI_KEY (or OPENAI_API_KEY) in .env.")
    order = list(sources)
    preferred = _preferred_key.get(model)
    if preferred:
        order.sort(key=lambda source: source[0] != preferred)

    last_error = None
    for var, api_key in order:
        kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_output:
            kwargs["response_format"] = {"type": "json_object"}

        for attempt in range(1, attempts + 1):
            try:
                response = _client_for(api_key).chat.completions.create(**kwargs)
                if not hasattr(response, "choices"):
                    # e.g. a 200 response carrying the gateway's HTML error page
                    raise RuntimeError(f"unexpected gateway response: {str(response)[:200]}")
                content = response.choices[0].message.content
                if content and content.strip():
                    _preferred_key[model] = var
                    return content
                last_error = RuntimeError("empty response")
            except Exception as error:  # noqa: BLE001 - report and retry whatever failed
                last_error = error
                # Some gateways reject response_format; retry once without it
                if json_output and "response_format" in kwargs:
                    kwargs.pop("response_format")
            print(f"Gateway call to '{model}' with {var} failed (attempt {attempt}/{attempts}): {last_error}")
            time.sleep(5)
        if len(order) > 1:
            print(f"Falling back from {var} to the next key for '{model}'.")
    return ""
