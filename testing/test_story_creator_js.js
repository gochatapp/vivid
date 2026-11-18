/*
 * Headless smoke test for web/story_creator.js.
 *
 * Runs the real page script against a tiny DOM stub, then drives the same
 * buttons a user would press (Add Character, Apply Image, Publish, Load,
 * Install) with fetch() stubbed per request. This catches runtime errors that
 * a syntax check cannot see - undefined handlers, broken wiring, wrong payloads.
 *
 * Run:  node testing/test_story_creator_js.js
 */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

let PASS = 0;
let FAIL = 0;

function check(name, condition, detail) {
  if (condition) {
    PASS += 1;
    console.log("  [PASS] " + name);
  } else {
    FAIL += 1;
    console.log("  [FAIL] " + name + "  " + (detail === undefined ? "" : detail));
  }
}

function makeClassList() {
  const set = new Set();
  return {
    add: (...names) => names.forEach(name => set.add(name)),
    remove: (...names) => names.forEach(name => set.delete(name)),
    contains: name => set.has(name)
  };
}

class StubEl {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.listeners = {};
    this.value = "";
    this.textContent = "";
    this.className = "";
    this.classList = makeClassList();
    this.disabled = false;
    this.hidden = false;
    this.type = "";
    this.files = [];
    this.style = {};
    this.attributes = {};
    this.onclick = null;
    this._innerHTML = "";
  }

  get options() {
    const out = [];
    const walk = node => node.children.forEach(child => {
      if (child.tagName === "OPTGROUP") walk(child);
      else out.push(child);
    });
    walk(this);
    return out;
  }

  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) { this._innerHTML = value; this.children = []; }

  appendChild(child) { this.children.push(child); return child; }
  append(child) { this.appendChild(child); }
  removeChild(child) { this.children = this.children.filter(c => c !== child); }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  dispatch(type, event) {
    (this.listeners[type] || []).forEach(fn => fn(event || { target: this }));
  }
  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "src") this._src = "";
  }
  setAttribute(name, value) { this.attributes[name] = value; if (name === "src") this._src = value; }
  focus() {}
  select() {}
  click() { this.dispatch("click"); }
  getContext() { return { drawImage() {} }; }
  toDataURL() { return "data:image/jpeg;base64,/9j/AAAA"; }
}

class StubImage extends StubEl {
  constructor() {
    super("img");
    this.onload = null;
  }
  set src(value) {
    this._src = value;
    if (this.onload) setTimeout(() => this.onload(), 0);
  }
  get src() { return this._src || ""; }
  get naturalWidth() { return 900; }
  get naturalHeight() { return 600; }
}

class StubFileReader {
  readAsDataURL() {
    this.result = "data:image/jpeg;base64,/9j/AAAA";
    if (this.onload) this.onload();
  }
}

const elements = new Map();
const documentStub = {
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new StubEl("div"));
    return elements.get(id);
  },
  createElement(tag) {
    return String(tag).toLowerCase() === "img" ? new StubImage() : new StubEl(tag);
  },
  addEventListener() {}
};

const requests = [];
const responseQueue = [];
let nextResponse = null;

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body)
  };
}

function fetchStub(url, options) {
  requests.push({ url: url, options: options || {} });
  if (responseQueue.length) return Promise.resolve(responseQueue.shift());
  if (!nextResponse) throw new Error("No stubbed response for " + url);
  return Promise.resolve(nextResponse);
}

const context = vm.createContext({
  document: documentStub,
  window: { addEventListener() {} },
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  location: { href: "http://localhost/story" },
  confirm: () => true,
  alert: () => {},
  Image: StubImage,
  FileReader: StubFileReader,
  fetch: fetchStub,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  console: console
});

const scriptPath = path.join(__dirname, "..", "web", "story_creator.js");
vm.runInContext(fs.readFileSync(scriptPath, "utf8"), context, {
  filename: "story_creator.js"
});

const el = id => documentStub.getElementById(id);
const tick = () => new Promise(resolve => setTimeout(resolve, 5));

const GIST_ID = "0123456789abcdef0123456789abcdef";
const GIST_URL = "https://gist.github.com/" + GIST_ID;

