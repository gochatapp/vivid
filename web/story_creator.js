/* story_creator.js - Story Creator for Character.XI.
 *
 * Builds a story in the browser and publishes it as a public GitHub Gist in the
 * GOCHATAPP convention (GOCHATAPPSTORYTAG_<story>.md, character_<name>.txt,
 * <name>_thumb.md, storyelement_<name>.txt, thumb.md). Nothing is kept in
 * localStorage: the page holds the story in memory, the backend uploads new
 * images to ImgBB and writes the gist. */

"use strict";

const SC_IMG_SIZE = 450;
const SC_API = {
  save: "/api/story/save",
  gist: "/api/story/gist",
  install: "/api/story/install",
  imgbb: "/api/story/imgbb",
  enhance: "/api/ai/enhance"
};

const sc$ = id => document.getElementById(id);

const scEl = {
  storyName: sc$("storyName"),
  storyDesc: sc$("storyDesc"),
  storyImgPreview: sc$("storyImgPreview"),
  storyImgMeta: sc$("storyImgMeta"),
  gistBadge: sc$("gistBadge"),
  gistLink: sc$("gistLink"),
  enhanceStoryBtn: sc$("enhanceStoryBtn"),
  undoEnhanceBtn: sc$("undoEnhanceBtn"),
  charSel: sc$("charSel"),
  charName: sc$("charName"),
  charDesc: sc$("charDesc"),
  charImgPreview: sc$("charImgPreview"),
  charImgMeta: sc$("charImgMeta"),
  charList: sc$("charList"),
  charImagePrompt: sc$("charImagePrompt"),
  copyCharPromptBtn: sc$("copyCharPromptBtn"),
  addCharBtn: sc$("addCharBtn"),
  removeCharBtn: sc$("removeCharBtn"),
  enhanceCharBtn: sc$("enhanceCharBtn"),
  elemSel: sc$("elemSel"),
  elemName: sc$("elemName"),
  elemDesc: sc$("elemDesc"),
  elemImgPreview: sc$("elemImgPreview"),
  elemImgMeta: sc$("elemImgMeta"),
  elemList: sc$("elemList"),
  elemImagePrompt: sc$("elemImagePrompt"),
  copyElemPromptBtn: sc$("copyElemPromptBtn"),
  addElemBtn: sc$("addElemBtn"),
  removeElemBtn: sc$("removeElemBtn"),
  enhanceElemBtn: sc$("enhanceElemBtn"),
  imageTargetSel: sc$("imageTargetSel"),
  assignImgPreview: sc$("assignImgPreview"),
  assignImgMeta: sc$("assignImgMeta"),
  assignImageUpload: sc$("assignImageUpload"),
  uploadAssignImageBtn: sc$("uploadAssignImageBtn"),
  clearAssignImageBtn: sc$("clearAssignImageBtn"),
  applyImageBtn: sc$("applyImageBtn"),
  gistInput: sc$("gistInput"),
  loadGistBtn: sc$("loadGistBtn"),
  saveStoryBtn: sc$("saveStoryBtn"),
  installStoryBtn: sc$("installStoryBtn"),
  clearBtn: sc$("clearBtn"),
  makerMsg: sc$("makerMsg")
};

/* The story being edited. Characters/elements have no ids: the selects use the
 * array index, which is enough for a form that is published as a whole. */
const scStory = {
  gistId: "",
  gistUrl: "",
  name: "",
  description: "",
  image: { url: "", meta: "" },
  characters: [],
  storyElements: []
};

let scEditChar = -1;
let scEditElem = -1;
let scAssign = { url: "", meta: "" };
let scUndoSnapshot = null;
let scBusy = false;

/* ================= messaging / previews ================= */
function scMessage(text, cls) {
  if (!scEl.makerMsg) return;
  scEl.makerMsg.textContent = text || "";
  scEl.makerMsg.className = cls || "";
}

function scNormalizeImage(image) {
  if (!image) return { url: "", meta: "" };
  if (typeof image === "string") return { url: image, meta: "" };
  return {
    url: image.url || image.data_url || "",
    meta: image.meta || ""
  };
}

