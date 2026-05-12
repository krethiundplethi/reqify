from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import sys
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from .attachment_preview import excel_attachment_prompt_context
from .xml_utils import attribute_key, strip_markup


DEFAULT_AGENT_PROMPT = """As an automotive requirements engineer, analyze this requirement statement.
Be as concise as possible.
Do not include what works, positive observations, risk sections, or discussion.
Return only terse findings, one quality rating, verification fields, and concrete suggested edits.

Guidance:
- "well_formed" considers clarity, singularity, unambiguity, measurable criteria, absence of design constraint unless intended.
- Analyze and improve the requirement text and verification fields: Verification Criteria, Verification Method or Measure, and Verification Domain.
- If a verification field is missing or weak, include a concrete edit for the exact verification attribute from context.
- Minimalistic edits.
"""

AGENT_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "markdown": {
            "type": "string",
            "description": "Human-readable markdown analysis and short rationale for the proposed changes.",
        },
        "edits": {
            "type": "array",
            "description": "Machine-readable field edits that can be applied to the selected ReqIF object.",
            "items": {
                "type": "object",
                "properties": {
                    "objectId": {"type": "string"},
                    "attributeId": {"type": "string"},
                    "attributeName": {"type": "string"},
                    "valueXhtml": {"type": "string"},
                    "valueEnum": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["objectId", "attributeId", "attributeName"],
                "anyOf": [{"required": ["valueXhtml"]}, {"required": ["valueEnum"]}],
                "additionalProperties": False,
            },
        },
    },
    "required": ["markdown", "edits"],
    "additionalProperties": False,
}

STRUCTURED_RESPONSE_INSTRUCTIONS = """Return only JSON matching this schema:
{
  "markdown": "Concise markdown. Start with exactly one rating line: quality: high, quality: medium, or quality: low. Then include only terse improvement finding and verification fields.",
  "edits": [
    {
      "objectId": "selected ReqIF object id",
      "attributeId": "exact attribute id from the selected item context",
      "attributeName": "exact attribute name from the selected item context",
      "valueXhtml": "complete replacement value as XHTML fragment",
      "valueEnum": ["for enumeration fields only, selected option labels or ids"]
    }
  ]
}

Keep markdown short.
Use exactly one quality rating: high, medium, or low.
Do not use numeric scores, letter grades, well_formed, or multiple ratings.
No positive feedback, no risk section, no discussion.
Include edits for requirement text and verification fields when any needs improvement and the field exists.
For enumeration fields, use valueEnum with allowed option labels or ids from context.
For text fields, use valueXhtml with the complete replacement as an XHTML fragment.
For plain non-XHTML fields, still use valueXhtml; the client converts it to plain text.
Do not use Markdown inside edit values. Use <br/> for line breaks in valueXhtml.
Use edits only for fields where you propose a concrete replacement.
Preserve the full intended field value, not a diff."""


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
        model = os.environ.get("REQIFY_OPENAI_MODEL", "gpt-5.4").strip()
        user_input = compose_user_input(request)
        payload: dict[str, object] = {
            "model": model,
            "instructions": system_prompt.strip(),
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_input}],
                }
            ],
            "max_output_tokens": int_env("REQIFY_AGENT_MAX_OUTPUT_TOKENS", 1200),
        }
        temperature = optional_float_env("REQIFY_AGENT_TEMPERATURE")
        if temperature is not None:
            payload["temperature"] = temperature
        print_llm_prompts("openai", model, system_prompt, user_input)
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
        user_input = compose_user_input(request)
        body: dict[str, object] = {
            "system": [{"text": system_prompt.strip()}],
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": user_input}],
                }
            ],
            "inferenceConfig": {"maxTokens": int_env("REQIFY_AGENT_MAX_OUTPUT_TOKENS", 1200)},
        }
        temperature = optional_float_env("REQIFY_AGENT_TEMPERATURE")
        if temperature is not None:
            inference_config = body["inferenceConfig"]
            if isinstance(inference_config, dict):
                inference_config["temperature"] = temperature
        print_llm_prompts("bedrock", model_id, system_prompt, user_input)
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


def agent_instructions() -> str:
    return f"{default_prompt().strip()}\n\n{STRUCTURED_RESPONSE_INSTRUCTIONS}"


