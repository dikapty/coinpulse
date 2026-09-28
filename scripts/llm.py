"""Thin LLM client: Google Gemini (primary) and Groq (fallback), free tiers only."""
import json
import os
import re
import time

import requests

from common import load_config, log, load_state, save_state


def _require_key(env_var: str) -> str:
    val = os.environ.get(env_var, "").strip()
    if not val:
        raise RuntimeError(f"MISSING_KEY: {env_var} is empty (set it in .env or GitHub Secrets)")
    return val

# Interactions API (GA June 2026) — the current endpoint for new Gemini models.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
# Legacy generateContent — kept as fallback for older model names.
GEMINI_LEGACY_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Free-tier daily request budget. Gemini free = 20 req/day per model on fresh keys;
# keep a safety margin. Each article costs ~3 calls (draft + self-edit + gate score).
# Override via config llm.daily_budget. When exhausted, complete() fails fast with
# BudgetExhaustedError so the pipeline stops cleanly instead of wasting quota.
DEFAULT_DAILY_BUDGET = 18


class BudgetExhaustedError(RuntimeError):
    pass


def _budget_state() -> dict:
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    st = load_state("llm_budget", {"day": day, "used": 0})
    if st.get("day") != day:  # new UTC day -> reset
        st = {"day": day, "used": 0}
    return st


def _budget_limit() -> int:
    try:
        return int(load_config()["llm"].get("daily_budget", DEFAULT_DAILY_BUDGET))
    except Exception:
        return DEFAULT_DAILY_BUDGET


def budget_remaining() -> int:
    return max(0, _budget_limit() - _budget_state()["used"])


def _budget_consume() -> None:
    st = _budget_state()
    st["used"] += 1
    save_state("llm_budget", st)


def _budget_check() -> None:
    if budget_remaining() <= 0:
        st = _budget_state()
        raise BudgetExhaustedError(
            f"LLM daily budget exhausted ({st['used']}/{_budget_limit()} calls today); "
            "resumes next UTC day (Gemini free tier = 20 req/day per model)")


_resolved_model = {"name": None}


def _discover_gemini_model(key: str, cfg: dict) -> str:
    """If the configured model 404s, list available models and pick a flash one."""
    if _resolved_model["name"]:
        return _resolved_model["name"]
    configured = cfg["gemini"]["model"]
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         params={"key": key}, timeout=30)
        r.raise_for_status()
        models = [m["name"].split("/")[-1] for m in r.json().get("models", [])
                  if "generateContent" in m.get("supportedGenerationMethods", [])]
        pick = configured if configured in models else next(
            (m for m in models if "flash" in m), configured)
        log(f"Gemini model resolved: {pick} (configured: {configured})")
        _resolved_model["name"] = pick
        return pick
    except Exception as e:
        log(f"Gemini model discovery failed ({e}) — using configured '{configured}'")
        _resolved_model["name"] = configured
        return configured


def _extract_interaction_text(data) -> str:
    """Extract reply text from an Interactions API response.

    Canonical shape: {"status": "completed", "steps": [
        {"type": "model_output", "content": [{"type": "text", "text": "..."}]}]}
    Tolerates string parts, string content, and dict/part variants.
    """
    if not isinstance(data, dict):
        raise RuntimeError(f"GEMINI_PARSE: unexpected response type {type(data).__name__}: {str(data)[:300]}")
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"].strip()
    texts = []
    for step in data.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("type") not in (None, "model_output"):
            continue
        content = step.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    texts.append(part["text"])
                elif isinstance(part, str):
                    texts.append(part)
        elif isinstance(content, dict):
            for part in content.get("parts") or []:
                if isinstance(part, dict) and part.get("text"):
                    texts.append(part["text"])
    out = "\n".join(t for t in texts if t).strip()
    if out:
        return out
    status = data.get("status")
    if status and status != "completed":
        raise RuntimeError(f"GEMINI_INCOMPLETE: interaction status={status}, id={data.get('id')}")
    raise RuntimeError(f"GEMINI_PARSE: no text in response; head={json.dumps(data)[:400]}")


def _model_chain(cfg: dict) -> list:
    """Configured model first, then fallbacks. Free-tier quota is PER MODEL
    (20 req/day each), so the chain is a real quota pool: on 429 we move on."""
    configured = _resolved_model["name"] or cfg["gemini"]["model"]
    fallbacks = cfg["gemini"].get("fallback_models", []) or []
    chain = [configured] + [m for m in fallbacks if m != configured]
    # models already quota-exhausted today go last (still tried only if all else fails)
    exhausted = set(_exhausted_models())
    return sorted(chain, key=lambda m: m in exhausted)


