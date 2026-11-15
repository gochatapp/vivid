// Regression test: web/role_maker.html must detect an animated WebP from raw
// bytes so the preview can play it instead of canvas-flattening it, report
// ".webp" as the save extension only for animated files, and accept an animated
// WebP only when it is already 450x450 (it can't be resized without losing
// frames).
//
// The page keeps these helpers inline, so this test extracts them straight from
// the HTML and exercises them against synthetic RIFF/WEBP buffers.
//
// Run:  node testing/test_webp_detect.js

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "web", "role_maker.html"), "utf8");

let PASS = 0;
let FAIL = 0;

function check(name, condition, detail) {
  if (condition) {
    PASS++;
    console.log(`  [PASS] ${name}`);
  } else {
    FAIL++;
    console.log(`  [FAIL] ${name}  ${detail || ""}`);
  }
}

// Pull a top-level `function name(...) { ... }` out of the page source.
function extractFunction(name) {
  const start = html.indexOf("function " + name + "(");
  if (start < 0) throw new Error("missing function " + name);
  const brace = html.indexOf("{", start);
  let depth = 0;
  for (let i = brace; i < html.length; i++) {
    if (html[i] === "{") depth++;
    else if (html[i] === "}") {
      depth--;
      if (depth === 0) return html.slice(start, i + 1);
    }
  }
  throw new Error("unbalanced function " + name);
}

const source = extractFunction("isAnimatedWebp") + "\n" +
  extractFunction("imageExt") + "\n" + extractFunction("isRoleImageSize");
// imageExt() reads the page-level `animatedImage` flag; expose a setter so the
// extracted copy can be driven the same way the page drives it.
eval(source + "\n;globalThis.__isAnimatedWebp = isAnimatedWebp;" +
  "globalThis.__imageExtFor = (a) => { animatedImage = a; return imageExt(); };" +
  "globalThis.__isRoleImageSize = isRoleImageSize;");

function u32(n) {
  const b = Buffer.alloc(4);
  b.writeUInt32LE(n, 0);
  return b;
}

function rifWebp(chunkFourCC) {
  const parts = [Buffer.from("RIFF", "ascii"), u32(chunkFourCC ? 0 : 0), Buffer.from("WEBP", "ascii")];
  if (chunkFourCC) parts.push(Buffer.from(chunkFourCC, "ascii"), u32(0));
  return Buffer.concat(parts);
}

function bufferToArrayBuffer(buf) {
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

console.log("\nisAnimatedWebp");

const animated = rifWebp("ANMF");
check("animated WebP (has ANMF) -> true", __isAnimatedWebp(bufferToArrayBuffer(animated)) === true);

const staticVp8 = rifWebp("VP8 ");
check("static WebP (VP8 only) -> false", __isAnimatedWebp(bufferToArrayBuffer(staticVp8)) === false);

const staticVp8l = rifWebp("VP8L");
check("static lossless WebP (VP8L) -> false", __isAnimatedWebp(bufferToArrayBuffer(staticVp8l)) === false);

const png = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13]);
check("PNG bytes -> false", __isAnimatedWebp(bufferToArrayBuffer(png)) === false);

check("shorter than 12 bytes -> false", __isAnimatedWebp(bufferToArrayBuffer(Buffer.from("RIFF"))) === false);
check("random bytes -> false", __isAnimatedWebp(bufferToArrayBuffer(Buffer.from([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]))) === false);

// A plain "ANMF" that is not inside a RIFF/WEBP container must not match.
const notWebpWithAnmf = Buffer.concat([Buffer.from("XXXX", "ascii"), u32(0), Buffer.from("ANMF", "ascii")]);
check("ANMF outside RIFF/WEBP -> false", __isAnimatedWebp(bufferToArrayBuffer(notWebpWithAnmf)) === false);

console.log("\nimageExt");

check("animated image saves as .webp", __imageExtFor(true) === ".webp");
check("static image saves as .jpg", __imageExtFor(false) === ".jpg");

console.log("\nisRoleImageSize");

check("450x450 accepted", __isRoleImageSize(450, 450) === true);
check("449x450 rejected", __isRoleImageSize(449, 450) === false);
check("450x451 rejected", __isRoleImageSize(450, 451) === false);
check("512x512 rejected", __isRoleImageSize(512, 512) === false);

console.log(`\n=== ${PASS} passed, ${FAIL} failed ===`);
process.exit(FAIL === 0 ? 0 : 1);
