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
- Export the edited `.reqif` or `.reqifz`.

The implementation uses only the Python standard library and browser-native JavaScript.

## Code layout

- `reqify_server.py` is the executable entrypoint.
- `reqify/web.py` contains HTTP routing and static file serving.
- `reqify/session_store.py` manages uploaded documents, per-session git repos, history, save, and export.
- `reqify/reqif_document.py` parses and updates ReqIF XML.
- `reqify/xml_utils.py`, `reqify/git_repo.py`, `reqify/http_utils.py`, and `reqify/config.py` contain focused helper code.
