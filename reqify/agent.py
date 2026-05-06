from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen


DEFAULT_AGENT_PROMPT = """As an automotive requirments engineer, analyze this requirement statement and return as bullet points:
- Rating 1-5 and one sentece what to improve.
- Verification Hint: One sentece what to improve.

Guidance:
- "well_formed" considers clarity, singularity, unambiguity, measurable criteria, absence of design constraint unless intended.
"""


@dataclass(frozen=True)
class AgentRequest:
    user_prompt: str
    session_id: str | None = None
    object_id: str | None = None
    selected_object: dict[str, object] | None = None


class AgentBackend(Protocol):
    def analyze(self, system_prompt: str, request: AgentRequest) -> str:
        ...


class AgentBackendError(Exception):
    pass


class LocalAgentBackend:
    def analyze(self, system_prompt: str, request: AgentRequest) -> str:
        raise AgentBackendError(
            "No LLM backend is configured. Set REQIFY_AGENT_BACKEND to an implemented LLM backend before running analysis."
        )


class OpenAIResponsesBackend:
    def analyze(self, system_prompt: str, request: AgentRequest) -> str:
        api_key = env_first("REQIFY_OPENAI_API_KEY", "OPENAI_API_KEY")
        if not api_key:
            raise AgentBackendError("OpenAI API key is missing. Set OPENAI_API_KEY or REQIFY_OPENAI_API_KEY.")
        base_url = os.environ.get("REQIFY_OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        model = os.environ.get("REQIFY_OPENAI_MODEL", "gpt-5.2").strip()
        payload: dict[str, object] = {
            "model": model,
            "instructions": system_prompt.strip(),
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": compose_user_input(request)}],
                }
            ],
            "max_output_tokens": int_env("REQIFY_AGENT_MAX_OUTPUT_TOKENS", 1200),
        }
        temperature = optional_float_env("REQIFY_AGENT_TEMPERATURE")
        if temperature is not None:
            payload["temperature"] = temperature
        response = post_json(
            f"{base_url}/responses",
            payload,
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        return extract_openai_text(response)


class BedrockConverseBackend:
    def analyze(self, system_prompt: str, request: AgentRequest) -> str:
        region = env_first("REQIFY_BEDROCK_REGION", "REQIFY_AWS_REGION", "AWS_REGION", "AWS_DEFAULT_REGION")
        if not region:
            raise AgentBackendError("Amazon Bedrock region is missing. Set REQIFY_BEDROCK_REGION or AWS_REGION.")
        model_id = os.environ.get("REQIFY_BEDROCK_MODEL_ID", "").strip()
        if not model_id:
            raise AgentBackendError("Amazon Bedrock model ID is missing. Set REQIFY_BEDROCK_MODEL_ID.")
        access_key = env_first("REQIFY_AWS_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID")
        secret_key = env_first("REQIFY_AWS_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY")
        if not access_key or not secret_key:
            raise AgentBackendError(
                "Amazon Bedrock credentials are missing. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY."
            )
        session_token = env_first("REQIFY_AWS_SESSION_TOKEN", "AWS_SESSION_TOKEN")
        endpoint = os.environ.get("REQIFY_BEDROCK_ENDPOINT", f"https://bedrock-runtime.{region}.amazonaws.com").rstrip("/")
        body: dict[str, object] = {
            "system": [{"text": system_prompt.strip()}],
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": compose_user_input(request)}],
                }
            ],
            "inferenceConfig": {"maxTokens": int_env("REQIFY_AGENT_MAX_OUTPUT_TOKENS", 1200)},
        }
        temperature = optional_float_env("REQIFY_AGENT_TEMPERATURE")
        if temperature is not None:
            inference_config = body["inferenceConfig"]
            if isinstance(inference_config, dict):
                inference_config["temperature"] = temperature
        response = aws_post_json(
            f"{endpoint}/model/{quote(model_id, safe='')}/converse",
            body,
            region,
            "bedrock",
            access_key,
            secret_key,
            session_token,
        )
        return extract_bedrock_text(response)


class UnconfiguredAgentBackend:
    def __init__(self, backend_name: str):
        self.backend_name = backend_name

    def analyze(self, system_prompt: str, request: AgentRequest) -> str:
        raise AgentBackendError(
            f"Agent backend '{self.backend_name}' is not implemented. "
            "Select an implemented LLM backend with REQIFY_AGENT_BACKEND."
        )


def default_prompt() -> str:
    return os.environ.get("REQIFY_AGENT_PROMPT", DEFAULT_AGENT_PROMPT)


def backend_from_config() -> AgentBackend:
    backend_name = os.environ.get("REQIFY_AGENT_BACKEND", "local").strip().lower()
    if backend_name == "local":
        return LocalAgentBackend()
    if backend_name in {"openai", "chatgpt", "chatgpt-pro"}:
        return OpenAIResponsesBackend()
    if backend_name in {"bedrock", "amazon-bedrock", "aws-bedrock"}:
        return BedrockConverseBackend()
    return UnconfiguredAgentBackend(backend_name)


