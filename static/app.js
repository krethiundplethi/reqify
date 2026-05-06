const state = {
  sessionId: null,
  fileName: "",
  specifications: [],
  objects: {},
  documentOrder: [],
  outlineNumbers: {},
  selectedId: null,
  history: [],
  historyIndex: 0,
  dirty: false,
};

const els = {
  fileInput: document.getElementById("fileInput"),
  saveButton: document.getElementById("saveButton"),
  exportButton: document.getElementById("exportButton"),
  status: document.getElementById("status"),
  tree: document.getElementById("tree"),
  documentView: document.getElementById("documentView"),
  attributesView: document.getElementById("attributesView"),
  historyView: document.getElementById("historyView"),
  historyBack: document.getElementById("historyBack"),
  historyForward: document.getElementById("historyForward"),
};

function setStatus(text, dirty = state.dirty) {
  els.status.textContent = text;
  els.status.classList.toggle("dirty", dirty);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function plainText(value) {
  const div = document.createElement("div");
  div.innerHTML = value || "";
  return div.textContent.trim().replace(/\s+/g, " ");
}

function ingest(payload) {
  state.sessionId = payload.sessionId;
  state.fileName = payload.fileName || "";
  state.specifications = payload.specifications || [];
  state.objects = payload.objects || {};
  state.documentOrder = payload.documentOrder || Object.keys(state.objects);
  state.outlineNumbers = payload.outlineNumbers || {};
  state.history = payload.history || [];
  state.historyIndex = 0;
  state.selectedId = state.selectedId && state.objects[state.selectedId] ? state.selectedId : state.documentOrder[0] || null;
  state.dirty = false;
  render();
  setStatus(state.fileName || "Loaded");
  els.saveButton.disabled = false;
  els.exportButton.disabled = false;
}

async function apiJson(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    throw new Error(payload?.error || response.statusText);
  }
  return payload;
}

function markDirty() {
  state.dirty = true;
  setStatus(`${state.fileName} • unsaved`, true);
}

function getAttribute(objectId, attrId) {
  return state.objects[objectId]?.attributes?.find((attr) => attr.id === attrId);
}

