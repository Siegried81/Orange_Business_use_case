"""
Provider-agnostic LLM client. Behavior is controlled entirely by .env, so
switching machines (or switching provider) never requires touching this
file -- only .env.

Sieg 25/8 -- added Cerebras and SambaNova as 2 more fallback steps between
Groq and Ollama, per current_project_state_overview.md's rate-limit
section ("the smartest approach is a fallback chain ... Groq (1,000
req/day) -> Cerebras (14,400 req/day) -> SambaNova (unlimited, slower)").
Both are OpenAI-API-compatible chat-completions endpoints, called with
`requests` (already a pipeline dependency, via ingest.py) rather than
adding a new SDK package for each -- same reasoning as `_call_ollama()`
below. "auto" mode's chain is now Groq -> Cerebras -> SambaNova -> Ollama:
the 3 cloud providers in the doc's own order, with Ollama (free, local,
never rate-limited, but slower and less reliable on strict JSON) kept as
the final safety net, same role it already had before this change.
LLM_PROVIDER can also be set to "cerebras" or "sambanova" directly, same
pattern as the existing "groq"/"ollama" direct modes -- useful for testing
one provider in isolation.

.env keys:
    LLM_PROVIDER      "auto" (default, full Groq->Cerebras->SambaNova->Ollama
                       chain) | "groq" | "cerebras" | "sambanova" | "ollama"
    GROQ_API_KEY      required if LLM_PROVIDER is "auto" or "groq"
    GROQ_MODEL        default: openai/gpt-oss-120b
    CEREBRAS_API_KEY  optional -- required if LLM_PROVIDER is "cerebras", or
                       to have "auto" actually reach this step (free tier
                       key: https://cloud.cerebras.ai)
    CEREBRAS_MODEL    default: gpt-oss-120b -- verified against this project's
                       actual key (25/8: `curl .../v1/models` confirmed
                       gpt-oss-120b and gemma-4-31b as the only 2 models
                       available -- the original default here, llama3.1-8b,
                       404'd; per-account model access varies, re-check with
                       the same curl command if this default 404s for you too
    SAMBANOVA_API_KEY optional -- required if LLM_PROVIDER is "sambanova", or
                       to have "auto" actually reach this step (free tier
                       key: https://cloud.sambanova.ai)
    SAMBANOVA_MODEL   default: DeepSeek-V3.1 -- check cloud.sambanova.ai for
                       the current model list (25/8: the original default
                       here, Meta-Llama-3.1-8B-Instruct, already came back
                       410 Gone the same day -- this catalog moves fast)
    OLLAMA_URL        default: http://localhost:11434/api/generate
    OLLAMA_MODEL      default: llama3.2:3b

Setup:
    Groq:      export GROQ_API_KEY="your-key-here"   (free tier: https://console.groq.com)
               pip install groq
    Cerebras:  export CEREBRAS_API_KEY="your-key-here"  (free tier: https://cloud.cerebras.ai)
    SambaNova: export SAMBANOVA_API_KEY="your-key-here" (free tier: https://cloud.sambanova.ai)
    Ollama:    install from https://ollama.com, then in a terminal:
                   ollama pull llama3.2:3b

Any of these can simply be left unset -- an empty key means that step of
the "auto" chain is skipped (see get_llm_response()), it does not error.
"""

import os
import json
from dotenv import load_dotenv

load_dotenv()  # reads .env in the repo root and sets os.environ -- run this once at import time

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "auto").lower()  # "auto" | "groq" | "cerebras" | "sambanova" | "ollama"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")  # llama-3.3-70b-versatile was deprecated by Groq (Aug 2026)
CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b")
SAMBANOVA_MODEL = os.environ.get("SAMBANOVA_MODEL", "DeepSeek-V3.1")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")

CEREBRAS_API_KEY = os.environ.get("CEREBRAS_API_KEY", "")
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"
SAMBANOVA_API_KEY = os.environ.get("SAMBANOVA_API_KEY", "")
SAMBANOVA_URL = "https://api.sambanova.ai/v1/chat/completions"

# Sieg 23/08 -- list of available Groq keys, in try-order. Empty/unset
# GROQ_API_KEY_2 is simply skipped, so this is safe with only 1 key too.
# Sieg 25/08 -- rotation re-enabled: single-key mode was causing an
# immediate 429 -> Ollama fallback instead of trying the other 3 keys
# that are actually set in .env.
GROQ_KEYS = [k for k in (
     os.environ.get("GROQ_API_KEY"),
     os.environ.get("GROQ_API_KEY_2"),
     os.environ.get("GROQ_API_KEY_3"),
     os.environ.get("GROQ_API_KEY_4"),
     os.environ.get("GROQ_API_KEY_5"),
 ) if k]

def _is_rate_limit_error(e):
    """Sieg 23/08 -- detects a 429/quota error specifically, so we only
    rotate keys for THAT reason (not for e.g. a malformed prompt, which
    would just fail the same way on the second key)."""
    msg = str(e).lower()
    return "429" in msg or "rate_limit" in msg or "quota" in msg


def _call_groq(prompt, system_prompt=None):
    from groq import Groq  # imported lazily so the package is only required if used

    if not GROQ_KEYS:
        raise RuntimeError("GROQ_API_KEY not set")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    # Sieg 23/08 -- try each key in turn; on a rate-limit error, move to the
    # next key instead of failing straight to Ollama. Any OTHER error (bad
    # key, network issue) still raises immediately -- no point retrying that
    # on a second key.
    # Sieg 25/08 -- rotation loop re-enabled (was commented out 24/08).
    last_error = None
    for i, api_key in enumerate(GROQ_KEYS):
        try:
            client = Groq(api_key=api_key)
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                temperature=0,
            )
            return response.choices[0].message.content
        except Exception as e:
            last_error = e
            if _is_rate_limit_error(e) and i < len(GROQ_KEYS) - 1:
                print(f"[llm_client] Groq key #{i + 1} rate-limited, trying key #{i + 2}")
                continue
            raise  # not a rate-limit error, or no more keys left -- propagate
    raise last_error


