const state = {
  sessionId: null,
  fileName: "",
  specifications: [],
  objects: {},
  documentOrder: [],
  outlineNumbers: {},
  selectedId: null,
  headCommit: "",
  history: [],
  historyIndex: 0,
  selectedHistoryIndex: 0,
  agentSuggestion: null,
  localModifiedObjectIds: new Set(),
  comparisonModifiedObjectIds: new Set(),
  selectedHistoryObjects: null,
  editorCursor: null,
  documentFilters: {
    modified: false,
    requirements: false,
    emptyVerificationCriteria: false,
  },
  dirty: false,
};

const els = {
  fileInput: document.getElementById("fileInput"),
  saveButton: document.getElementById("saveButton"),
  exportButton: document.getElementById("exportButton"),
  status: document.getElementById("status"),
  tree: document.getElementById("tree"),
  documentView: document.getElementById("documentView"),
  filterModified: document.getElementById("filterModified"),
  filterRequirements: document.getElementById("filterRequirements"),
  filterEmptyVerificationCriteria: document.getElementById("filterEmptyVerificationCriteria"),
  attributesView: document.getElementById("attributesView"),
  rightStack: document.querySelector(".right-stack"),
  rightResize: document.getElementById("rightResize"),
  historyView: document.getElementById("historyView"),
  historyBack: document.getElementById("historyBack"),
  historyForward: document.getElementById("historyForward"),
  agentPrompt: document.getElementById("agentPrompt"),
  agentAnalyze: document.getElementById("agentAnalyze"),
  agentApply: document.getElementById("agentApply"),
  agentApplyNext: document.getElementById("agentApplyNext"),
  agentResponse: document.getElementById("agentResponse"),
  agentPreview: document.getElementById("agentPreview"),
  diffModal: document.getElementById("diffModal"),
  diffTitle: document.getElementById("diffTitle"),
  diffText: document.getElementById("diffText"),
  diffClose: document.getElementById("diffClose"),
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

function markdownInline(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function markdownToXhtml(markdown) {
  const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  const html = [];
  let listType = null;
  const closeList = () => {
    if (listType) {
      html.push(`</${listType}>`);
      listType = null;
    }
  };
  lines.forEach((line) => {
    const trimmed = line.trim();
    if (!trimmed) {
      closeList();
      return;
    }
    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      closeList();
      const level = heading[1].length + 2;
      html.push(`<h${level}>${markdownInline(heading[2])}</h${level}>`);
      return;
    }
    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      if (listType !== "ul") {
        closeList();
        html.push("<ul>");
        listType = "ul";
      }
      html.push(`<li>${markdownInline(bullet[1])}</li>`);
      return;
    }
    const numbered = trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (numbered) {
      if (listType !== "ol") {
        closeList();
        html.push("<ol>");
        listType = "ol";
      }
      html.push(`<li>${markdownInline(numbered[1])}</li>`);
      return;
    }
    closeList();
    html.push(`<p>${markdownInline(trimmed)}</p>`);
  });
  closeList();
  return html.join("");
}

function sanitizeXhtmlFragment(value) {
  const template = document.createElement("template");
  template.innerHTML = String(value || "");
  const allowedTags = new Set(["B", "BR", "CODE", "DIV", "EM", "I", "LI", "OL", "P", "STRONG", "U", "UL"]);
  const clean = (node) => {
    [...node.childNodes].forEach((child) => {
      if (child.nodeType === Node.TEXT_NODE) return;
      if (child.nodeType !== Node.ELEMENT_NODE || !allowedTags.has(child.tagName)) {
        child.replaceWith(document.createTextNode(child.textContent || ""));
        return;
      }
      [...child.attributes].forEach((attr) => child.removeAttribute(attr.name));
      clean(child);
    });
  };
  clean(template.content);
  return template.innerHTML;
}

function plainText(value) {
  const div = document.createElement("div");
  div.innerHTML = value || "";
  return div.textContent.trim().replace(/\s+/g, " ");
}