function scShowImage(imgEl, metaEl, image) {
  const normal = scNormalizeImage(image);
  if (normal.url) {
    imgEl.src = normal.url;
    imgEl.classList.add("show");
    if (metaEl) metaEl.textContent = normal.meta || "";
  } else {
    imgEl.removeAttribute("src");
    imgEl.classList.remove("show");
    if (metaEl) metaEl.textContent = "";
  }
}

function scSetBusy(busy) {
  scBusy = !!busy;
  [scEl.saveStoryBtn, scEl.loadGistBtn, scEl.enhanceStoryBtn,
   scEl.addCharBtn, scEl.removeCharBtn, scEl.enhanceCharBtn,
   scEl.addElemBtn, scEl.removeElemBtn, scEl.enhanceElemBtn,
   scEl.applyImageBtn, scEl.uploadAssignImageBtn, scEl.clearBtn].forEach(btn => {
    if (btn) btn.disabled = scBusy;
  });
  if (scEl.installStoryBtn) scEl.installStoryBtn.disabled = scBusy || !scStory.gistId;
}

/* ================= form <-> story ================= */
function scSyncForm() {
  scStory.name = (scEl.storyName.value || "").trim();
  scStory.description = (scEl.storyDesc.value || "").trim();
}

function scUpdateGistBadge() {
  if (scStory.gistId) {
    scEl.gistBadge.textContent = "Gist " + scStory.gistId.slice(0, 10) + "...";
    scEl.gistBadge.classList.add("live");
  } else {
    scEl.gistBadge.textContent = "Not published";
    scEl.gistBadge.classList.remove("live");
  }
  if (scStory.gistUrl) {
    scEl.gistLink.href = scStory.gistUrl;
    scEl.gistLink.textContent = "Open gist: " + scStory.gistUrl;
    scEl.gistLink.hidden = false;
  } else {
    scEl.gistLink.removeAttribute("href");
    scEl.gistLink.textContent = "";
    scEl.gistLink.hidden = true;
  }
  if (scEl.installStoryBtn) scEl.installStoryBtn.disabled = scBusy || !scStory.gistId;
}

function scLoadStoryIntoForm() {
  scEl.storyName.value = scStory.name || "";
  scEl.storyDesc.value = scStory.description || "";
  scShowImage(scEl.storyImgPreview, scEl.storyImgMeta, scStory.image);
  scRefreshCharSelects();
  scRefreshElemSelects();
  scRefreshImageTargetSelect();
  scRenderLists();
  scUpdateGistBadge();
}

function scRefreshCharSelects() {
  const sel = scEl.charSel;
  sel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "Select or create a character...";
  sel.appendChild(blank);
  scStory.characters.forEach((char, index) => {
    const opt = document.createElement("option");
    opt.value = String(index);
    opt.textContent = char.name || "Unnamed";
    sel.appendChild(opt);
  });
  sel.value = scEditChar >= 0 && scEditChar < scStory.characters.length
    ? String(scEditChar) : "";
}

function scRefreshElemSelects() {
  const sel = scEl.elemSel;
  sel.innerHTML = "";
  const blank = document.createElement("option");
  blank.value = "";
  blank.textContent = "Select or create a story element...";
  sel.appendChild(blank);
  scStory.storyElements.forEach((elem, index) => {
    const opt = document.createElement("option");
    opt.value = String(index);
    opt.textContent = elem.name || "Unnamed";
    sel.appendChild(opt);
  });
  sel.value = scEditElem >= 0 && scEditElem < scStory.storyElements.length
    ? String(scEditElem) : "";
}

function scTargetOption(value, label) {
  const opt = document.createElement("option");
  opt.value = value;
  opt.textContent = label || "Unnamed";
  return opt;
}

function scRefreshImageTargetSelect() {
  const sel = scEl.imageTargetSel;
  const previous = sel.value;
  sel.innerHTML = "";
  sel.appendChild(scTargetOption("story", "Story cover"));

  const charGroup = document.createElement("optgroup");
  charGroup.label = "Characters";
  scStory.characters.forEach((char, index) => {
    charGroup.appendChild(scTargetOption("c:" + index, char.name));
  });
  sel.appendChild(charGroup);

  const elemGroup = document.createElement("optgroup");
  elemGroup.label = "Story Elements";
  scStory.storyElements.forEach((elem, index) => {
    elemGroup.appendChild(scTargetOption("e:" + index, elem.name));
  });
  sel.appendChild(elemGroup);

  const stillThere = previous && Array.from(sel.options).some(o => o.value === previous);
  sel.value = stillThere ? previous : "story";
}

