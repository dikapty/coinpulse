"""Thin LLM client: Google Gemini (primary) and Groq (fallback), free tiers only."""
import json
import os
import re
import time

import requests

from common import load_config, log


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
    """Configured model first, then fallbacks (free tiers restrict some models)."""
    configured = _resolved_model["name"] or cfg["gemini"]["model"]
    fallbacks = cfg["gemini"].get("fallback_models", []) or []
    return [configured] + [m for m in fallbacks if m != configured]


def _gemini(prompt: str, cfg: dict) -> str:
    key = _require_key(cfg["gemini"]["api_key_env"])
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    last_err = None
    for model in _model_chain(cfg):
        body = {
            "model": model,
            "input": prompt,
            "store": False,
            # Reasoning models think long by default; news writing needs speed/quota economy.
            "generation_config": {"thinking_level": "minimal"},
        }
        r = requests.post(GEMINI_URL, headers=headers, json=body, timeout=180)
        if r.status_code == 429:
            # Rate limits are per-project, not per-model: wait once (honor Retry-After
            # if present), retry SAME model, then fail fast — no chain walk on quota.
            try:
                wait = min(int(float(r.headers.get("Retry-After", "70"))), 120)
            except ValueError:
                wait = 70
            wait = max(wait, 5)
            log(f"Gemini {model}: 429 rate-limited ({r.text[:150]!r}) — waiting {wait}s, one retry")
            time.sleep(wait)
            r = requests.post(GEMINI_URL, headers=headers, json=body, timeout=180)
            if r.status_code == 429:
                raise RuntimeError(f"GEMINI_QUOTA: {model} still 429 after wait: {r.text[:200]}")
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
            # remember the model that actually works on this key (quota is precious)
            log(f"Gemini: '{model}' works on this key — remembering for subsequent calls")
            _resolved_model["name"] = model
        return text
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
    order = ["gemini", "groq"] if cfg.get("provider", "gemini") == "gemini" else ["groq", "gemini"]
    last_err = None
    for provider in order:
        fn = _gemini if provider == "gemini" else _groq
        for attempt in range(1, retries + 1):
            try:
                return fn(prompt, cfg)
            except RuntimeError as e:  # quota — switch provider immediately
                log(f"LLM {provider}: {e}")
                last_err = e
                break
            except Exception as e:  # network/5xx — retry with backoff
                last_err = e
                log(f"LLM {provider} attempt {attempt} failed: {e}")
                time.sleep(2 ** attempt)
    raise RuntimeError(f"All LLM providers failed: {last_err}")


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