function richTextToPlainText(value) {
  const template = document.createElement("template");
  template.innerHTML = String(value || "");
  const blockTags = new Set([
    "ADDRESS",
    "ARTICLE",
    "ASIDE",
    "BLOCKQUOTE",
    "DD",
    "DIV",
    "DL",
    "DT",
    "FIGCAPTION",
    "FIGURE",
    "FOOTER",
    "H1",
    "H2",
    "H3",
    "H4",
    "H5",
    "H6",
    "HEADER",
    "LI",
    "MAIN",
    "NAV",
    "P",
    "PRE",
    "SECTION",
    "TABLE",
    "TBODY",
    "TFOOT",
    "THEAD",
    "TR",
    "UL",
    "OL",
  ]);
  let line = "";
  const lines = [];
  const appendText = (text) => {
    const normalized = String(text || "").replace(/\s+/g, " ");
    if (!normalized.trim()) {
      if (line && !line.endsWith(" ")) line += " ";
      return;
    }
    line += normalized;
  };
  const flushLine = () => {
    const trimmed = line.trim();
    if (trimmed) lines.push(trimmed);
    line = "";
  };
  const walk = (node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      appendText(node.textContent || "");
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    if (node.tagName === "BR") {
      flushLine();
      return;
    }
    if (blockTags.has(node.tagName) && line.trim()) flushLine();
    [...node.childNodes].forEach(walk);
    if (node.tagName === "TD" || node.tagName === "TH") appendText(" ");
    if (blockTags.has(node.tagName)) flushLine();
  };
  [...template.content.childNodes].forEach(walk);
  flushLine();
  return lines.join("\n");
}

function plainTextToXhtml(value) {
  return escapeHtml(value).replace(/\n/g, "<br>");
}

function ingest(payload) {
  const uiState = payload.uiState && typeof payload.uiState === "object" ? payload.uiState : {};
  state.sessionId = payload.sessionId;
  state.fileName = payload.fileName || "";
  state.specifications = payload.specifications || [];
  state.objects = payload.objects || {};
  state.documentOrder = payload.documentOrder || Object.keys(state.objects);
  state.outlineNumbers = payload.outlineNumbers || {};
  state.headCommit = payload.headCommit || "";
  state.history = payload.history || [];
  state.historyIndex = 0;
  state.selectedHistoryIndex = 0;
  state.agentSuggestion = null;
  state.localModifiedObjectIds = new Set();
  state.comparisonModifiedObjectIds = new Set();
  state.selectedHistoryObjects = null;
  state.selectedId = uiState.selectedId && state.objects[uiState.selectedId] ? uiState.selectedId : state.selectedId && state.objects[state.selectedId] ? state.selectedId : state.documentOrder[0] || null;
  state.editorCursor = uiState.cursor && typeof uiState.cursor === "object" ? uiState.cursor : null;
  if (typeof uiState.agentPrompt === "string") {
    els.agentPrompt.value = uiState.agentPrompt;
  }
  state.dirty = false;
  render();
  restoreEditorCursor();
  setStatus(payload.reusedSession ? `${state.fileName || "Loaded"} · recovered workspace` : state.fileName || "Loaded");
  els.exportButton.disabled = false;
  updateSaveState();
  updateAgentApplyState();
}

function captureUiState() {
  captureActiveEditorCursor();
  return {
    selectedId: state.selectedId || "",
    agentPrompt: els.agentPrompt.value,
    cursor: state.editorCursor,
  };
}

function textOffsetForPosition(root, target, offset) {
  let position = 0;
  const walk = (node) => {
    if (node === target) {
      if (node.nodeType === Node.TEXT_NODE) {
        position += Math.min(offset, node.textContent.length);
      } else {
        [...node.childNodes].slice(0, offset).forEach((child) => {
          position += child.textContent.length;
        });
      }
      return true;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      position += node.textContent.length;
      return false;
    }
    return [...node.childNodes].some(walk);
  };
  walk(root);
  return position;
}

function captureEditorCursor(editor) {
  const selection = window.getSelection();
  if (!selection || !selection.rangeCount) return;
  const range = selection.getRangeAt(0);
  if (!editor.contains(range.startContainer)) return;
  state.editorCursor = {
    objectId: editor.dataset.objectId || state.selectedId || "",
    attrId: editor.dataset.attrId || "",
    origin: editor.dataset.origin || "",
    offset: textOffsetForPosition(editor, range.startContainer, range.startOffset),
  };
}

function captureActiveEditorCursor() {
  const active = document.activeElement;
  if (active?.classList?.contains("xhtml-editor")) {
    captureEditorCursor(active);
  }
}

function setCursorByTextOffset(root, offset) {
  const range = document.createRange();
  const selection = window.getSelection();
  let remaining = Math.max(0, Number(offset) || 0);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    const length = node.textContent.length;
    if (remaining <= length) {
      range.setStart(node, remaining);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
      return;
    }
    remaining -= length;
    node = walker.nextNode();
  }
  range.selectNodeContents(root);
  range.collapse(false);
  selection.removeAllRanges();
  selection.addRange(range);
}