function attrKey(attr) {
  return String(attr.name || attr.id || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function isReqifText(attr) {
  return attrKey(attr) === "reqiftext";
}

function isChapterName(attr) {
  const key = attrKey(attr);
  return key === "reqifchaptername" || key.endsWith("chaptername");
}

function chapterNameAttr(object) {
  return object.attributes.find(isChapterName);
}

function reqifTextAttr(object) {
  return object.attributes.find(isReqifText);
}

function updateAttribute(objectId, attrId, value) {
  const attr = getAttribute(objectId, attrId);
  if (!attr || attr.value === value) return;
  attr.value = value;
  state.objects[objectId].title = computeTitle(state.objects[objectId]);
  markDirty();
}

function computeTitle(object) {
  const chapterAttr = chapterNameAttr(object);
  const textAttr = reqifTextAttr(object);
  if (chapterAttr && plainText(chapterAttr.value)) return plainText(chapterAttr.value);
  if (textAttr && plainText(textAttr.value)) return plainText(textAttr.value);
  const htmlAttr = object.attributes.find((attr) => attr.type === "xhtml" && plainText(attr.value));
  const anyAttr = object.attributes.find((attr) => plainText(attr.value));
  return plainText((htmlAttr || anyAttr || {}).value || "") || object.id;
}

function displayTitle(objectId) {
  const object = state.objects[objectId];
  const number = state.outlineNumbers[objectId];
  const title = object?.title || objectId;
  return number ? `${number} ${title}` : title;
}

function selectObject(objectId) {
  if (!state.objects[objectId]) return;
  state.selectedId = objectId;
  renderTree();
  renderAttributes();
  document.querySelectorAll(".document-item").forEach((node) => {
    node.classList.toggle("active", node.dataset.objectId === objectId);
  });
  document.querySelector(`[data-object-id="${CSS.escape(objectId)}"]`)?.scrollIntoView({ block: "nearest" });
}

function render() {
  renderTree();
  renderDocument();
  renderAttributes();
  renderHistory();
}

function renderTree() {
  els.tree.innerHTML = "";
  if (!state.specifications.length && !state.documentOrder.length) {
    els.tree.className = "tree empty-state";
    els.tree.textContent = "No structure";
    return;
  }
  els.tree.className = "tree";
  if (state.specifications.length) {
    state.specifications.forEach((spec) => {
      const specNode = document.createElement("div");
      specNode.className = "tree-spec";
      specNode.innerHTML = `<div class="tree-spec-title">${escapeHtml(spec.title)}</div>`;
      renderTreeNodes(spec.children || [], specNode);
      els.tree.appendChild(specNode);
    });
  } else {
    renderTreeNodes(
      state.documentOrder.map((id) => ({ id, objectId: id, title: state.objects[id]?.title || id, children: [] })),
      els.tree,
    );
  }
}

function renderTreeNodes(nodes, parent) {
  nodes.forEach((node) => {
    const wrapper = document.createElement("div");
    const button = document.createElement("button");
    button.className = `tree-node${node.objectId === state.selectedId ? " active" : ""}`;
    button.textContent = displayTitle(node.objectId);
    button.addEventListener("click", () => selectObject(node.objectId));
    wrapper.appendChild(button);
    if (node.children?.length) {
      const children = document.createElement("div");
      children.className = "tree-children";
      renderTreeNodes(node.children, children);
      wrapper.appendChild(children);
    }
    parent.appendChild(wrapper);
  });
}

function renderDocument() {
  els.documentView.innerHTML = "";
  els.documentView.className = "document-view";
  if (!state.documentOrder.length) {
    els.documentView.className = "document-view empty-state";
    els.documentView.textContent = "No content";
    return;
  }
  state.documentOrder.forEach((objectId) => {
    const object = state.objects[objectId];
    if (!object) return;
    const chapterAttr = chapterNameAttr(object);
    const textAttr = reqifTextAttr(object);
    if (!chapterAttr && !textAttr) return;
    const item = document.createElement("article");
    item.className = `document-item${chapterAttr ? " chapter-item" : ""}${objectId === state.selectedId ? " active" : ""}`;
    item.dataset.objectId = objectId;
    if (textAttr) {
      item.appendChild(renderDocumentText(objectId, textAttr));
    } else if (chapterAttr) {
      item.appendChild(renderChapterHeading(objectId, chapterAttr));
    }
    item.addEventListener("click", (event) => {
      if (!event.target.closest(".editor-toolbar")) selectObject(objectId);
    });
    els.documentView.appendChild(item);
  });
}

function renderChapterHeading(objectId, attr) {
  const heading = document.createElement("section");
  heading.className = "chapter-heading";
  const number = state.outlineNumbers[objectId] || "";
  heading.innerHTML = `<div class="chapter-number">${escapeHtml(number)}</div>`;
  heading.appendChild(renderXhtmlEditor(objectId, attr, "document chapter"));
  return heading;
}

function renderDocumentText(objectId, attr) {
  const body = document.createElement("section");
  body.className = "document-text";
  if (attr.type === "xhtml") {
    body.appendChild(renderXhtmlEditor(objectId, attr, "document text"));
  } else {
    const input = document.createElement("textarea");
    input.className = "document-text-input";
    input.value = attr.value || "";
    input.dataset.objectId = objectId;
    input.dataset.attrId = attr.id;
    input.dataset.origin = "document text";
    input.addEventListener("input", () => {
      updateAttribute(objectId, attr.id, input.value);
      syncEditors(objectId, attr.id, input.value, input);
    });
    body.appendChild(input);
  }
  return body;
}

function renderAttributes() {
  els.attributesView.innerHTML = "";
  if (!state.selectedId || !state.objects[state.selectedId]) {
    els.attributesView.className = "attributes-view empty-state";
    els.attributesView.textContent = "No selection";
    return;
  }
  els.attributesView.className = "attributes-view";
  const object = state.objects[state.selectedId];
  const attrs = object.attributes.filter((attr) => !isReqifText(attr));
  if (!attrs.length) {
    els.attributesView.className = "attributes-view empty-state";
    els.attributesView.textContent = "No attributes";
    return;
  }
  attrs.forEach((attr) => {
    els.attributesView.appendChild(renderAttributeBlock(object.id, attr, "attributes"));
  });
}

function renderAttributeBlock(objectId, attr, origin) {
  const block = document.createElement("section");
  block.className = "attribute-block";
  block.innerHTML = `<div class="attribute-label">${escapeHtml(attr.name || attr.id)}</div>`;
  if (attr.type === "enumeration") {
    block.appendChild(renderEnumerationEditor(objectId, attr));
  } else if (attr.type === "xhtml") {
    block.appendChild(renderXhtmlEditor(objectId, attr, origin));
  } else if (attr.editable) {
    const input = document.createElement("input");
    input.className = "text-input";
    input.value = attr.value || "";
    input.addEventListener("input", () => {
      updateAttribute(objectId, attr.id, input.value);
      syncEditors(objectId, attr.id, input.value, input);
    });
    block.appendChild(input);
  } else {
    const readonly = document.createElement("div");
    readonly.className = "text-input";
    readonly.textContent = attr.value || "";
    block.appendChild(readonly);
  }
  block.dataset.origin = origin;
  return block;
}

function enumValues(attr) {
  if (Array.isArray(attr.value)) return attr.value.map(String);
  if (attr.value) return [String(attr.value)];
  return [];
}

function optionLabel(attr, optionId) {
  return attr.options?.find((option) => option.id === optionId)?.label || optionId;
}

function renderEnumerationEditor(objectId, attr) {
  if (attr.multiple) return renderMultiEnumerationEditor(objectId, attr);
  const select = document.createElement("select");
  select.className = "choice-select";
  select.dataset.objectId = objectId;
  select.dataset.attrId = attr.id;
  select.innerHTML = `<option value=""></option>`;
  (attr.options || []).forEach((option) => {
    const optionNode = document.createElement("option");
    optionNode.value = option.id;
    optionNode.textContent = option.label || option.id;
    select.appendChild(optionNode);
  });
  const selected = enumValues(attr)[0] || "";
  if (selected && !(attr.options || []).some((option) => option.id === selected)) {
    const unknownOption = document.createElement("option");
    unknownOption.value = selected;
    unknownOption.textContent = selected;
    select.appendChild(unknownOption);
  }
  select.value = selected;
  select.addEventListener("change", () => {
    updateAttribute(objectId, attr.id, select.value ? [select.value] : []);
  });
  return select;
}

function renderMultiEnumerationEditor(objectId, attr) {
  const wrapper = document.createElement("div");
  wrapper.className = "choice-multi";
  const selected = document.createElement("div");
  selected.className = "choice-selected";
  const renderSelected = () => {
    selected.innerHTML = "";
    enumValues(attr).forEach((value) => {
      const entry = document.createElement("div");
      entry.className = "choice-chip";
      entry.innerHTML = `<span>${escapeHtml(optionLabel(attr, value))}</span>`;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.title = "Remove";
      remove.textContent = "×";
      remove.addEventListener("click", () => {
        const next = enumValues(attr).filter((item) => item !== value);
        updateAttribute(objectId, attr.id, next);
        renderSelected();
        renderAddSelect();
      });
      entry.appendChild(remove);
      selected.appendChild(entry);
    });
  };
  const addRow = document.createElement("div");
  addRow.className = "choice-add";
  const renderAddSelect = () => {
    addRow.innerHTML = "";
    const select = document.createElement("select");
    select.className = "choice-select";
    select.innerHTML = `<option value="">Add choice</option>`;
    const current = new Set(enumValues(attr));
    (attr.options || [])
      .filter((option) => !current.has(option.id))
      .forEach((option) => {
        const optionNode = document.createElement("option");
        optionNode.value = option.id;
        optionNode.textContent = option.label || option.id;
        select.appendChild(optionNode);
      });
    select.addEventListener("change", () => {
      if (!select.value) return;
      updateAttribute(objectId, attr.id, [...enumValues(attr), select.value]);
      renderSelected();
      renderAddSelect();
    });
    addRow.appendChild(select);
  };
  renderSelected();
  renderAddSelect();
  wrapper.appendChild(selected);
  wrapper.appendChild(addRow);
  return wrapper;
}

function renderXhtmlEditor(objectId, attr, origin) {
  const shell = document.createElement("div");
  shell.className = "editor-shell";
  shell.innerHTML = `
    <div class="editor-toolbar">
      <button type="button" data-command="bold" title="Bold">B</button>
      <button type="button" data-command="italic" title="Italic">I</button>
      <button type="button" data-command="underline" title="Underline">U</button>
      <button type="button" data-command="insertUnorderedList" title="Bullet list">•</button>
      <button type="button" data-command="insertOrderedList" title="Numbered list">1.</button>
      <button type="button" data-command="undo" title="Undo">↶</button>
      <button type="button" data-command="redo" title="Redo">↷</button>
    </div>
  `;
  const editor = document.createElement("div");
  editor.className = "xhtml-editor";
  editor.contentEditable = attr.editable ? "true" : "false";
  editor.dataset.objectId = objectId;
  editor.dataset.attrId = attr.id;
  editor.dataset.origin = origin;
  editor.innerHTML = attr.value || "";
  editor.addEventListener("input", () => {
    updateAttribute(objectId, attr.id, editor.innerHTML);
    syncEditors(objectId, attr.id, editor.innerHTML, editor);
  });
  shell.appendChild(editor);
  shell.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => {
      editor.focus();
      document.execCommand(button.dataset.command, false, null);
      updateAttribute(objectId, attr.id, editor.innerHTML);
      syncEditors(objectId, attr.id, editor.innerHTML, editor);
    });
  });
  return shell;
}

