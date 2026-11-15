"""Provider-agnostic LLM access with tiered routing, on-disk caching and usage accounting.

Routing rule (the efficiency story):
  cheap  tier -> bulk extraction, one-liners, per-section summaries
  strong tier -> only where reasoning is the product: narrative-vs-numbers, bull/bear
Retrieval keeps every prompt to a handful of passages; identical prompts are served from disk.
Zero API key => the app still runs in extractive (retrieval-only) mode.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib

import requests

CACHE = pathlib.Path(__file__).resolve().parent.parent / "data" / "cache" / "llm"

STATS = {"calls": 0, "cache_hits": 0, "tokens_in": 0, "tokens_out": 0, "by_tier": {}}


class LLMUnavailable(RuntimeError):
    pass


def _secret(name: str):
    v = os.environ.get(name)
    if v:
        return v
    try:
        import streamlit as st
        return st.secrets.get(name)
    except Exception:  # noqa: BLE001 - no secrets file configured
        return None


def provider():
    if _secret("ANTHROPIC_API_KEY"):
        return "anthropic"
    if _secret("GEMINI_API_KEY"):
        return "gemini"
    return None


def models():
    if provider() == "anthropic":
        return {"cheap": _secret("FINN_MODEL_CHEAP") or "claude-haiku-5-5",
                "strong": _secret("FINN_MODEL_STRONG") or "claude-sonnet-5-5"}
    return {"cheap": _secret("FINN_MODEL_CHEAP") or "gemini-2.5-flash",
            "strong": _secret("FINN_MODEL_STRONG") or "gemini-2.5-pro"}


def available() -> bool:
    return provider() is not None


def complete(prompt: str, system: str = "", tier: str = "cheap", max_tokens: int = 700) -> str:
    prov = provider()
    if prov is None:
        raise LLMUnavailable("no API key configured")
    model = models()[tier]
    key = hashlib.sha256(json.dumps([prov, model, system, prompt, max_tokens]).encode()).hexdigest()
    path = CACHE / f"{key}.json"
    if path.exists():
        STATS["cache_hits"] += 1
        return json.loads(path.read_text())["text"]

    if prov == "anthropic":
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": _secret("ANTHROPIC_API_KEY"), "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": model, "max_tokens": max_tokens, "temperature": 0, "system": system,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=120)
        r.raise_for_status()
        j = r.json()
        text = "".join(b.get("text", "") for b in j.get("content", []))
        tin, tout = j.get("usage", {}).get("input_tokens", 0), j.get("usage", {}).get("output_tokens", 0)
    else:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": _secret("GEMINI_API_KEY"), "content-type": "application/json"},
            json={"systemInstruction": {"parts": [{"text": system}]},
                  "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "generationConfig": {"temperature": 0, "maxOutputTokens": max_tokens}},
            timeout=120)
        r.raise_for_status()
        j = r.json()
        parts = j["candidates"][0]["content"].get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        um = j.get("usageMetadata", {})
        tin, tout = um.get("promptTokenCount", 0), um.get("candidatesTokenCount", 0)

    STATS["calls"] += 1
    STATS["tokens_in"] += tin
    STATS["tokens_out"] += tout
    t = STATS["by_tier"].setdefault(tier, {"calls": 0, "in": 0, "out": 0})
    t["calls"] += 1; t["in"] += tin; t["out"] += tout
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"text": text}))
    except Exception:  # noqa: BLE001
        pass
    return text