function scChildRow(entry, index, onEdit, onDelete) {
  const item = document.createElement("div");
  item.className = "storyChildItem";

  const img = document.createElement("img");
  img.alt = "";
  img.src = scNormalizeImage(entry.image).url ||
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='40' height='40'%3E%3C/svg%3E";

  const info = document.createElement("div");
  info.className = "storyChildInfo";
  const nameDiv = document.createElement("div");
  nameDiv.className = "storyChildName";
  nameDiv.textContent = entry.name || "Unnamed";
  const descDiv = document.createElement("div");
  descDiv.className = "storyChildDesc";
  const text = entry.description || "No description";
  descDiv.textContent = text.length > 60 ? text.slice(0, 60) + "..." : text;
  info.appendChild(nameDiv);
  info.appendChild(descDiv);

  const actions = document.createElement("div");
  actions.className = "storyChildActions";
  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "btn ghost sm";
  editBtn.textContent = "Edit";
  editBtn.onclick = () => onEdit(index);
  const delBtn = document.createElement("button");
  delBtn.type = "button";
  delBtn.className = "btn ghost sm danger";
  delBtn.textContent = "Delete";
  delBtn.onclick = () => onDelete(index);
  actions.appendChild(editBtn);
  actions.appendChild(delBtn);

  item.appendChild(img);
  item.appendChild(info);
  item.appendChild(actions);
  return item;
}

function scRenderLists() {
  const charList = scEl.charList;
  const elemList = scEl.elemList;
  if (!charList || !elemList) return;

  charList.innerHTML = "";
  scStory.characters.forEach((char, index) => {
    charList.appendChild(scChildRow(char, index, scSelectCharacter, scDeleteCharacter));
  });

  elemList.innerHTML = "";
  scStory.storyElements.forEach((elem, index) => {
    elemList.appendChild(scChildRow(elem, index, scSelectElement, scDeleteElement));
  });
}

/* ================= characters ================= */
function scClearCharForm() {
  scEl.charName.value = "";
  scEl.charDesc.value = "";
  scEl.charImagePrompt.value = "";
  scEl.copyCharPromptBtn.disabled = true;
  scShowImage(scEl.charImgPreview, scEl.charImgMeta, null);
}

function scSelectCharacter(index) {
  if (index < 0 || index >= scStory.characters.length) {
    scEditChar = -1;
    scEl.charSel.value = "";
    return;
  }
  scEditChar = index;
  const char = scStory.characters[index];
  scEl.charName.value = char.name || "";
  scEl.charDesc.value = char.description || "";
  scShowImage(scEl.charImgPreview, scEl.charImgMeta, char.image);
  scEl.charImagePrompt.value = "";
  scEl.copyCharPromptBtn.disabled = true;
  scEl.charSel.value = String(index);
}

function scSaveCharacter() {
  const name = (scEl.charName.value || "").trim();
  const description = (scEl.charDesc.value || "").trim();
  if (!name) {
    scMessage("Enter a character name first.", "err");
    scEl.charName.focus();
    return;
  }
  if (!description) {
    scMessage("Enter a character description first.", "err");
    scEl.charDesc.focus();
    return;
  }

  const isEdit = scEditChar >= 0 && scEditChar < scStory.characters.length;
  const existing = isEdit ? scStory.characters[scEditChar] : null;
  const entry = {
    name: name,
    description: description,
    image: scNormalizeImage(existing && existing.image)
  };
  if (isEdit) {
    scStory.characters[scEditChar] = entry;
  } else {
    scStory.characters.push(entry);
    scEditChar = scStory.characters.length - 1;
  }

  scSyncForm();
  scRefreshCharSelects();
  scRefreshImageTargetSelect();
  scRenderLists();
  scShowImage(scEl.charImgPreview, scEl.charImgMeta, entry.image);
  scMessage((isEdit ? "Updated character: " : "Added character: ") + name, "ok");
}