def _call_openai_compatible(url, api_key, model, prompt, system_prompt, provider_name):
    """Sieg 25/8 -- shared implementation for Cerebras and SambaNova: both
    expose the same OpenAI-style /v1/chat/completions shape (Bearer auth,
    {"model", "messages"} body, response.choices[0].message.content), so
    one function serves both instead of duplicating the request/response
    handling twice. Raises RuntimeError immediately if the key isn't set --
    same "don't even try" pattern as _call_groq()'s GROQ_KEYS check -- and
    raises for any HTTP error via raise_for_status(), same as _call_ollama()."""
    import requests  # already a dependency of pipeline/ingest.py

    if not api_key:
        raise RuntimeError(f"{provider_name.upper()}_API_KEY not set")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": 0},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _call_cerebras(prompt, system_prompt=None):
    """Sieg 25/8 -- Provider 2 in the fallback chain (14,400 req/day free
    tier vs Groq's 1,000, per current_project_state_overview.md)."""
    return _call_openai_compatible(
        CEREBRAS_URL, CEREBRAS_API_KEY, CEREBRAS_MODEL, prompt, system_prompt, "cerebras"
    )


def _call_sambanova(prompt, system_prompt=None):
    """Sieg 25/8 -- Provider 3, final CLOUD fallback (unlimited but slower,
    per current_project_state_overview.md) -- Ollama remains the true final
    fallback below it, since it needs no API key/network access at all."""
    return _call_openai_compatible(
        SAMBANOVA_URL, SAMBANOVA_API_KEY, SAMBANOVA_MODEL, prompt, system_prompt, "sambanova"
    )


def _call_ollama(prompt, system_prompt=None):
    import requests  # already a dependency of pipeline/ingest.py

    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": full_prompt, "stream": False},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["response"]


def get_llm_response(prompt, system_prompt=None):
    """
    Provider selection driven by LLM_PROVIDER in .env:
      - "ollama":    call Ollama only, never touch anything else.
      - "groq":      call Groq only, no fallback (fails loudly if unavailable).
      - "cerebras":  call Cerebras only, no fallback.
      - "sambanova": call SambaNova only, no fallback.
      - "auto" (default): try Groq -> Cerebras -> SambaNova -> Ollama, in
        that order, falling through to the next on ANY failure (quota,
        network, auth, missing key) -- same "any exception moves to the
        next step" behavior the original Groq->Ollama chain already had,
        just with 2 more cloud steps in between. A provider with no API
        key set (empty string) fails its own "not set" check immediately
        and falls through just as fast as a real network error would --
        you don't need to unset LLM_PROVIDER or edit code to skip a
        provider you haven't signed up for, just leave its key blank.
    """
    if LLM_PROVIDER == "ollama":
        return _call_ollama(prompt, system_prompt)
    if LLM_PROVIDER == "groq":
        return _call_groq(prompt, system_prompt)
    if LLM_PROVIDER == "cerebras":
        return _call_cerebras(prompt, system_prompt)
    if LLM_PROVIDER == "sambanova":
        return _call_sambanova(prompt, system_prompt)

    # "auto" -- Sieg 25/8: was Groq -> Ollama (try/except), now a 4-step
    # chain. Each step is independent: a failure at any point (including
    # "key not set") just moves to the next provider and prints why, so a
    # partially-configured .env (e.g. only GROQ_API_KEY and OLLAMA set,
    # nothing for Cerebras/SambaNova) degrades gracefully to the old
    # 2-step behavior instead of erroring on the steps that aren't set up.
    chain = [
        ("Groq", _call_groq),
        ("Cerebras", _call_cerebras),
        ("SambaNova", _call_sambanova),
        ("Ollama", _call_ollama),
    ]
    last_error = None
    for i, (name, call_fn) in enumerate(chain):
        try:
            return call_fn(prompt, system_prompt)
        except Exception as e:
            last_error = e
            is_last = i == len(chain) - 1
            if not is_last:
                print(f"[llm_client] {name} failed ({e}), falling back to {chain[i + 1][0]}")
    # Every provider in the chain failed -- propagate the LAST error (Ollama's,
    # usually "connection refused" if it isn't installed/running) so the
    # caller's own error message is meaningful rather than reporting Groq's
    # long-since-superseded failure.
    raise last_error


def get_llm_json(prompt, system_prompt=None):
    """
    Same as get_llm_response, but strips markdown code fences and parses JSON.
    Use this whenever you need a structured {"score": ..., "justification": ...}
    output -- e.g. for scoring/scoring.py's evidence_quality and
    strategic_relevance functions.
    Returns None if parsing fails (caller should handle that case).
    """
    try:
        raw = get_llm_response(prompt, system_prompt)
    except Exception as e:
        print(f"[llm_client] No LLM reachable ({e}) -- check GROQ_API_KEY or Ollama")
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        return json.loads(cleaned.strip())
    except json.JSONDecodeError:
        print(f"[llm_client] Could not parse JSON from response: {raw[:200]}")
        return None


if __name__ == "__main__":
    # Quick manual test: python -m llm.llm_client
    print(f"[llm_client] provider={LLM_PROVIDER}  groq_model={GROQ_MODEL}  ollama_model={OLLAMA_MODEL}")
    result = get_llm_response("Reply with exactly the word: OK")
    print("LLM responded:", result)