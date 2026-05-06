# Reqify

Reqify is a small Python web application for editing ReqIF documents in a browser.

## Run

```bash
python3 reqify_server.py
```

Open `http://127.0.0.1:8080`.

## Current scope

- Upload `.reqif`, `.xml`, and `.reqifz` files.
- Explore `SPECIFICATION` / `SPEC-HIERARCHY` structure on the left.
- Edit XHTML attributes in the center document view and the right attribute pane.
- Edit simple text-like ReqIF attribute values.
- Save changes into a per-document git repository under `data/sessions`.
- Load earlier commits from the history panel.
- Export the edited `.reqif`, `.xml`, or `.reqifz`.
- Analyze the selected item through a configurable agent backend.

The implementation uses only the Python standard library and browser-native JavaScript.

## Agent backend

The agent panel posts to `/api/agent/analyze`. Successful responses are reserved for text returned by an implemented LLM backend; unavailable or unimplemented backends return a JSON error with the reason.

Agent responses must be machine-readable JSON with a human-readable `markdown` field and an `edits` array that identifies the target ReqIF object and attribute. Reqify validates this JSON before returning it to the browser. The browser renders the markdown as XHTML and uses the edits for `Apply & Next`.

Configure `REQIFY_AGENT_PROMPT` to replace the default system prompt and `REQIFY_AGENT_BACKEND` to select a backend:

- `local`: default placeholder, returns an error until an LLM backend is configured.
- `openai`, `chatgpt`, or `chatgpt-pro`: calls the OpenAI Responses API.
- `bedrock`, `amazon-bedrock`, or `aws-bedrock`: calls Amazon Bedrock Converse.

OpenAI configuration:

```bash
export REQIFY_AGENT_BACKEND=openai
export OPENAI_API_KEY=...
export REQIFY_OPENAI_MODEL=gpt-5.2
```

`REQIFY_OPENAI_API_KEY` can be used instead of `OPENAI_API_KEY`. `REQIFY_OPENAI_BASE_URL` can override the default `https://api.openai.com/v1`. ChatGPT Pro and OpenAI API billing are separate, so a ChatGPT Pro subscription still needs API access and an API key for this backend.

Amazon Bedrock configuration:

```bash
export REQIFY_AGENT_BACKEND=bedrock
export REQIFY_BEDROCK_REGION=us-east-1
export REQIFY_BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

`AWS_SESSION_TOKEN` is supported for temporary credentials. `REQIFY_BEDROCK_ENDPOINT` can override the default regional runtime endpoint. Both OpenAI and Bedrock support `REQIFY_AGENT_MAX_OUTPUT_TOKENS`, `REQIFY_AGENT_TEMPERATURE`, and `REQIFY_AGENT_TIMEOUT`.

## Code layout

- `reqify_server.py` is the executable entrypoint.
- `reqify/web.py` contains HTTP routing and static file serving.
- `reqify/session_store.py` manages uploaded documents, per-session git repos, history, save, and export.
- `reqify/agent.py` contains the configurable agent backend abstraction.
- `reqify/reqif_document.py` parses and updates ReqIF XML.
- `reqify/xml_utils.py`, `reqify/git_repo.py`, `reqify/http_utils.py`, and `reqify/config.py` contain focused helper code.
