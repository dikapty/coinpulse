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

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
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


def _gemini(prompt: str, cfg: dict) -> str:
    key = _require_key(cfg["gemini"]["api_key_env"])
    model = _resolved_model["name"] or cfg["gemini"]["model"]
    url = GEMINI_URL.format(model=model)
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": cfg.get("temperature", 0.4),
            "maxOutputTokens": cfg.get("max_tokens", 3000),
        },
    }
    r = requests.post(url, params={"key": key}, json=body, timeout=120)
    if r.status_code == 429:
        raise RuntimeError("GEMINI_QUOTA: free daily limit reached")
    if r.status_code == 404 and _resolved_model["name"] is None:
        _resolved_model["name"] = _discover_gemini_model(key, cfg)
        return _gemini(prompt, cfg)  # retry once with the discovered model
    r.raise_for_status()
    data = r.json()
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()


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