function syncEditors(objectId, attrId, value, source) {
  document.querySelectorAll(`[data-object-id="${CSS.escape(objectId)}"][data-attr-id="${CSS.escape(attrId)}"]`).forEach((node) => {
    if (node === source) return;
    if (node.classList.contains("xhtml-editor")) {
      node.innerHTML = value;
    } else if ("value" in node) {
      node.value = value;
    }
  });
  document.querySelectorAll(`.document-item[data-object-id="${CSS.escape(objectId)}"] .object-title`).forEach((node) => {
    node.textContent = displayTitle(objectId);
  });
  renderTree();
}

function renderHistory() {
  els.historyView.innerHTML = "";
  if (!state.history.length) {
    els.historyView.className = "history-view empty-state";
    els.historyView.textContent = "No commits";
    els.historyBack.disabled = true;
    els.historyForward.disabled = true;
    return;
  }
  els.historyView.className = "history-view";
  state.history.forEach((entry, index) => {
    const button = document.createElement("button");
    button.className = `history-entry${index === state.historyIndex ? " active" : ""}`;
    button.innerHTML = `
      <div class="history-subject">${escapeHtml(entry.subject)}</div>
      <div class="history-meta">${escapeHtml(entry.short)} · ${escapeHtml(entry.date)}</div>
    `;
    button.addEventListener("click", () => checkoutHistory(index));
    els.historyView.appendChild(button);
  });
  els.historyBack.disabled = state.historyIndex >= state.history.length - 1;
  els.historyForward.disabled = state.historyIndex <= 0;
}