function scDeleteCharacter(index) {
  const char = scStory.characters[index];
  if (!char) return;
  if (!confirm("Delete character '" + (char.name || "Unnamed") + "'?")) return;

  scStory.characters.splice(index, 1);
  if (scEditChar === index) {
    scEditChar = -1;
    scClearCharForm();
  } else if (scEditChar > index) {
    scEditChar -= 1;
  }
  scRefreshCharSelects();
  scRefreshImageTargetSelect();
  scRenderLists();
  scMessage("Character removed.", "ok");
}

/* ================= story elements ================= */
function scClearElemForm() {
  scEl.elemName.value = "";
  scEl.elemDesc.value = "";
  scEl.elemImagePrompt.value = "";
  scEl.copyElemPromptBtn.disabled = true;
  scShowImage(scEl.elemImgPreview, scEl.elemImgMeta, null);
}

function scSelectElement(index) {
  if (index < 0 || index >= scStory.storyElements.length) {
    scEditElem = -1;
    scEl.elemSel.value = "";
    return;
  }
  scEditElem = index;
  const elem = scStory.storyElements[index];
  scEl.elemName.value = elem.name || "";
  scEl.elemDesc.value = elem.description || "";
  scShowImage(scEl.elemImgPreview, scEl.elemImgMeta, elem.image);
  scEl.elemImagePrompt.value = "";
  scEl.copyElemPromptBtn.disabled = true;
  scEl.elemSel.value = String(index);
}

function scSaveElement() {
  const name = (scEl.elemName.value || "").trim();
  const description = (scEl.elemDesc.value || "").trim();
  if (!name) {
    scMessage("Enter an element name first.", "err");
    scEl.elemName.focus();
    return;
  }
  if (!description) {
    scMessage("Enter an element description first.", "err");
    scEl.elemDesc.focus();
    return;
  }

  const isEdit = scEditElem >= 0 && scEditElem < scStory.storyElements.length;
  const existing = isEdit ? scStory.storyElements[scEditElem] : null;
  const entry = {
    name: name,
    description: description,
    image: scNormalizeImage(existing && existing.image)
  };
  if (isEdit) {
    scStory.storyElements[scEditElem] = entry;
  } else {
    scStory.storyElements.push(entry);
    scEditElem = scStory.storyElements.length - 1;
  }

  scSyncForm();
  scRefreshElemSelects();
  scRefreshImageTargetSelect();
  scRenderLists();
  scShowImage(scEl.elemImgPreview, scEl.elemImgMeta, entry.image);
  scMessage((isEdit ? "Updated element: " : "Added element: ") + name, "ok");
}

function scDeleteElement(index) {
  const elem = scStory.storyElements[index];
  if (!elem) return;
  if (!confirm("Delete element '" + (elem.name || "Unnamed") + "'?")) return;

  scStory.storyElements.splice(index, 1);
  if (scEditElem === index) {
    scEditElem = -1;
    scClearElemForm();
  } else if (scEditElem > index) {
    scEditElem -= 1;
  }
  scRefreshElemSelects();
  scRefreshImageTargetSelect();
  scRenderLists();
  scMessage("Element removed.", "ok");
}


/* ================= image assignment ================= */
function scClearAssign() {
  scAssign = { url: "", meta: "" };
  scShowImage(scEl.assignImgPreview, scEl.assignImgMeta, null);
  scMessage("");
}

function scApplyToTarget() {
  const value = scEl.imageTargetSel.value;
  if (!value) {
    scMessage("Select a target first.", "err");
    return;
  }
  if (!scAssign.url) {
    scMessage("Upload or paste an image first.", "err");
    return;
  }

  const image = { url: scAssign.url, meta: scAssign.meta };
  if (value === "story") {
    scStory.image = image;
    scShowImage(scEl.storyImgPreview, scEl.storyImgMeta, image);
    scMessage("Image applied to the story cover.", "ok");
  } else if (value.startsWith("c:")) {
    const index = Number(value.slice(2));
    const char = scStory.characters[index];
    if (!char) {
      scMessage("That character no longer exists.", "err");
      return;
    }
    char.image = image;
    if (index === scEditChar) {
      scShowImage(scEl.charImgPreview, scEl.charImgMeta, image);
    }
    scMessage("Image applied to character: " + (char.name || "Unnamed"), "ok");
  } else if (value.startsWith("e:")) {
    const index = Number(value.slice(2));
    const elem = scStory.storyElements[index];
    if (!elem) {
      scMessage("That story element no longer exists.", "err");
      return;
    }
    elem.image = image;
    if (index === scEditElem) {
      scShowImage(scEl.elemImgPreview, scEl.elemImgMeta, image);
    }
    scMessage("Image applied to element: " + (elem.name || "Unnamed"), "ok");
  }

  scAssign = { url: "", meta: "" };
  scShowImage(scEl.assignImgPreview, scEl.assignImgMeta, null);
  scRenderLists();
}

