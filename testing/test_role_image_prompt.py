"""
Regression test: Role Maker image prompts must be framed from the hips up and
must tie the character's mouth/expression (and pose) to their personality.

Runs the real /api/create_role_stream endpoint against a fake provider injected
into the live provider registry, so no LLM call and no config change happens.

Run:  python testing/test_role_image_prompt.py
"""

import asyncio
import json
import sys
from pathlib import Path

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import system.web_server as ws
from system.web_server import IMAGE_PROMPT_RULES, create_web_app
from aiohttp.test_utils import TestClient, TestServer


PASS = 0
FAIL = 0

FAKE_NAME = "__image_prompt_test__"
FAKE_URL = "http://image-prompt-test.local"


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


class FakeProvider:
    """Captures the role prompt and returns a canned LLM reply."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    async def chat(self, model, messages, options=None):
        self.prompts.append(messages[0]["content"])
        return self.reply


def sse_events(text):
    events = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                events.append(json.loads(line[5:].strip()))
            except ValueError:
                pass
    return events


def install_fake(reply):
    fake = FakeProvider(reply)
    ws.providers[FAKE_NAME] = fake
    ws.providers_config.append({"name": FAKE_NAME, "url": FAKE_URL, "model": "fake-model"})
    return fake


def remove_fake():
    ws.providers.pop(FAKE_NAME, None)
    ws.providers_config[:] = [c for c in ws.providers_config if c.get("name") != FAKE_NAME]


async def request_role(client, general_description):
    return await client.post("/api/create_role_stream", json={
        "template_text": "TEMPLATE BODY",
        "general_description": general_description,
        "server_url": FAKE_URL,
        "model": "fake-model",
        "image_look": "sultry",
    })


async def run():
    # --- Prompt rules -------------------------------------------------------
    print("\nIMAGE_PROMPT_RULES")
    rules = IMAGE_PROMPT_RULES.lower()
    check("mentions hips up", "from the hips up" in rules)
    check("ruled out close-up", "close-up" in rules)
    check("ties mouth to personality", "mouth" in rules and "personality" in rules)

    full_reply = (
        "=== ROLE ===\nRole body text.\n\n"
        "=== FILENAME ===\nTest_Role\n\n"
        "=== IMAGE PROMPT ===\nA canned image prompt.\n\n"
        "=== SUMMARY ===\nShort summary.\n"
    )
    fake = install_fake(full_reply)
    try:
        app = create_web_app()
        async with TestClient(TestServer(app)) as client:
            # --- Prompt sent to the LLM ------------------------------------
            print("\nPOST /api/create_role_stream (LLM prompt)")
            resp = await request_role(client, "A bratty cheerleader")
            check("status 200", resp.status == 200, f"got {resp.status}")
            text = await resp.text()
            check("one prompt captured", len(fake.prompts) == 1, f"got {len(fake.prompts)}")
            sent = fake.prompts[0].lower() if fake.prompts else ""
            check("prompt asks for hips-up view", "from the hips up" in sent)
            check("prompt asks for mouth/expression", "mouth" in sent)
            check("prompt asks for personality match", "personality" in sent)

            events = sse_events(text)
            image_events = [e for e in events if e.get("step") == "image"]
            check("image event streamed", bool(image_events))
            check("image prompt passed through",
                  image_events and image_events[0].get("image_description") == "A canned image prompt.")

            # --- Fallback when the LLM omits an image prompt ----------------
            print("\nPOST /api/create_role_stream (fallback image prompt)")
            fake.reply = (
                "=== ROLE ===\nRole body text.\n\n"
                "=== FILENAME ===\nTest_Role\n\n"
                "=== SUMMARY ===\nShort summary.\n"
            )
            resp = await request_role(client, "A bratty cheerleader")
            check("status 200", resp.status == 200, f"got {resp.status}")
            image_events = [e for e in sse_events(await resp.text()) if e.get("step") == "image"]
            fallback = (image_events[0].get("image_description") or "").lower() if image_events else ""
            check("fallback is hips-up", "hips-up shot" in fallback)
            check("fallback matches personality", "personality" in fallback)

            # --- Page hint --------------------------------------------------
            print("\nGET /role_maker")
            resp = await client.get("/role_maker")
            check("/role_maker 200", resp.status == 200, f"got {resp.status}")
            body = (await resp.text()).lower()
            check("page mentions hips up", "from the hips up" in body)
            check("page mentions personality expression", "personality" in body)
    finally:
        remove_fake()

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