function restoreEditorCursor() {
  const cursor = state.editorCursor;
  if (!cursor?.objectId || !cursor?.attrId) return;
  requestAnimationFrame(() => {
    const selector = `.xhtml-editor[data-object-id="${CSS.escape(cursor.objectId)}"][data-attr-id="${CSS.escape(cursor.attrId)}"]`;
    const editor = document.querySelector(selector);
    if (!editor) return;
    selectObject(cursor.objectId);
    editor.focus();
    setCursorByTextOffset(editor, cursor.offset);
  });
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

function markDirty(objectId = null) {
  state.dirty = true;
  if (objectId) {
    state.localModifiedObjectIds.add(objectId);
    if (state.selectedHistoryObjects) recomputeComparisonModifiedObjectIds();
    updateDocumentModificationState(objectId);
  }
  setStatus(`${state.fileName} • unsaved`, true);
  updateSaveState();
}

function viewedCommit() {
  return state.history[state.historyIndex]?.hash || state.headCommit || "";
}

function isViewingHead() {
  return Boolean(state.sessionId) && (!state.history.length || viewedCommit() === state.headCommit);
}

function updateSaveState() {
  els.saveButton.disabled = !isViewingHead();
  els.saveButton.title = isViewingHead() ? "Commit edits" : "Checkout HEAD before saving";
}

function effectiveModifiedObjectIds() {
  return state.selectedHistoryObjects ? state.comparisonModifiedObjectIds : state.localModifiedObjectIds;
}

function setRightSplit(agentPercent) {
  const clamped = Math.min(72, Math.max(24, agentPercent));
  els.rightStack.style.gridTemplateRows = `minmax(140px, 1fr) 8px minmax(180px, ${clamped}%)`;
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

function isVerificationCriteria(attr) {
  const key = attrKey(attr);
  return key.includes("verification") && (key.includes("criteria") || key.includes("criterion"));
}

function isEmptyAttributeValue(value) {
  if (Array.isArray(value)) return value.length === 0;
  return !plainText(value);
}

function hasEmptyOrNoVerificationCriteria(object) {
  const verificationAttrs = object.attributes.filter(isVerificationCriteria);
  return !verificationAttrs.length || verificationAttrs.some((attr) => attr.missing || isEmptyAttributeValue(attr.value));
}

function currentRequirementText() {
  const object = state.objects[state.selectedId];
  if (!object) return "";
  const attr = reqifTextAttr(object);
  return attr ? plainText(attr.value) : "";
}

function objectKind(object) {
  const typeValues = [object.objectTypeName, object.objectTypeId];
  object.attributes.forEach((attr) => {
    const key = attrKey(attr);
    if (key.includes("type") || key.includes("category") || key.includes("kind") || key.includes("classification")) {
      typeValues.push(attr.displayValue, attr.value, attr.name, attr.id);
    }
  });
  const text = typeValues.flat().map((value) => String(value || "").toLowerCase()).join(" ");
  if (/\binfo(?:rmation|rmational)?\b/.test(text)) return "info";
  return "requirement";
}

function agentEditsForSelection() {
  if (!state.agentSuggestion || !state.selectedId) return [];
  return state.agentSuggestion.edits.filter((edit) => !edit.objectId || edit.objectId === state.selectedId);
}

function updateAgentApplyState() {
  const edits = agentEditsForSelection();
  els.agentApply.disabled = !edits.length;
  els.agentApplyNext.disabled = !edits.length;
  renderAgentPreview(edits);
}

function renderAgentPreview(edits = agentEditsForSelection()) {
  els.agentPreview.innerHTML = "";
  if (!edits.length) {
    els.agentPreview.className = "agent-preview empty-state";
    els.agentPreview.textContent = "No pending changes";
    return;
  }
  els.agentPreview.className = "agent-preview";
  edits.forEach((edit) => {
    const object = state.objects[edit.objectId || state.selectedId];
    const attr = object ? findEditableAttribute(object, edit) : null;
    const label = attr?.name || edit.attributeName || edit.attributeId || "Field";
    const nextValue = attr ? agentEditValueForAttribute(attr, edit) : edit.valueMarkdown || edit.valueXhtml || "";
    const row = document.createElement("label");
    row.className = "agent-preview-edit";
    const name = document.createElement("div");
    name.className = "agent-preview-label";
    name.textContent = label;
    row.appendChild(name);
    if (attr?.type === "enumeration") {
      row.appendChild(
        renderAgentEnumerationEditor(attr, nextValue, (value) => {
          edit.valueEnum = value;
          edit.valueMarkdown = value.join(", ");
          edit.valueXhtml = escapeHtml(edit.valueMarkdown);
        }),
      );
    } else {
      const editor = renderXhtmlEditor(edit.objectId || state.selectedId || "", { id: edit.attributeId || attr?.id || "", value: nextValue, editable: true }, "agent preview");
      const editable = editor.querySelector(".xhtml-editor");
      editable.addEventListener("input", () => {
        const value = editable.innerHTML || "";
        edit.valueMarkdown = plainText(value);
        edit.valueXhtml = value;
      });
      row.appendChild(editor);
    }
    els.agentPreview.appendChild(row);
  });
}

function updateAttribute(objectId, attrId, value) {
  const attr = getAttribute(objectId, attrId);
  if (!attr || attr.value === value) return;
  attr.value = value;
  state.objects[objectId].title = computeTitle(state.objects[objectId]);
  markDirty(objectId);
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
  state.editorCursor = state.editorCursor?.objectId === objectId ? state.editorCursor : null;
  renderTree();
  renderAttributes();
  updateAgentApplyState();
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
  updateAgentApplyState();
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

function shouldShowDocumentObject(objectId) {
  const object = state.objects[objectId];
  if (!object || !reqifTextAttr(object)) return false;
  if (state.documentFilters.modified && !effectiveModifiedObjectIds().has(objectId)) return false;
  if (state.documentFilters.requirements && objectKind(object) !== "requirement") return false;
  if (state.documentFilters.emptyVerificationCriteria && !hasEmptyOrNoVerificationCriteria(object)) return false;
  return true;
}

function outlineLevel(objectId) {
  const number = String(state.outlineNumbers[objectId] || "");
  if (!number) return 0;
  return number.split(".").filter(Boolean).length;
}

function chapterHasVisibleContent(index) {
  const level = outlineLevel(state.documentOrder[index]);
  for (let offset = index + 1; offset < state.documentOrder.length; offset += 1) {
    const objectId = state.documentOrder[offset];
    const object = state.objects[objectId];
    if (!object) continue;
    if (chapterNameAttr(object)) {
      const nextLevel = outlineLevel(objectId);
      if (!level || (nextLevel && nextLevel <= level)) return false;
    }
    if (reqifTextAttr(object) && shouldShowDocumentObject(objectId)) return true;
  }
  return false;
}

function renderDocument() {
  els.documentView.innerHTML = "";
  els.documentView.className = "document-view";
  if (!state.documentOrder.length) {
    els.documentView.className = "document-view empty-state";
    els.documentView.textContent = "No content";
    return;
  }
  let rendered = 0;
  state.documentOrder.forEach((objectId, index) => {
    const object = state.objects[objectId];
    if (!object) return;
    const chapterAttr = chapterNameAttr(object);
    const textAttr = reqifTextAttr(object);
    if (!chapterAttr && !textAttr) return;
    if (textAttr && !shouldShowDocumentObject(objectId)) return;
    if (chapterAttr && !textAttr && !chapterHasVisibleContent(index)) return;
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
    rendered += 1;
  });
  if (!rendered) {
    els.documentView.className = "document-view empty-state";
    els.documentView.textContent = "No matching content";
  }
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
  const object = state.objects[objectId];
  const kind = objectKind(object);
  const body = document.createElement("section");
  body.className = `document-text ${kind === "info" ? "info-item" : "requirement-item"}${effectiveModifiedObjectIds().has(objectId) ? " modified-item" : ""}`;
  const icon = document.createElement("div");
  icon.className = "document-kind-icon";
  icon.title = kind === "info" ? "Information" : "Requirement";
  icon.textContent = kind === "info" ? "I" : "R";
  body.appendChild(icon);
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

function renderAgentEnumerationEditor(attr, value, onChange) {
  let selectedValues = Array.isArray(value) ? value.map(String) : [];
  const setSelectedValues = (nextValues) => {
    selectedValues = attr.multiple ? nextValues : nextValues.slice(0, 1);
    onChange([...selectedValues]);
  };
  if (attr.multiple) {
    const wrapper = document.createElement("div");
    wrapper.className = "choice-multi";
    const selected = document.createElement("div");
    selected.className = "choice-selected";
    const addRow = document.createElement("div");
    addRow.className = "choice-add";
    const renderSelected = () => {
      selected.innerHTML = "";
      selectedValues.forEach((optionId) => {
        const entry = document.createElement("div");
        entry.className = "choice-chip";
        entry.innerHTML = `<span>${escapeHtml(optionLabel(attr, optionId))}</span>`;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.title = "Remove";
        remove.textContent = "×";
        remove.addEventListener("click", () => {
          setSelectedValues(selectedValues.filter((valueId) => valueId !== optionId));
          renderSelected();
          renderAddSelect();
        });
        entry.appendChild(remove);
        selected.appendChild(entry);
      });
    };
    const renderAddSelect = () => {
      addRow.innerHTML = "";
      const select = document.createElement("select");
      select.className = "choice-select";
      select.innerHTML = `<option value="">Add choice</option>`;
      const selectedSet = new Set(selectedValues);
      (attr.options || [])
        .filter((option) => !selectedSet.has(option.id))
        .forEach((option) => {
          const optionNode = document.createElement("option");
          optionNode.value = option.id;
          optionNode.textContent = option.label || option.id;
          select.appendChild(optionNode);
        });
      select.addEventListener("change", () => {
        if (!select.value) return;
        setSelectedValues([...selectedValues, select.value]);
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
  const select = document.createElement("select");
  select.className = "choice-select";
  select.innerHTML = `<option value=""></option>`;
  (attr.options || []).forEach((option) => {
    const optionNode = document.createElement("option");
    optionNode.value = option.id;
    optionNode.textContent = option.label || option.id;
    select.appendChild(optionNode);
  });
  const selected = selectedValues[0] || "";
  if (selected && !(attr.options || []).some((option) => option.id === selected)) {
    const unknownOption = document.createElement("option");
    unknownOption.value = selected;
    unknownOption.textContent = selected;
    select.appendChild(unknownOption);
  }
  select.value = selected;
  select.addEventListener("change", () => {
    setSelectedValues(select.value ? [select.value] : []);
  });
  return select;
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
      <button type="button" data-action="plain-text" title="Convert to plain text">Tx</button>
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
  const showToolbar = () => {
    shell.classList.add("editing");
  };
  const hideToolbar = () => {
    requestAnimationFrame(() => {
      if (!shell.contains(document.activeElement)) {
        shell.classList.remove("editing");
      }
    });
  };
  editor.addEventListener("focus", showToolbar);
  editor.addEventListener("blur", hideToolbar);
  ["focus", "keyup", "mouseup", "input"].forEach((eventName) => {
    editor.addEventListener(eventName, () => captureEditorCursor(editor));
  });
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
      captureEditorCursor(editor);
    });
  });
  const plainTextButton = shell.querySelector('[data-action="plain-text"]');
  plainTextButton.addEventListener("mousedown", (event) => event.preventDefault());
  plainTextButton.addEventListener("click", () => {
    editor.focus();
    editor.innerHTML = plainTextToXhtml(richTextToPlainText(editor.innerHTML));
    updateAttribute(objectId, attr.id, editor.innerHTML);
    syncEditors(objectId, attr.id, editor.innerHTML, editor);
    captureEditorCursor(editor);
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

function normalizedAttributeValue(attr) {
  const value = attr?.value;
  if (Array.isArray(value)) return value.map(String).sort().join("\u0001");
  if (attr?.type === "xhtml") return plainText(value);
  return String(value ?? "").trim();
}

function comparableAttributes(object) {
  const values = new Map();
  (object?.attributes || []).forEach((attr) => {
    if (!attr?.id) return;
    values.set(String(attr.id), normalizedAttributeValue(attr));
  });
  return values;
}

function objectDiffersFromSnapshot(object, snapshotObject) {
  if (!snapshotObject) return true;
  const current = comparableAttributes(object);
  const snapshot = comparableAttributes(snapshotObject);
  const ids = new Set([...current.keys(), ...snapshot.keys()]);
  for (const id of ids) {
    if ((current.get(id) || "") !== (snapshot.get(id) || "")) return true;
  }
  return false;
}

function recomputeComparisonModifiedObjectIds() {
  const next = new Set();
  const snapshotObjects = state.selectedHistoryObjects || {};
  Object.entries(state.objects).forEach(([objectId, object]) => {
    if (!reqifTextAttr(object)) return;
    if (objectDiffersFromSnapshot(object, snapshotObjects[objectId])) next.add(objectId);
  });
  state.comparisonModifiedObjectIds = next;
}

function updateDocumentModificationState(objectId) {
  const modified = effectiveModifiedObjectIds().has(objectId);
  document.querySelectorAll(`.document-item[data-object-id="${CSS.escape(objectId)}"] .document-text`).forEach((node) => {
    node.classList.toggle("modified-item", modified);
  });
  if (state.documentFilters.modified && !shouldShowDocumentObject(objectId)) renderDocument();
}

function renderHistory() {
  els.historyView.innerHTML = "";
  if (!state.history.length) {
    els.historyView.className = "history-view empty-state";
    els.historyView.textContent = "No commits";
    els.historyBack.disabled = true;
    els.historyForward.disabled = true;
    updateSaveState();
    return;
  }
  els.historyView.className = "history-view";
  state.history.forEach((entry, index) => {
    const row = document.createElement("div");
    row.className = `history-entry${index === state.historyIndex ? " checked-out" : ""}${index === state.selectedHistoryIndex ? " selected" : ""}`;
    row.tabIndex = 0;
    row.innerHTML = `
      <div class="history-subject">${escapeHtml(entry.subject)}</div>
      <div class="history-meta">${escapeHtml(entry.short)} · ${escapeHtml(entry.date)}</div>
    `;
    row.addEventListener("click", () => selectHistoryEntry(index));
    row.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      selectHistoryEntry(index);
    });
    const actions = document.createElement("div");
    actions.className = "history-actions";
    const diff = document.createElement("button");
    diff.type = "button";
    diff.className = "history-diff";
    diff.textContent = "Diff";
    diff.disabled = !state.selectedId;
    diff.addEventListener("click", (event) => {
      event.stopPropagation();
      showHistoryDiff(index);
    });
    const checkout = document.createElement("button");
    checkout.type = "button";
    checkout.className = "history-checkout";
    checkout.textContent = index === state.historyIndex ? "Current" : "Checkout";
    checkout.disabled = index === state.historyIndex;
    checkout.addEventListener("click", (event) => {
      event.stopPropagation();
      checkoutHistory(index);
    });
    actions.appendChild(diff);
    actions.appendChild(checkout);
    row.appendChild(actions);
    els.historyView.appendChild(row);
  });
  els.historyBack.disabled = state.historyIndex >= state.history.length - 1;
  els.historyForward.disabled = state.historyIndex <= 0;
  updateSaveState();
}

async function selectHistoryEntry(index) {
  const entry = state.history[index];
  if (!state.sessionId || !entry) return;
  state.selectedHistoryIndex = index;
  renderHistory();
  try {
    const payload = await apiJson(`/api/session/${state.sessionId}/commit-payload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ commit: entry.hash }),
    });
    state.selectedHistoryObjects = payload.objects && typeof payload.objects === "object" ? payload.objects : {};
    recomputeComparisonModifiedObjectIds();
    renderDocument();
    setStatus(`${state.fileName} · comparing ${entry.short}`, state.dirty);
  } catch (error) {
    state.selectedHistoryObjects = null;
    state.comparisonModifiedObjectIds = new Set();
    renderDocument();
    setStatus(error.message, state.dirty);
  }
}

async function showHistoryDiff(index) {
  const entry = state.history[index];
  if (!state.sessionId || !state.selectedId || !entry) return;
  const currentText = currentRequirementText();
  try {
    const payload = await apiJson(`/api/session/${state.sessionId}/object-text`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ commit: entry.hash, objectId: state.selectedId }),
    });
    const previousText = String(payload.text || "");
    openDiffModal(
      `Diff ${entry.short}`,
      formatPlainTextDiff(previousText, currentText, `commit ${entry.short}`, "current view"),
    );
  } catch (error) {
    openDiffModal("Diff unavailable", error.message);
  }
}

function openDiffModal(title, text) {
  els.diffTitle.textContent = title;
  els.diffText.value = text;
  els.diffModal.hidden = false;
  els.diffText.focus();
}

function closeDiffModal() {
  els.diffModal.hidden = true;
  els.diffText.value = "";
}

function formatPlainTextDiff(leftText, rightText, leftLabel, rightLabel) {
  const left = splitPlainTextLines(leftText);
  const right = splitPlainTextLines(rightText);
  const rows = longestCommonSubsequenceRows(left, right);
  const diff = [`--- ${leftLabel}`, `+++ ${rightLabel}`];
  let changed = false;
  rows.forEach((row) => {
    if (row.type === "same") {
      diff.push(`  ${row.text}`);
    } else if (row.type === "remove") {
      changed = true;
      diff.push(`- ${row.text}`);
    } else {
      changed = true;
      diff.push(`+ ${row.text}`);
    }
  });
  if (!changed) return [`--- ${leftLabel}`, `+++ ${rightLabel}`, "No plaintext differences."].join("\n");
  return diff.join("\n");
}

function splitPlainTextLines(text) {
  const normalized = String(text || "").replace(/\r\n?/g, "\n");
  return normalized ? normalized.split("\n") : [];
}

function longestCommonSubsequenceRows(left, right) {
  const table = Array.from({ length: left.length + 1 }, () => Array(right.length + 1).fill(0));
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      table[i][j] = left[i] === right[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const rows = [];
  let i = 0;
  let j = 0;
  while (i < left.length && j < right.length) {
    if (left[i] === right[j]) {
      rows.push({ type: "same", text: left[i] });
      i += 1;
      j += 1;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      rows.push({ type: "remove", text: left[i] });
      i += 1;
    } else {
      rows.push({ type: "add", text: right[j] });
      j += 1;
    }
  }
  while (i < left.length) {
    rows.push({ type: "remove", text: left[i] });
    i += 1;
  }
  while (j < right.length) {
    rows.push({ type: "add", text: right[j] });
    j += 1;
  }
  return rows;
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
  state.selectedHistoryIndex = index;
  state.selectedHistoryObjects = null;
  state.comparisonModifiedObjectIds = new Set();
  renderHistory();
  renderDocument();
  updateSaveState();
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
      body: JSON.stringify({ objects: updates, viewedCommit: viewedCommit(), uiState: captureUiState() }),
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

async function analyzeWithAgent() {
  const prompt = els.agentPrompt.value.trim();
  state.agentSuggestion = null;
  updateAgentApplyState();
  els.agentAnalyze.disabled = true;
  els.agentResponse.className = "agent-response";
  els.agentResponse.textContent = "";
  try {
    const payload = await apiJson("/api/agent/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt,
        sessionId: state.sessionId,
        objectId: state.selectedId,
      }),
    });
    const markdown = String(payload.markdown || payload.response || "").trim();
    const edits = Array.isArray(payload.edits) ? payload.edits.filter(isAgentEdit) : [];
    if (!markdown) throw new Error("The LLM backend returned an empty response.");
    state.agentSuggestion = {
      objectId: state.selectedId,
      markdown,
      edits,
    };
    els.agentResponse.innerHTML = markdownToXhtml(markdown);
    updateAgentApplyState();
  } catch (error) {
    els.agentResponse.textContent = error.message;
  } finally {
    els.agentAnalyze.disabled = false;
  }
}

function isAgentEdit(edit) {
  return (
    edit &&
    typeof edit === "object" &&
    typeof edit.objectId === "string" &&
    typeof edit.attributeId === "string" &&
    typeof edit.attributeName === "string" &&
    (typeof edit.valueMarkdown === "string" || typeof edit.valueXhtml === "string")
  );
}

function applyAgentSuggestion({ advance = false } = {}) {
  const object = state.objects[state.selectedId];
  if (!object) return;
  const edits = agentEditsForSelection();
  if (!edits.length) return;
  let applied = 0;
  edits.forEach((edit) => {
    const attr = findEditableAttribute(object, edit);
    if (!attr) return;
    const nextValue = agentEditValueForAttribute(attr, edit);
    if (attr.type === "enumeration" && !nextValue.length) return;
    if (!nextValue) return;
    updateAttribute(object.id, attr.id, nextValue);
    syncEditors(object.id, attr.id, nextValue, null);
    applied += 1;
  });
  if (!applied) {
    els.agentResponse.textContent = "The agent response did not identify an editable field on the selected requirement.";
    return;
  }
  state.agentSuggestion = null;
  updateAgentApplyState();
  setStatus(`${state.fileName} • unsaved`, true);
  renderDocument();
  renderAttributes();
  if (advance && selectNextRequirement(object.id)) {
    analyzeWithAgent();
  }
}

function applyAgentSuggestionAndNext() {
  applyAgentSuggestion({ advance: true });
}

function findEditableAttribute(object, edit) {
  const byId = object.attributes.find((attr) => attr.editable && edit.attributeId && attr.id === edit.attributeId);
  if (byId) return byId;
  const targetKey = comparableAttributeName(edit.attributeName || edit.attributeId);
  return object.attributes.find((attr) => attr.editable && comparableAttributeName(attr.name || attr.id) === targetKey);
}

function comparableAttributeName(value) {
  const key = String(value || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  if (key.includes("verification") && (key.includes("criteria") || key.includes("criterion"))) return "verificationcriteria";
  return key;
}

function enumOptionKey(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function enumEditCandidates(attr, edit) {
  if (Array.isArray(edit.valueEnum)) return edit.valueEnum.map(String);
  const candidates = [];
  const add = (value) => {
    const text = String(value || "").trim();
    if (text) candidates.push(text);
  };
  add(edit.valueMarkdown);
  add(plainText(markdownToXhtml(edit.valueMarkdown || "")));
  add(plainText(edit.valueXhtml || ""));
  if (edit.attributeId && edit.attributeId !== attr.id) add(edit.attributeId);
  return candidates
    .flatMap((candidate) => candidate.split(/[\n,;|]+/))
    .map((candidate) => candidate.trim())
    .filter(Boolean);
}

function enumValuesFromAgentEdit(attr, edit) {
  const options = attr.options || [];
  const optionsById = new Map(options.map((option) => [String(option.id), String(option.id)]));
  const optionsByKey = new Map();
  options.forEach((option) => {
    optionsByKey.set(enumOptionKey(option.id), String(option.id));
    optionsByKey.set(enumOptionKey(option.label), String(option.id));
  });
  const resolved = [];
  enumEditCandidates(attr, edit).forEach((candidate) => {
    const exact = optionsById.get(candidate);
    const normalized = optionsByKey.get(enumOptionKey(candidate));
    const optionId = exact || normalized;
    if (optionId && !resolved.includes(optionId)) resolved.push(optionId);
  });
  return attr.multiple ? resolved : resolved.slice(0, 1);
}

function agentEditValueForAttribute(attr, edit) {
  if (attr.type === "xhtml") {
    if (edit.valueMarkdown) return markdownToXhtml(edit.valueMarkdown);
    return sanitizeXhtmlFragment(edit.valueXhtml);
  }
  if (attr.type === "enumeration") return enumValuesFromAgentEdit(attr, edit);
  return plainText(markdownToXhtml(edit.valueMarkdown || edit.valueXhtml));
}

function selectNextRequirement(currentObjectId) {
  const start = state.documentOrder.indexOf(currentObjectId);
  const ordered = start >= 0 ? state.documentOrder.slice(start + 1) : state.documentOrder;
  const nextId = ordered.find((objectId) => {
    const object = state.objects[objectId];
    return object && objectKind(object) === "requirement" && reqifTextAttr(object);
  });
  if (!nextId) return null;
  selectObject(nextId);
  focusDocumentEditor(nextId);
  return nextId;
}

function focusDocumentEditor(objectId) {
  requestAnimationFrame(() => {
    const selector = `.document-item[data-object-id="${CSS.escape(objectId)}"] [data-origin="document text"]`;
    const editor = document.querySelector(selector);
    if (!editor) return;
    editor.focus();
    if (editor.classList.contains("xhtml-editor")) {
      const range = document.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
  });
}

function bindDocumentFilter(input, key) {
  input.addEventListener("change", () => {
    state.documentFilters[key] = input.checked;
    renderDocument();
  });
}

els.agentAnalyze.addEventListener("click", analyzeWithAgent);
els.agentApply.addEventListener("click", () => applyAgentSuggestion());
els.agentApplyNext.addEventListener("click", applyAgentSuggestionAndNext);
bindDocumentFilter(els.filterModified, "modified");
bindDocumentFilter(els.filterRequirements, "requirements");
bindDocumentFilter(els.filterEmptyVerificationCriteria, "emptyVerificationCriteria");
els.rightResize.addEventListener("pointerdown", (event) => {
  event.preventDefault();
  els.rightResize.setPointerCapture(event.pointerId);
  document.body.classList.add("resizing");
  const bounds = els.rightStack.getBoundingClientRect();
  const onMove = (moveEvent) => {
    const agentPercent = ((bounds.bottom - moveEvent.clientY) / bounds.height) * 100;
    setRightSplit(agentPercent);
  };
  const onUp = () => {
    document.body.classList.remove("resizing");
    els.rightResize.removeEventListener("pointermove", onMove);
    els.rightResize.removeEventListener("pointerup", onUp);
    els.rightResize.removeEventListener("pointercancel", onUp);
  };
  els.rightResize.addEventListener("pointermove", onMove);
  els.rightResize.addEventListener("pointerup", onUp);
  els.rightResize.addEventListener("pointercancel", onUp);
});
els.diffClose.addEventListener("click", closeDiffModal);
els.diffModal.addEventListener("click", (event) => {
  if (event.target === els.diffModal) closeDiffModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !els.diffModal.hidden) closeDiffModal();
});
els.historyBack.addEventListener("click", () => checkoutHistory(state.historyIndex + 1));
els.historyForward.addEventListener("click", () => checkoutHistory(state.historyIndex - 1));