def _exhausted_models() -> list:
    st = _budget_state()
    return list(st.get("exhausted", []))


def _mark_model_exhausted(model: str) -> None:
    st = _budget_state()
    ex = set(st.get("exhausted", []))
    ex.add(model)
    st["exhausted"] = sorted(ex)
    save_state("llm_budget", st)


def _gemini(prompt: str, cfg: dict) -> str:
    key = _require_key(cfg["gemini"]["api_key_env"])
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    chain = _model_chain(cfg)
    last_err = None
    quota_hits = 0
    for model in chain:
        body = {
            "model": model,
            "input": prompt,
            "store": False,
            # Reasoning models think long by default; news writing needs speed/quota economy.
            "generation_config": {"thinking_level": "minimal"},
        }
        r = requests.post(GEMINI_URL, headers=headers, json=body, timeout=180)
        if r.status_code == 429:
            # Per-model daily quota hit. Mark it, try the next model in the chain.
            quota_hits += 1
            _mark_model_exhausted(model)
            last_err = RuntimeError(f"GEMINI_QUOTA {model}: {r.text[:150]}")
            log(f"Gemini {model}: daily quota exhausted -> next model")
            continue
        if r.status_code in (400, 403, 404):
            last_err = RuntimeError(f"GEMINI_MODEL {model} -> {r.status_code}: {r.text[:200]}")
            log(f"Gemini model {model} refused ({r.status_code}) -> next model")
            continue
        if r.status_code >= 500:
            last_err = RuntimeError(f"GEMINI_5XX {model}: {r.status_code} {r.text[:150]}")
            log(f"Gemini {model}: server error {r.status_code} — retry once after 15s")
            time.sleep(15)
            r = requests.post(GEMINI_URL, headers=headers, json=body, timeout=180)
            if r.status_code >= 500:
                log(f"Gemini {model}: still {r.status_code} -> next model")
                continue
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):  # observed variant: top-level JSON array
            dicts = [d for d in data if isinstance(d, dict)]
            data = dicts[-1] if dicts else {}
        text = _extract_interaction_text(data)
        if _resolved_model["name"] != model:
            log(f"Gemini: '{model}' works on this key — using it for subsequent calls")
            _resolved_model["name"] = model
        return text
    if quota_hits and quota_hits == len(chain):
        raise BudgetExhaustedError(
            "Gemini free-tier daily quota exhausted on ALL models "
            f"({', '.join(chain)}). Resumes next UTC day.")
    raise last_err or RuntimeError("GEMINI: all models exhausted")


def _groq(prompt: str, cfg: dict) -> str:
    gcfg = cfg["groq"]
    key = _require_key(gcfg["api_key_env"])
    body = {
        "model": gcfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": cfg.get("temperature", 0.4),
        "max_tokens": cfg.get("max_tokens", 3000),
    }
    r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=120)
    if r.status_code == 429:
        raise RuntimeError("GROQ_QUOTA: rate/quota limit reached")
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def complete(prompt: str, retries: int = 3) -> str:
    """Generate text using the configured provider, falling back to the other on quota errors."""
    cfg = load_config()["llm"]
    _budget_check()
    order = ["gemini", "groq"] if cfg.get("provider", "gemini") == "gemini" else ["groq", "gemini"]
    last_err = None
    for provider in order:
        fn = _gemini if provider == "gemini" else _groq
        for attempt in range(1, retries + 1):
            try:
                text = fn(prompt, cfg)
                _budget_consume()
                return text
            except BudgetExhaustedError:
                raise  # all Gemini models quota-exhausted today -> stop pipeline stage
            except RuntimeError as e:  # quota — switch provider immediately
                log(f"LLM {provider}: {e}")
                last_err = e
                break
            except Exception as e:  # network/5xx — retry with backoff
                last_err = e
                log(f"LLM {provider} attempt {attempt} failed: {e}")
                time.sleep(2 ** attempt)
    raise RuntimeError(f"All LLM providers failed: {last_err}")


def _budget_exhausted_flag() -> None:
    """Kept for compatibility; per-model 429s are tracked via _mark_model_exhausted."""
    st = _budget_state()
    st["used"] = max(st["used"], _budget_limit())
    save_state("llm_budget", st)


def extract_json(text: str):
    """Pull the first JSON object/array out of an LLM reply (tolerates code fences)."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"No valid JSON in LLM reply: {text[:300]}")