async function checkoutHistory(index) {
  if (!state.sessionId || !state.history[index]) return;
  if (state.dirty && !confirm("Discard unsaved changes and load this commit?")) return;
  setStatus("Loading commit...");
  const payload = await apiJson(`/api/session/${state.sessionId}/checkout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ commit: state.history[index].hash }),
  });
  ingest(payload);
  state.historyIndex = index;
  renderHistory();
  setStatus(`${state.fileName} · ${state.history[index].short}`);
}

els.fileInput.addEventListener("change", async () => {
  const file = els.fileInput.files[0];
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  setStatus("Loading...");
  try {
    const payload = await apiJson("/api/load", { method: "POST", body: formData });
    ingest(payload);
  } catch (error) {
    setStatus(error.message);
  } finally {
    els.fileInput.value = "";
  }
});

els.saveButton.addEventListener("click", async () => {
  if (!state.sessionId) return;
  const updates = {};
  Object.values(state.objects).forEach((object) => {
    updates[object.id] = {};
    object.attributes.forEach((attr) => {
      updates[object.id][attr.id] = { value: attr.value };
    });
  });
  setStatus("Saving...");
  try {
    const payload = await apiJson(`/api/session/${state.sessionId}/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ objects: updates }),
    });
    ingest(payload);
    setStatus(payload.committed ? `${state.fileName} · committed` : `${state.fileName} · unchanged`);
  } catch (error) {
    setStatus(error.message, true);
  }
});

els.exportButton.addEventListener("click", () => {
  if (!state.sessionId) return;
  window.location.href = `/api/session/${state.sessionId}/export`;
});

els.historyBack.addEventListener("click", () => checkoutHistory(state.historyIndex + 1));
els.historyForward.addEventListener("click", () => checkoutHistory(state.historyIndex - 1));