async function main() {
  console.log("\ninit");
  check("publish button ready", el("saveStoryBtn").disabled === false);
  check("badge starts unpublished",
        el("gistBadge").textContent === "Not published", el("gistBadge").textContent);
  check("install disabled without a gist", el("installStoryBtn").disabled === true);
  check("image target defaults to the story cover",
        el("imageTargetSel").value === "story", el("imageTargetSel").value);

  console.log("\nadd character");
  el("storyName").value = "Crystal Chronicles";
  el("storyDesc").value = "A crystal that remembers everything.";
  el("storyName").dispatch("input");
  el("charName").value = "Aiko Tanaka";
  el("charDesc").value = "A cartographer with a glass eye.";
  el("addCharBtn").click();
  check("character row rendered", el("charList").children.length === 1,
        String(el("charList").children.length));
  check("character option added", el("charSel").options.length === 2,
        String(el("charSel").options.length));
  check("image target lists the character",
        el("imageTargetSel").options.some(o => o.value === "c:0"));
  check("add reports success", el("makerMsg").className === "ok",
        el("makerMsg").textContent);

  console.log("\nadd story element");
  el("elemName").value = "Glass Spire";
  el("elemDesc").value = "The city's memory archive.";
  el("addElemBtn").click();
  check("element row rendered", el("elemList").children.length === 1,
        String(el("elemList").children.length));
  check("image target lists the element",
        el("imageTargetSel").options.some(o => o.value === "e:0"));

  console.log("\nimage assignment");
  el("applyImageBtn").click();
  check("apply without an image warns", el("makerMsg").className === "err",
        el("makerMsg").textContent);

  await context.scApplyImageBlob({ type: "image/jpeg", size: 2048 }, "uploaded");
  check("staged preview shown", el("assignImgPreview").classList.contains("show"));
  check("staged image is a data URL",
        String(el("assignImgPreview").src).startsWith("data:image/jpeg"),
        el("assignImgPreview").src);

  el("imageTargetSel").value = "story";
  el("applyImageBtn").click();
  check("story cover preview shown", el("storyImgPreview").classList.contains("show"));
  check("staging area cleared",
        el("assignImgPreview").classList.contains("show") === false);

  await context.scApplyImageBlob({ type: "image/jpeg", size: 2048 }, "pasted");
  el("imageTargetSel").value = "c:0";
  el("applyImageBtn").click();
  check("character preview shown", el("charImgPreview").classList.contains("show"));

  console.log("\npublish");
  nextResponse = response(200, {
    ok: true, gist_id: GIST_ID, html_url: GIST_URL, files: ["a", "b"]
  });
  el("saveStoryBtn").click();
  await tick();

  const saveReq = requests[requests.length - 1];
  check("posts to /api/story/save", saveReq.url === "/api/story/save", saveReq.url);
  const body = JSON.parse(saveReq.options.body);
  check("payload has the story name", body.name === "Crystal Chronicles", body.name);
  check("payload has the description",
        body.description === "A crystal that remembers everything.");
  check("payload has one character", body.characters.length === 1,
        JSON.stringify(body.characters));
  check("payload has one element", body.story_elements.length === 1,
        JSON.stringify(body.story_elements));
  check("payload carries the cover data URL",
        String(body.image.url).startsWith("data:image/jpeg"), String(body.image.url));
  check("payload carries the character data URL",
        String(body.characters[0].image.url).startsWith("data:image/jpeg"),
        String(body.characters[0].image.url));
  check("badge shows the gist", el("gistBadge").textContent.startsWith("Gist "),
        el("gistBadge").textContent);
  check("gist link shown",
        el("gistLink").hidden === false && el("gistLink").href === GIST_URL);
  check("install enabled after publishing", el("installStoryBtn").disabled === false);
  check("publish reports success", el("makerMsg").className === "ok",
        el("makerMsg").textContent);
  check("gist url copied into the loader", el("gistInput").value === GIST_URL,
        el("gistInput").value);

  console.log("\npublish failure");
  nextResponse = response(502, { ok: false, error: "ImgBB upload failed: quota" });
  el("saveStoryBtn").click();
  await tick();
  check("error surfaced",
        el("makerMsg").className === "err" &&
        el("makerMsg").textContent.includes("ImgBB"), el("makerMsg").textContent);

  console.log("\ninstall");
  nextResponse = response(200, {
    ok: true, name: "Crystal_Chronicles!" + GIST_ID, files: ["x"]
  });
  el("installStoryBtn").click();
  await tick();
  const installReq = requests[requests.length - 1];
  check("posts to /api/story/install", installReq.url === "/api/story/install",
        installReq.url);
  check("install sends the gist id",
        JSON.parse(installReq.options.body).gist_id === GIST_ID);
  check("install reports success",
        el("makerMsg").textContent.includes("Installed as"), el("makerMsg").textContent);

  console.log("\nload");
  nextResponse = response(200, {
    ok: true,
    gist_id: GIST_ID,
    html_url: GIST_URL,
    story: {
      name: "Dax and Ren",
      description: "Wounded after destroying a Yakuza drug lab.",
      image: { url: "https://i.ibb.co/cover.jpg", meta: "" },
      characters: [
        { name: "dax gunnerson", description: "Ex-special forces.",
          image: { url: "https://i.ibb.co/dax.jpg" } },
        { name: "ren takahashi", description: "A fixer.", image: { url: "" } }
      ],
      story_elements: [
        { name: "rens house", description: "A safe house.", image: { url: "" } }
      ]
    }
  });
  el("gistInput").value = GIST_ID;
  el("loadGistBtn").click();
  await tick();
  check("load hits /api/story/gist",
        requests[requests.length - 1].url.indexOf("/api/story/gist?gist_id=") === 0,
        requests[requests.length - 1].url);
  check("loaded name", el("storyName").value === "Dax and Ren", el("storyName").value);
  check("loaded description",
        el("storyDesc").value === "Wounded after destroying a Yakuza drug lab.");
  check("loaded characters", el("charList").children.length === 2,
        String(el("charList").children.length));
  check("loaded elements", el("elemList").children.length === 1,
        String(el("elemList").children.length));
  check("loaded cover preview", el("storyImgPreview").classList.contains("show"));
  check("loaded gist badge live", el("gistBadge").classList.contains("live"));
  check("load reports success",
        el("makerMsg").textContent.includes("Loaded 2 character(s)"),
        el("makerMsg").textContent);

  console.log("\nclear");
  el("clearBtn").click();
  check("form cleared",
        el("storyName").value === "" && el("charList").children.length === 0);
  check("badge reset", el("gistBadge").textContent === "Not published",
        el("gistBadge").textContent);
  check("install disabled again", el("installStoryBtn").disabled === true);

  console.log("\nai enhance + undo");
  el("charName").value = "Dax";
  el("charDesc").value = "A soldier.";
  el("addCharBtn").click();
  responseQueue.push(
    response(200, { ok: true, text: "A soldier with a haunted past." }),
    response(200, { ok: true, text: "anime soldier portrait, centered" })
  );
  const beforeEnhance = requests.length;
  el("enhanceCharBtn").click();
  await tick();
  await tick();
  const enhanceReqs = requests.slice(beforeEnhance)
    .filter(req => req.url === "/api/ai/enhance");
  check("two enhance calls", enhanceReqs.length === 2, String(enhanceReqs.length));
  const firstBody = enhanceReqs.length ? JSON.parse(enhanceReqs[0].options.body) : {};
  check("enhance sends a system prompt",
        typeof firstBody.system === "string" && firstBody.system.length > 0,
        String(firstBody.system));
  check("enhance sends the original description",
        String(firstBody.prompt).includes("A soldier."), String(firstBody.prompt));
  check("description replaced",
        el("charDesc").value === "A soldier with a haunted past.", el("charDesc").value);
  check("image prompt stored",
        el("charImagePrompt").value === "anime soldier portrait, centered",
        el("charImagePrompt").value);
  check("copy prompt enabled", el("copyCharPromptBtn").disabled === false);

  el("undoEnhanceBtn").click();
  check("undo restores the description", el("charDesc").value === "A soldier.",
        el("charDesc").value);

  console.log(`\n=== ${PASS} passed, ${FAIL} failed ===`);
  process.exit(FAIL === 0 ? 0 : 1);
}

main().catch(error => {
  console.error("\nHARNESS ERROR:", error && error.stack ? error.stack : error);
  process.exit(1);
});
