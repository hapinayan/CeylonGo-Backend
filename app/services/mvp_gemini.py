import os
import re

import httpx
from fastapi import HTTPException


DEFAULT_MODEL = "gemini-3.5-flash-lite"

_LOCAL_VALIDATION_KEYWORDS = {
    "title", "default", "format", "pattern", "minLength", "maxLength",
    "minItems", "maxItems", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
}


def _generation_schema(schema):
    # Gemini can reject complex constrained schemas even when each keyword is supported.
    # Pydantic still validates the full contract after generation.
    result = {}
    for key, value in schema.items():
        if key in _LOCAL_VALIDATION_KEYWORDS:
            continue
        if key in {"properties", "$defs"}:
            value = {name: _generation_schema(child) for name, child in value.items()}
        elif key in {"anyOf", "oneOf", "allOf", "prefixItems"}:
            value = [_generation_schema(child) for child in value]
        elif key == "items" and isinstance(value, dict):
            value = _generation_schema(value)
        result[key] = value
    return result


def _invalid_api_key(response):
    if response.status_code != 400:
        return False
    try:
        details = response.json()["error"]["details"]
    except (KeyError, TypeError, ValueError):
        return False
    return isinstance(details, list) and any(
        isinstance(item, dict) and item.get("reason") == "API_KEY_INVALID"
        for item in details
    )


def generate(contents, system, *, tools=None, schema=None):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip()
    if not key or not re.fullmatch(r"gemini-[a-z0-9.-]{1,70}", model):
        raise HTTPException(503, "Gemini is not configured")
    try:
        timeout = float(os.getenv("GEMINI_TIMEOUT_SECONDS", "12"))
        if not 1 <= timeout <= 20:
            raise ValueError
    except ValueError:
        raise HTTPException(503, "Gemini timeout configuration is invalid") from None
    config = {"maxOutputTokens": 4096}
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": config,
    }
    if tools:
        body["tools"] = [{"functionDeclarations": tools}]
    if schema:
        config.update(
            responseMimeType="application/json", responseJsonSchema=_generation_schema(schema),
        )
    try:
        response = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={"x-goog-api-key": key}, json=body,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 5)),
        )
    except httpx.TimeoutException:
        raise HTTPException(504, "Gemini timed out") from None
    except httpx.RequestError:
        raise HTTPException(502, "Gemini could not be reached") from None
    if response.status_code == 429:
        raise HTTPException(429, "Gemini rate limit reached", headers={"Retry-After": "30"})
    if response.status_code in {401, 403, 404} or _invalid_api_key(response):
        raise HTTPException(503, "The configured Gemini model or credentials are unavailable")
    if response.is_error:
        raise HTTPException(502, "Gemini could not complete this request")
    try:
        choice = response.json()["candidates"][0]
        content = choice["content"]
        parts = content["parts"]
        if (choice.get("finishReason") not in (None, "STOP")
                or not isinstance(parts, list) or not parts
                or not all(isinstance(part, dict) for part in parts)
                or any(not isinstance(part["functionCall"], dict)
                       for part in parts if "functionCall" in part)):
            raise ValueError
        return content
    except (KeyError, TypeError, ValueError, IndexError):
        raise HTTPException(502, "Gemini returned no usable response") from None
