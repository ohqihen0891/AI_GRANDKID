"""Wrapper for any OpenAI-compatible chat API (OpenAI, Tencent Hunyuan, DeepSeek, Qwen...)."""
import json
import re

from .config import S

_client = None


def is_mock() -> bool:
    return S.LLM_PROVIDER.lower() == "mock" or not S.LLM_API_KEY


def client():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(base_url=S.LLM_BASE_URL, api_key=S.LLM_API_KEY, timeout=90)
    return _client


def chat(messages, model=None, temperature=0.7, max_tokens=900) -> str:
    r = client().chat.completions.create(
        model=model or S.LLM_MODEL, messages=messages,
        temperature=temperature, max_tokens=max_tokens)
    return (r.choices[0].message.content or "").strip()


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("No JSON found in model output: " + text[:200])
    return json.loads(m.group(0))


def chat_json(messages, model=None) -> dict:
    out = chat(messages, model=model, temperature=0.2, max_tokens=1500)
    try:
        return parse_json(out)
    except Exception:
        retry = messages + [
            {"role": "assistant", "content": out},
            {"role": "user", "content": "Return ONLY the valid JSON object, nothing else."}]
        return parse_json(chat(retry, model=model, temperature=0, max_tokens=1500))