/* ================= image upload / paste (always 450x450) ================= */
function scFileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error("Could not read that image."));
    reader.readAsDataURL(file);
  });
}

function scLoadImage(dataUrl) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("That file is not a valid image."));
    img.src = dataUrl;
  });
}

function scResizeToSquare(image) {
  const width = image.naturalWidth || image.width;
  const height = image.naturalHeight || image.height;
  const side = Math.min(width, height);
  const sx = Math.max(0, (width - side) / 2);
  const sy = Math.max(0, (height - side) / 2);
  const canvas = document.createElement("canvas");
  canvas.width = SC_IMG_SIZE;
  canvas.height = SC_IMG_SIZE;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(image, sx, sy, side, side, 0, 0, SC_IMG_SIZE, SC_IMG_SIZE);
  return canvas;
}

async function scApplyImageBlob(blob, sourceLabel) {
  if (!blob) return;
  const type = blob.type || "";
  if (type && type.indexOf("image/") !== 0) {
    scMessage("That is not an image file.", "err");
    return;
  }
  if (blob.size > 8 * 1024 * 1024) {
    scMessage("That image is over 8 MB - pick a smaller one.", "err");
    return;
  }
  try {
    const dataUrl = await scFileToDataUrl(blob);
    const image = await scLoadImage(dataUrl);
    const canvas = scResizeToSquare(image);
    const out = canvas.toDataURL("image/jpeg", 0.85);
    const kb = Math.round((out.length * 3) / 4 / 1024);
    const meta = SC_IMG_SIZE + "x" + SC_IMG_SIZE + " - JPEG ~" + kb + " KB - " + sourceLabel;
    scAssign = { url: out, meta: meta };
    scShowImage(scEl.assignImgPreview, scEl.assignImgMeta, scAssign);
    scMessage("Image ready. Pick a target, then Apply to Selected.", "ok");
  } catch (e) {
    scMessage("Could not use that image: " + e.message, "err");
  }
}

/* ================= AI Enhance (proxied through the backend) ================= */
async function scCallAi(system, prompt) {
  const res = await fetch(SC_API.enhance, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ system: system, prompt: prompt })
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) {
    throw new Error((data && data.error) || ("HTTP " + res.status));
  }
  const text = String(data.text || "").trim();
  if (!text) throw new Error("The model returned an empty reply.");
  return text;
}

function scEnhanceTargets(kind) {
  if (kind === "character") {
    return {
      label: "character",
      desc: scEl.charDesc,
      name: scEl.charName,
      promptField: scEl.charImagePrompt,
      copyBtn: scEl.copyCharPromptBtn,
      button: scEl.enhanceCharBtn
    };
  }
  if (kind === "element") {
    return {
      label: "story element",
      desc: scEl.elemDesc,
      name: scEl.elemName,
      promptField: scEl.elemImagePrompt,
      copyBtn: scEl.copyElemPromptBtn,
      button: scEl.enhanceElemBtn
    };
  }
  return {
    label: "story",
    desc: scEl.storyDesc,
    name: scEl.storyName,
    promptField: null,
    copyBtn: null,
    button: scEl.enhanceStoryBtn
  };
}

