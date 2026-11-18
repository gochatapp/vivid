"""
Regression test: the Persona Maker backend (/api/personas and
/api/persona_templates) plus the /persona_maker page.

Runs the real aiohttp endpoints against temporary persona/template
directories, so nothing in the project is touched. It also asserts the copied
web assets (persona_maker.html and web/persona_templates/*) are present.

Run:  python testing/test_personas_api.py
"""

import asyncio
import base64
import sys
import tempfile
from pathlib import Path
from unittest import mock

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import system.web_server as ws  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

PASS = 0
FAIL = 0

JPEG_PAYLOAD = b"\xff\xd8\xff\xe0FAKEJPEGDATA"


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


async def run():
    # --- Copied assets ------------------------------------------------------
    web_templates = ROOT / "web" / "persona_templates"
    print("\ncopied assets")
    check("persona_maker.html copied", (ROOT / "web" / "persona_maker.html").exists())
    check("persona_templates dir copied", web_templates.is_dir())
    check("basic_user_template.txt copied",
          (web_templates / "basic_user_template.txt").exists())
    check("user_persona_template.txt copied",
          (web_templates / "user_persona_template.txt").exists())

    with tempfile.TemporaryDirectory() as tmp:
        personas_dir = Path(tmp) / "personas"
        builtin_tpl_dir = Path(tmp) / "builtin_templates"
        user_tpl_dir = Path(tmp) / "user_templates"
        personas_dir.mkdir()
        builtin_tpl_dir.mkdir()
        user_tpl_dir.mkdir()
        (builtin_tpl_dir / "sample_template.txt").write_text(
            "SAMPLE TEMPLATE", encoding="utf-8"
        )
        (user_tpl_dir / "sample_template.txt").write_text(
            "USER OVERRIDE", encoding="utf-8"
        )
        (user_tpl_dir / "extra_template.txt").write_text(
            "EXTRA TEMPLATE", encoding="utf-8"
        )

        with mock.patch.object(ws, "CHAT_PERSONAS_DIR", personas_dir), \
             mock.patch.object(ws, "PERSONA_TEMPLATES_DIR", builtin_tpl_dir), \
             mock.patch.object(ws, "USER_PERSONA_TEMPLATES_DIR", user_tpl_dir):
            app = ws.create_web_app()
            async with TestClient(TestServer(app)) as client:
                # --- Page route --------------------------------------------
                print("\nGET /persona_maker")
                resp = await client.get("/persona_maker")
                check("status 200", resp.status == 200, f"got {resp.status}")
                body = await resp.text()
                check("serves persona maker", "Persona Maker" in body)

                print("\nGET /persona (alias)")
                resp = await client.get("/persona")
                check("status 200", resp.status == 200, f"got {resp.status}")

                print("\nGET /index.html (relative back link)")
                resp = await client.get("/index.html")
                check("status 200", resp.status == 200, f"got {resp.status}")

                # --- Empty list --------------------------------------------
                print("\nGET /api/personas?names_only=1 (empty)")
                resp = await client.get("/api/personas?names_only=1")
                data = await resp.json()
                check("status 200", resp.status == 200)
                check("empty list", data.get("personas") == [], data)

                # --- Save --------------------------------------------------
                print("\nPOST /api/personas")
                resp = await client.post("/api/personas", json={
                    "name": "Aiko",
                    "text": "You are Aiko, a cheerful guide.",
                    "image_desc": "anime girl, cherry blossoms",
                    "image_jpeg": base64.b64encode(JPEG_PAYLOAD).decode("ascii"),
                })
                check("status 200", resp.status == 200, f"got {resp.status}")
                data = await resp.json()
                check("status saved", data.get("status") == "saved", data)
                check("name echoed", data.get("name") == "Aiko", data)
                check("txt written",
                      (personas_dir / "Aiko.txt").read_text(encoding="utf-8")
                      == "You are Aiko, a cheerful guide.")
                check("image_desc sidecar written",
                      (personas_dir / "Aiko.image_desc").read_text(encoding="utf-8")
                      == "anime girl, cherry blossoms")
                check("image written",
                      (personas_dir / "Aiko.jpg").read_bytes() == JPEG_PAYLOAD)

                # --- Load --------------------------------------------------
                print("\nGET /api/personas?name=Aiko")
                resp = await client.get("/api/personas?name=Aiko")
                data = await resp.json()
                check("status 200", resp.status == 200)
                check("text round-trips",
                      data.get("text") == "You are Aiko, a cheerful guide.")
                check("image_desc round-trips",
                      data.get("image_desc") == "anime girl, cherry blossoms")

                # --- List now has Aiko -------------------------------------
                print("\nGET /api/personas?names_only=1 (one entry)")
                resp = await client.get("/api/personas?names_only=1")
                data = await resp.json()
                check("list contains Aiko", "Aiko" in data.get("personas", []))

                # --- Re-save overwrites (Go parity) ------------------------
                print("\nPOST /api/personas (re-save clears omitted media)")
                resp = await client.post("/api/personas", json={
                    "name": "Aiko", "text": "New Aiko text.",
                })
                check("status 200", resp.status == 200, f"got {resp.status}")
                check("txt overwritten",
                      (personas_dir / "Aiko.txt").read_text(encoding="utf-8")
                      == "New Aiko text.")
                check("image_desc cleared when omitted",
                      not (personas_dir / "Aiko.image_desc").exists())
                check("image cleared when omitted",
                      not (personas_dir / "Aiko.jpg").exists())

                # --- Missing name ------------------------------------------
                print("\nGET /api/personas?name=Nope")
                resp = await client.get("/api/personas?name=Nope")
                check("status 404", resp.status == 404, f"got {resp.status}")

                # --- Templates list (builtin + user merged) ----------------
                print("\nGET /api/persona_templates?names_only=1")
                resp = await client.get("/api/persona_templates?names_only=1")
                data = await resp.json()
                names = data.get("templates", [])
                check("status 200", resp.status == 200)
                check("builtin template listed", "sample_template" in names, data)
                check("user template listed", "extra_template" in names, data)

                # --- Template load (user overrides / merges with builtin) --
                print("\nGET /api/persona_templates?name=sample_template (merged)")
                resp = await client.get("/api/persona_templates?name=sample_template")
                data = await resp.json()
                check("status 200", resp.status == 200)
                check("merged user+builtin text",
                      data.get("text") == "USER OVERRIDE\n---\nSAMPLE TEMPLATE", data)

                # --- Missing template (optional -> empty text, 200) -------
                print("\nGET /api/persona_templates?name=nope")
                resp = await client.get("/api/persona_templates?name=nope")
                data = await resp.json()
                check("status 200", resp.status == 200, f"got {resp.status}")
                check("empty text for unknown template", data.get("text") == "")

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