def debug_enabled() -> bool:
    return os.environ.get("REQIFY_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def print_llm_prompts(backend: str, model: str, system_prompt: str, user_input: str) -> None:
    if not debug_enabled():
        return
    print("\n=== Reqify LLM prompt debug ===", file=sys.stderr)
    print(f"Backend: {backend}", file=sys.stderr)
    if model:
        print(f"Model: {model}", file=sys.stderr)
    print("--- system ---", file=sys.stderr)
    print(system_prompt.strip(), file=sys.stderr)
    print("--- user ---", file=sys.stderr)
    print(user_input.strip(), file=sys.stderr)
    print("=== end Reqify LLM prompt debug ===\n", file=sys.stderr)


def print_llm_response(response: str) -> None:
    if not debug_enabled():
        return
    print("\n=== Reqify LLM response debug ===", file=sys.stderr)
    print(response.strip(), file=sys.stderr)
    print("=== end Reqify LLM response debug ===\n", file=sys.stderr)


def backend_from_config() -> AgentBackend:
    backend_name = os.environ.get("REQIFY_AGENT_BACKEND", "local").strip().lower()
    if backend_name == "local":
        return LocalAgentBackend()
    if backend_name in {"openai", "chatgpt", "chatgpt-pro"}:
        return OpenAIResponsesBackend()
    if backend_name in {"bedrock", "amazon-bedrock", "aws-bedrock"}:
        return BedrockConverseBackend()
    return UnconfiguredAgentBackend(backend_name)


def analyze_agent(request: AgentRequest) -> dict[str, object]:
    backend = backend_from_config()
    response = backend.analyze(agent_instructions(), request).strip()
    print_llm_response(response)
    if not response:
        raise AgentBackendError("The LLM backend returned an empty response.")
    structured = parse_agent_response(response)
    return {
        "response": structured["markdown"],
        "markdown": structured["markdown"],
        "edits": structured["edits"],
    }


def compose_user_input(request: AgentRequest) -> str:
    user_prompt = request.user_prompt.strip() or "Analyze the selected requirement."
    object_summary = summarize_object(request.selected_object)
    if not object_summary:
        return user_prompt
    attachment_summary = excel_attachment_prompt_context(request.session_id, request.selected_object)
    if attachment_summary:
        return f"{user_prompt}\n\nSelected item context:\n{object_summary}\n\n{attachment_summary}"
    return f"{user_prompt}\n\nSelected item context:\n{object_summary}"


def summarize_object(selected_object: dict[str, object] | None) -> str:
    if not selected_object:
        return ""
    lines = [f"Object: {selected_object.get('id', '')}"]
    type_name = selected_object.get("objectTypeName")
    if type_name:
        lines.append(f"Object type: {type_name}")
    attributes = selected_object.get("attributes")
    reqif_text_attribute = None
    chapter_name_attribute = None
    if isinstance(attributes, list):
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            key = attribute_key(attribute)
            if key == "reqiftext":
                reqif_text_attribute = attribute
            elif key == "reqifchaptername" or key.endswith("chaptername"):
                chapter_name_attribute = attribute
        if reqif_text_attribute:
            lines.append(format_attribute_line(reqif_text_attribute, "Requirement text", 1200))
        elif selected_object.get("title"):
            lines.append(f"Title: {strip_markup(str(selected_object.get('title') or ''))}")
        if chapter_name_attribute:
            lines.append(format_attribute_line(chapter_name_attribute, "Chapter name"))
        for attribute in attributes:
            if not isinstance(attribute, dict):
                continue
            if attribute is reqif_text_attribute or attribute is chapter_name_attribute:
                continue
            line = format_attribute_line(attribute, "Attribute")
            if line:
                lines.append(line)
            else:
                attr_id = str(attribute.get("id") or "")
                name = str(attribute.get("name") or attr_id or "Attribute")
                if is_verification_attribute(attr_id, name):
                    lines.append(f"Attribute id={attr_id} name={name}: <empty>{format_attribute_options(attribute)}")
    return "\n".join(lines)


def format_attribute_line(attribute: dict[str, object], label: str, limit: int = 700) -> str:
    attr_id = str(attribute.get("id") or "")
    name = str(attribute.get("name") or attr_id or "Attribute")
    value = attribute.get("displayValue", attribute.get("value", ""))
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    text = strip_markup(str(value)).strip()
    if not text:
        return ""
    suffix = format_attribute_options(attribute) if is_verification_attribute(attr_id, name) else ""
    return f"{label} id={attr_id} name={name}: {text[:limit]}{suffix}"


def format_attribute_options(attribute: dict[str, object]) -> str:
    options = attribute.get("options")
    if not isinstance(options, list):
        return ""
    labels = []
    for option in options:
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or option.get("id") or "").strip()
        if label:
            labels.append(label)
    return f" allowed values: {', '.join(labels)}" if labels else ""


def is_verification_attribute(attribute_id: str, name: str) -> bool:
    key = "".join(char for char in f"{attribute_id} {name}".lower() if char.isalnum())
    return "verification" in key and (
        "criteria" in key
        or "criterion" in key
        or "method" in key
        or "measure" in key
        or "domain" in key
    )


def parse_agent_response(response: str) -> dict[str, object]:
    payload_text = response.strip()
    if payload_text.startswith("```"):
        payload_text = strip_code_fence(payload_text)
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise AgentBackendError("The LLM backend did not return machine-readable JSON suggestions.") from exc
    if not isinstance(payload, dict):
        raise AgentBackendError("The LLM backend returned an unexpected suggestion shape.")
    markdown = payload.get("markdown")
    edits = payload.get("edits")
    if not isinstance(markdown, str) or not markdown.strip():
        raise AgentBackendError("The LLM backend response is missing human-readable markdown.")
    if not isinstance(edits, list):
        raise AgentBackendError("The LLM backend response is missing machine-readable edits.")
    clean_edits: list[dict[str, object]] = []
    for edit in edits:
        if not isinstance(edit, dict):
            raise AgentBackendError("The LLM backend returned an invalid edit entry.")
        object_id = str(edit.get("objectId", "")).strip()
        attribute_id = str(edit.get("attributeId", "")).strip()
        attribute_name = str(edit.get("attributeName", "")).strip()
        value_xhtml = str(edit.get("valueXhtml", "")).strip()
        value_enum_raw = edit.get("valueEnum")
        value_enum = []
        if isinstance(value_enum_raw, list):
            value_enum = [str(item).strip() for item in value_enum_raw if str(item).strip()]
        if not (object_id and (attribute_id or attribute_name) and (value_xhtml or value_enum)):
            raise AgentBackendError("The LLM backend returned an incomplete edit entry.")
        clean_edit: dict[str, object] = {
            "objectId": object_id,
            "attributeId": attribute_id,
            "attributeName": attribute_name,
            "valueXhtml": value_xhtml,
        }
        if value_enum:
            clean_edit["valueEnum"] = value_enum
        clean_edits.append(clean_edit)
    return {"markdown": markdown.strip(), "edits": clean_edits}


def strip_code_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


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