async function scEnhance(kind) {
  if (scBusy) return;
  const target = scEnhanceTargets(kind);
  const original = (target.desc.value || "").trim();
  if (!original) {
    scMessage("Write a " + target.label + " description first, then AI Enhance.", "err");
    target.desc.focus();
    return;
  }
  const displayName = (target.name.value || "").trim() ||
    ("Unnamed " + target.label);

  const button = target.button;
  const originalLabel = button ? button.textContent : "";
  scUndoSnapshot = {
    story: scEl.storyDesc.value,
    character: scEl.charDesc.value,
    element: scEl.elemDesc.value
  };
  if (target.promptField) target.promptField.value = "";
  if (target.copyBtn) target.copyBtn.disabled = true;

  scSetBusy(true);
  if (button) button.textContent = "Enhancing...";
  scMessage("Enhancing " + target.label + "...");

  try {
    const enhanced = await scCallAi(
      "You write a " + target.label + " description for a story. Make things up to fill every field.\n" +
      "Keep the structure of the original, use clear headings and fields, and expand thin sections.\n" +
      "Invent plausible details for every missing field so the " + target.label + " feels complete. " +
      "Never leave a field blank and never write 'Not specified', 'Unknown', or similar filler.\n" +
      "Keep it concise and directly usable. No preamble, no commentary.",
      target.label.charAt(0).toUpperCase() + target.label.slice(1) + " name: " + displayName +
      "\n\nORIGINAL DESCRIPTION:\n" + original
    );

    let imagePrompt = "";
    try {
      imagePrompt = await scCallAi(
        "You write a single detailed Perchance AI text-to-image prompt from a " +
        target.label + " description.\n" +
        "Use only appearance/visual details that can reasonably be gleaned from the description; " +
        "where details are missing, choose simple, neutral, attractive defaults.\n" +
        "Always write a centered composition, drawn-anime style, square aspect ratio, " +
        "clean background, soft lighting.\n" +
        "One paragraph only. No preamble, no explanation, no quotes, no bullet points.",
        target.label.charAt(0).toUpperCase() + target.label.slice(1) +
        " description:\n" + enhanced
      );
    } catch (promptError) {
      imagePrompt = "";
    }

    if (target.promptField) target.promptField.value = imagePrompt;
    if (target.copyBtn) target.copyBtn.disabled = !imagePrompt;
    target.desc.value = enhanced;
    scSyncForm();
    scMessage("AI Enhance complete. Review the description, then publish.", "ok");
  } catch (e) {
    scMessage("AI Enhance failed: " + e.message, "err");
  } finally {
    scSetBusy(false);
    if (button) button.textContent = originalLabel || "AI Enhance";
  }
}

function scUndoEnhance() {
  if (!scUndoSnapshot) {
    scMessage("Nothing to undo - run AI Enhance first.", "err");
    return;
  }
  scEl.storyDesc.value = scUndoSnapshot.story || "";
  scEl.charDesc.value = scUndoSnapshot.character || "";
  scEl.elemDesc.value = scUndoSnapshot.element || "";
  scUndoSnapshot = null;
  scSyncForm();
  scMessage("Undone - descriptions restored to before AI Enhance.", "ok");
}

function scCopyPrompt(field, okText, errText) {
  const text = (field.value || "").trim();
  if (!text) {
    scMessage(errText, "err");
    return;
  }
  navigator.clipboard.writeText(text).then(
    () => scMessage(okText, "ok"),
    () => {
      field.hidden = false;
      field.select();
      scMessage("Copy blocked - the prompt is selected, press Ctrl+C.", "err");
    }
  );
}


/* ================= publish / load / install ================= */
function scSyncOpenEditors() {
  /* Publish what is currently typed: an edited entry is updated in place, and a
   * completely new draft is added so nothing is silently dropped. */
  const charName = (scEl.charName.value || "").trim();
  const charDesc = (scEl.charDesc.value || "").trim();
  if (charName && charDesc) {
    if (scEditChar >= 0 && scStory.characters[scEditChar]) {
      scStory.characters[scEditChar].name = charName;
      scStory.characters[scEditChar].description = charDesc;
    } else if (!scStory.characters.some(c => c.name === charName)) {
      scStory.characters.push({
        name: charName, description: charDesc, image: { url: "", meta: "" }
      });
    }
  }

  const elemName = (scEl.elemName.value || "").trim();
  const elemDesc = (scEl.elemDesc.value || "").trim();
  if (elemName && elemDesc) {
    if (scEditElem >= 0 && scStory.storyElements[scEditElem]) {
      scStory.storyElements[scEditElem].name = elemName;
      scStory.storyElements[scEditElem].description = elemDesc;
    } else if (!scStory.storyElements.some(e => e.name === elemName)) {
      scStory.storyElements.push({
        name: elemName, description: elemDesc, image: { url: "", meta: "" }
      });
    }
  }
}