def analyze_agent(request: AgentRequest) -> dict[str, str]:
    backend = backend_from_config()
    response = backend.analyze(default_prompt(), request).strip()
    if not response:
        raise AgentBackendError("The LLM backend returned an empty response.")
    return {"response": response}


def compose_user_input(request: AgentRequest) -> str:
    user_prompt = request.user_prompt.strip() or "Analyze the selected requirement."
    object_summary = summarize_object(request.selected_object)
    if not object_summary:
        return user_prompt
    return f"{user_prompt}\n\nSelected item context:\n{object_summary}"


def summarize_object(selected_object: dict[str, object] | None) -> str:
    if not selected_object:
        return ""
    lines = [f"Object: {selected_object.get('id', '')}"]
    title = selected_object.get("title")
    if title:
        lines.append(f"Title: {title}")
    attributes = selected_object.get("attributes")
    if isinstance(attributes, list):
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            name = str(attribute.get("name") or attribute.get("id") or "Attribute")
            value = attribute.get("displayValue", attribute.get("value", ""))
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            text = str(value).strip()
            if text:
                lines.append(f"{name}: {text[:700]}")
    return "\n".join(lines)


def env_first(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def int_env(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise AgentBackendError(f"{name} must be an integer.") from exc
    if parsed <= 0:
        raise AgentBackendError(f"{name} must be greater than zero.")
    return parsed


def optional_float_env(name: str) -> float | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise AgentBackendError(f"{name} must be a number.") from exc


def timeout_seconds() -> float:
    return float(int_env("REQIFY_AGENT_TIMEOUT", 60))


def post_json(url: str, payload: dict[str, object], headers: dict[str, str]) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    return post_json_body(url, body, headers)


def post_json_body(url: str, body: bytes, headers: dict[str, str]) -> dict[str, object]:
    request = UrlRequest(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds()) as response:
            return parse_json_response(response.read())
    except HTTPError as exc:
        raise AgentBackendError(http_error_message(exc)) from exc
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise AgentBackendError(f"LLM backend request failed: {reason}") from exc
    except TimeoutError as exc:
        raise AgentBackendError("LLM backend request timed out.") from exc


def aws_post_json(
    url: str,
    payload: dict[str, object],
    region: str,
    service: str,
    access_key: str,
    secret_key: str,
    session_token: str,
) -> dict[str, object]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = aws_sigv4_headers(url, body, region, service, access_key, secret_key, session_token)
    return post_json_body(url, body, headers)


def parse_json_response(body: bytes) -> dict[str, object]:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise AgentBackendError("LLM backend returned invalid JSON.") from exc
    if not isinstance(parsed, dict):
        raise AgentBackendError("LLM backend returned an unexpected response shape.")
    return parsed


def http_error_message(exc: HTTPError) -> str:
    body = exc.read()
    try:
        payload = parse_json_response(body)
    except AgentBackendError:
        details = body.decode("utf-8", errors="replace").strip()
    else:
        details = extract_error_message(payload)
    if details:
        return f"LLM backend returned HTTP {exc.code}: {details}"
    return f"LLM backend returned HTTP {exc.code}: {exc.reason}"


def extract_error_message(payload: dict[str, object]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if message:
            return str(message)
    if isinstance(error, str):
        return error
    message = payload.get("message")
    return str(message) if message else ""


def extract_openai_text(payload: dict[str, object]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text
    texts: list[str] = []
    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "output_text" and isinstance(block.get("text"), str):
                    texts.append(block["text"])
    return "\n".join(texts)


def extract_bedrock_text(payload: dict[str, object]) -> str:
    output = payload.get("output")
    if not isinstance(output, dict):
        return ""
    message = output.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    texts = [block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)]
    return "\n".join(texts)


def aws_sigv4_headers(
    url: str,
    body: bytes,
    region: str,
    service: str,
    access_key: str,
    secret_key: str,
    session_token: str,
) -> dict[str, str]:
    parsed = urlparse(url)
    now = dt.datetime.now(dt.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    body_hash = hashlib.sha256(body).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "Host": parsed.netloc,
        "X-Amz-Content-Sha256": body_hash,
        "X-Amz-Date": amz_date,
    }
    if session_token:
        headers["X-Amz-Security-Token"] = session_token
    canonical_headers = "".join(f"{name.lower()}:{headers[name]}\n" for name in sorted(headers, key=str.lower))
    signed_headers = ";".join(name.lower() for name in sorted(headers, key=str.lower))
    canonical_query = parsed.query
    canonical_request = "\n".join(
        [
            "POST",
            parsed.path or "/",
            canonical_query,
            canonical_headers,
            signed_headers,
            body_hash,
        ]
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = aws_signing_key(secret_key, date_stamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers


def aws_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    key = hmac.new(key, region.encode("utf-8"), hashlib.sha256).digest()
    key = hmac.new(key, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(key, b"aws4_request", hashlib.sha256).digest()