function scStoryPayload() {
  scSyncForm();
  scSyncOpenEditors();
  return {
    name: scStory.name,
    description: scStory.description,
    image: scStory.image,
    characters: scStory.characters.map(char => ({
      name: char.name,
      description: char.description,
      image: scNormalizeImage(char.image)
    })),
    story_elements: scStory.storyElements.map(elem => ({
      name: elem.name,
      description: elem.description,
      image: scNormalizeImage(elem.image)
    }))
  };
}

async function scPublishStory() {
  if (scBusy) return;
  const payload = scStoryPayload();
  if (!payload.name) {
    scMessage("Enter a story name first.", "err");
    scEl.storyName.focus();
    return;
  }

  scSetBusy(true);
  scEl.saveStoryBtn.textContent = "Publishing...";
  scMessage("Uploading images and creating the gist...");
  try {
    const res = await fetch(SC_API.save, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error((data && data.error) || ("HTTP " + res.status));
    }
    scStory.gistId = data.gist_id || "";
    scStory.gistUrl = data.html_url || "";
    scEl.gistInput.value = scStory.gistUrl || scStory.gistId;
    scUpdateGistBadge();
    scMessage(
      "Published " + (data.files || []).length + " files to " +
      (scStory.gistUrl || scStory.gistId) +
      ". Publishing again creates a new gist.",
      "ok"
    );
  } catch (e) {
    scMessage("Publish failed: " + e.message, "err");
  } finally {
    scEl.saveStoryBtn.textContent = "Publish to Gist";
    scSetBusy(false);
  }
}

async function scLoadStoryGist() {
  if (scBusy) return;
  const raw = (scEl.gistInput.value || "").trim();
  if (!raw) {
    scMessage("Paste a gist URL or gist id first.", "err");
    scEl.gistInput.focus();
    return;
  }

  scSetBusy(true);
  scMessage("Loading gist...");
  try {
    const res = await fetch(SC_API.gist + "?gist_id=" + encodeURIComponent(raw));
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error((data && data.error) || ("HTTP " + res.status));
    }
    const story = data.story || {};
    scStory.gistId = data.gist_id || "";
    scStory.gistUrl = data.html_url || "";
    scStory.name = story.name || "";
    scStory.description = story.description || "";
    scStory.image = scNormalizeImage(story.image);
    scStory.characters = (story.characters || []).map(char => ({
      name: char.name || "",
      description: char.description || "",
      image: scNormalizeImage(char.image)
    }));
    scStory.storyElements = (story.story_elements || []).map(elem => ({
      name: elem.name || "",
      description: elem.description || "",
      image: scNormalizeImage(elem.image)
    }));
    scEditChar = -1;
    scEditElem = -1;
    scClearCharForm();
    scClearElemForm();
    scLoadStoryIntoForm();
    scMessage(
      "Loaded " + scStory.characters.length + " character(s) and " +
      scStory.storyElements.length + " element(s). Publishing creates a new gist.",
      "ok"
    );
  } catch (e) {
    scMessage("Load failed: " + e.message, "err");
  } finally {
    scSetBusy(false);
  }
}

async function scInstallStory() {
  if (scBusy) return;
  if (!scStory.gistId) {
    scMessage("Publish or load a gist first.", "err");
    return;
  }

  scSetBusy(true);
  scMessage("Installing locally...");
  try {
    const res = await fetch(SC_API.install, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gist_id: scStory.gistId })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error((data && data.error) || ("HTTP " + res.status));
    }
    scMessage(
      "Installed as " + data.name + " (" + (data.files || []).length +
      " files). Select the story in chat to start using it.",
      "ok"
    );
  } catch (e) {
    scMessage("Install failed: " + e.message, "err");
  } finally {
    scSetBusy(false);
  }
}

function scClearForm() {
  if (scBusy) return;
  if (!confirm("Clear the whole form? The published gist is not affected.")) return;

  scStory.gistId = "";
  scStory.gistUrl = "";
  scStory.name = "";
  scStory.description = "";
  scStory.image = { url: "", meta: "" };
  scStory.characters = [];
  scStory.storyElements = [];
  scEditChar = -1;
  scEditElem = -1;
  scAssign = { url: "", meta: "" };
  scUndoSnapshot = null;

  scEl.storyName.value = "";
  scEl.storyDesc.value = "";
  scEl.gistInput.value = "";
  scEl.charImagePrompt.value = "";
  scEl.elemImagePrompt.value = "";
  scClearCharForm();
  scClearElemForm();
  scShowImage(scEl.storyImgPreview, scEl.storyImgMeta, null);
  scShowImage(scEl.assignImgPreview, scEl.assignImgMeta, null);
  scLoadStoryIntoForm();
  scMessage("Form cleared.", "ok");
}


/* ================= wiring ================= */
function scInit() {
  scRefreshCharSelects();
  scRefreshElemSelects();
  scRefreshImageTargetSelect();
  scRenderLists();
  scUpdateGistBadge();

  scEl.storyName.addEventListener("input", scSyncForm);
  scEl.storyDesc.addEventListener("input", scSyncForm);

  scEl.charSel.addEventListener("change", () => {
    if (scEl.charSel.value === "") {
      scEditChar = -1;
      scClearCharForm();
      return;
    }
    scSelectCharacter(Number(scEl.charSel.value));
  });

  scEl.elemSel.addEventListener("change", () => {
    if (scEl.elemSel.value === "") {
      scEditElem = -1;
      scClearElemForm();
      return;
    }
    scSelectElement(Number(scEl.elemSel.value));
  });

  scEl.enhanceStoryBtn.addEventListener("click", () => scEnhance("story"));
  scEl.enhanceCharBtn.addEventListener("click", () => scEnhance("character"));
  scEl.enhanceElemBtn.addEventListener("click", () => scEnhance("element"));
  scEl.undoEnhanceBtn.addEventListener("click", scUndoEnhance);

  scEl.copyCharPromptBtn.addEventListener("click", () => scCopyPrompt(
    scEl.charImagePrompt,
    "Character image prompt copied.",
    "No character image prompt yet - run AI Enhance on a character first."
  ));
  scEl.copyElemPromptBtn.addEventListener("click", () => scCopyPrompt(
    scEl.elemImagePrompt,
    "Element image prompt copied.",
    "No element image prompt yet - run AI Enhance on an element first."
  ));

  scEl.addCharBtn.addEventListener("click", scSaveCharacter);
  scEl.removeCharBtn.addEventListener("click", () => {
    if (scEditChar < 0) {
      scMessage("Select a character to remove.", "err");
      return;
    }
    scDeleteCharacter(scEditChar);
  });
  scEl.addElemBtn.addEventListener("click", scSaveElement);
  scEl.removeElemBtn.addEventListener("click", () => {
    if (scEditElem < 0) {
      scMessage("Select an element to remove.", "err");
      return;
    }
    scDeleteElement(scEditElem);
  });

  scEl.uploadAssignImageBtn.addEventListener("click", () => {
    scEl.assignImageUpload.click();
  });
  scEl.assignImageUpload.addEventListener("change", event => {
    const file = event.target.files && event.target.files[0];
    if (file) scApplyImageBlob(file, "uploaded");
    event.target.value = "";
  });
  scEl.clearAssignImageBtn.addEventListener("click", scClearAssign);
  scEl.applyImageBtn.addEventListener("click", scApplyToTarget);

  scEl.loadGistBtn.addEventListener("click", scLoadStoryGist);
  scEl.gistInput.addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      scLoadStoryGist();
    }
  });
  scEl.saveStoryBtn.addEventListener("click", scPublishStory);
  scEl.installStoryBtn.addEventListener("click", scInstallStory);
  scEl.clearBtn.addEventListener("click", scClearForm);

  // Ctrl+V anywhere on the page stages a pasted image for Section 4.
  document.addEventListener("paste", event => {
    const items = (event.clipboardData && event.clipboardData.items) || [];
    for (const item of items) {
      if ((item.type || "").indexOf("image/") === 0) {
        const file = item.getAsFile();
        if (file) {
          event.preventDefault();
          scApplyImageBlob(file, "pasted");
          return;
        }
      }
    }
  });
}

scInit();
