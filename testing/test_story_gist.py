"""
Tests for the Story Creator: GET /story and the /api/story/* + /api/ai/enhance
endpoints that publish stories as public GitHub Gists.

GitHub, ImgBB and image downloads are stubbed out and the story install runs
against a temporary directory, so nothing here touches the network or real data.

Run:  python testing/test_story_gist.py
"""

import asyncio
import sys
import tempfile
import shutil
from pathlib import Path

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import system.web_server as web_server
from aiohttp.test_utils import TestClient, TestServer

PASS = 0
FAIL = 0
GIST_ID = "0123456789abcdef0123456789abcdef"

CHECKS = {"count": 0}


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def make_story_gist():
    """A published story gist in the GOCHATAPP convention."""
    return {
        "files": {
            "GOCHATAPPSTORYTAG_Crystal_Chronicles.md": {
                "content": "# Crystal Chronicles\n\nA crystal that remembers everything.\n",
                "truncated": False,
                "raw_url": "",
            },
            "character_Aiko_Tanaka.txt": {
                "content": "Aiko is a cartographer with a glass eye.",
                "truncated": False,
                "raw_url": "",
            },
            "Aiko_Tanaka_thumb.md": {
                "content": '<img width="450" height="450" alt="image" '
                           'src="https://i.ibb.co/aiko.jpg" />\n',
                "truncated": False,
                "raw_url": "",
            },
            "storyelement_Glass_Spire.txt": {
                "content": "The Glass Spire is the city's memory archive.",
                "truncated": False,
                "raw_url": "",
            },
            "thumb.md": {
                "content": '<img width="450" height="450" alt="image" '
                           'src="https://i.ibb.co/cover.jpg" />\n',
                "truncated": False,
                "raw_url": "",
            },
        }
    }


def make_role_gist():
    """A role gist, which is not a story."""
    return {
        "files": {
            "GOCHATAPPROLETAG_store_test.txt": {
                "content": "You are a role.",
                "truncated": False,
                "raw_url": "",
            },
        }
    }


class FakeProvider:
    """Minimal LLMProvider stand-in that records the call it received."""

    def __init__(self, reply="Enhanced description."):
        self.reply = reply
        self.calls = []

    async def chat(self, model, messages, options):
        self.calls.append({"model": model, "messages": messages, "options": options})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


async def run():
    tmp_dir = Path(tempfile.mkdtemp(prefix="vividstory_"))
    stories_dir = tmp_dir / "stories"
    stories_dir.mkdir()

    patched = {
        name: getattr(web_server, name)
        for name in (
            "STORIES_DIR",
            "_create_gist",
            "_fetch_gist",
            "_read_gist_file",
            "_imgbb_upload_base64",
            "_download_bytes",
            "providers",
            "AI_PROVIDERS",
            "providers_config",
        )
    }
    original_token = web_server.CONFIG.get("GITHUB_TOKEN", "")

    published = {}
    uploads = {"count": 0}
    downloaded = {"urls": []}

    async def fake_create_gist(gist_data):
        published["data"] = gist_data
        return {
            "id": GIST_ID,
            "html_url": f"https://gist.github.com/{GIST_ID}",
        }

    async def fake_imgbb(session, image_base64, name=""):
        uploads["count"] += 1
        return f"https://i.ibb.co/upload{uploads['count']}.jpg"

    async def fake_fetch_gist(session, gist_id):
        if gist_id != GIST_ID:
            raise RuntimeError(f"Gist not found: {gist_id}")
        return make_story_gist()

    async def fake_read_gist_file(session, file_info):
        return file_info.get("content", "")

    async def fake_download(session, url):
        downloaded["urls"].append(url)
        return b"IMG"

    web_server.STORIES_DIR = stories_dir
    web_server._create_gist = fake_create_gist
    web_server._imgbb_upload_base64 = fake_imgbb
    web_server._fetch_gist = fake_fetch_gist
    web_server._read_gist_file = fake_read_gist_file
    web_server._download_bytes = fake_download

    try:
        app = web_server.create_web_app()
        async with TestClient(TestServer(app)) as client:
            # --- Pages ----------------------------------------------------
            print("\nGET /story")
            resp = await client.get("/story")
            body = await resp.text()
            check("status 200", resp.status == 200, f"got {resp.status}")
            check("page served", "Story Creator" in body)
            check("script wired", "/static/story_creator.js" in body)

            resp = await client.get("/static/story_creator.js")
            js = await resp.text()
            check("script served", resp.status == 200 and "scPublishStory" in js,
                  f"got {resp.status}")

            for route in ("/story2", "/story_creator"):
                resp = await client.get(route)
                check(f"{route} status 200", resp.status == 200,
                      f"got {resp.status}")

            # --- POST /api/story/save validation --------------------------
            print("\nPOST /api/story/save (validation)")
            resp = await client.post("/api/story/save", json={})
            check("missing name -> 400", resp.status == 400, f"got {resp.status}")

            web_server.CONFIG["GITHUB_TOKEN"] = ""
            resp = await client.post(
                "/api/story/save", json={"name": "No Token Story"}
            )
            error = (await resp.json()).get("error", "")
            check("missing token -> 400", resp.status == 400, f"got {resp.status}")
            check("missing token message", "GitHub token" in error, error)

            resp = await client.post(
                "/api/story/save",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            check("invalid JSON -> 400", resp.status == 400, f"got {resp.status}")

            web_server.CONFIG["GITHUB_TOKEN"] = "test-token"


            # --- POST /api/story/save (publish) ---------------------------
            print("\nPOST /api/story/save (publish)")
            payload = {
                "name": "Crystal Chronicles",
                "description": "A crystal that remembers everything.",
                "image": {"url": "data:image/jpeg;base64,AAAA", "meta": "450x450"},
                "characters": [
                    {
                        "name": "Aiko Tanaka",
                        "description": "A cartographer with a glass eye.",
                        "image": {"url": "data:image/jpeg;base64,BBBB"},
                    },
                    {
                        "name": "Aiko Tanaka",
                        "description": "A rival cartographer.",
                        "image": {"url": "https://i.ibb.co/existing.jpg"},
                    },
                    {
                        "name": "No Description",
                        "description": "",
                        "image": {"url": ""},
                    },
                ],
                "story_elements": [
                    {
                        "name": "Glass Spire",
                        "description": "The city's memory archive.",
                        "image": {"url": "https://i.ibb.co/spire.jpg"},
                    },
                ],
            }
            resp = await client.post("/api/story/save", json=payload)
            result = await resp.json()
            check("status 200", resp.status == 200,
                  f"got {resp.status} ({result})")
            check("ok true", result.get("ok") is True, str(result))
            check("gist id returned", result.get("gist_id") == GIST_ID,
                  str(result))
            check("gist url returned",
                  result.get("html_url") == f"https://gist.github.com/{GIST_ID}")

            files = (published.get("data") or {}).get("files") or {}
            contents = {name: info["content"] for name, info in files.items()}
            expected = {
                "GOCHATAPPSTORYTAG_Crystal_Chronicles.md",
                "thumb.md",
                "character_Aiko_Tanaka.txt",
                "Aiko_Tanaka_thumb.md",
                "character_Aiko_Tanaka_2.txt",
                "Aiko_Tanaka_2_thumb.md",
                "storyelement_Glass_Spire.txt",
                "Glass_Spire_thumb.md",
            }
            check("gist files match the GOCHATAPP layout",
                  set(contents) == expected,
                  f"missing={expected - set(contents)} "
                  f"extra={set(contents) - expected}")
            tag_text = contents.get("GOCHATAPPSTORYTAG_Crystal_Chronicles.md", "")
            check("story tag starts with the name heading",
                  tag_text.startswith("# Crystal Chronicles\n\n"), tag_text)
            check("story tag keeps the description",
                  "A crystal that remembers everything." in tag_text, tag_text)
            check("character file written",
                  contents.get("character_Aiko_Tanaka.txt", "")
                  == "A cartographer with a glass eye.\n",
                  contents.get("character_Aiko_Tanaka.txt", ""))
            check("duplicate name gets a suffix",
                  contents.get("character_Aiko_Tanaka_2.txt", "")
                  == "A rival cartographer.\n",
                  contents.get("character_Aiko_Tanaka_2.txt", ""))
            check("empty description is skipped",
                  "character_No_Description.txt" not in contents)
            check("story cover uploaded to ImgBB",
                  'src="https://i.ibb.co/upload1.jpg"' in contents.get("thumb.md", "")
                  and 'width="450"' in contents.get("thumb.md", ""),
                  contents.get("thumb.md", ""))
            check("uploaded character portrait used",
                  'src="https://i.ibb.co/upload2.jpg"'
                  in contents.get("Aiko_Tanaka_thumb.md", ""),
                  contents.get("Aiko_Tanaka_thumb.md", ""))
            check("existing image URL is reused",
                  'src="https://i.ibb.co/existing.jpg"'
                  in contents.get("Aiko_Tanaka_2_thumb.md", ""),
                  contents.get("Aiko_Tanaka_2_thumb.md", ""))
            check("element image written",
                  'src="https://i.ibb.co/spire.jpg"'
                  in contents.get("Glass_Spire_thumb.md", ""),
                  contents.get("Glass_Spire_thumb.md", ""))
            check("only the two data URLs were uploaded", uploads["count"] == 2,
                  str(uploads["count"]))
            check("gist is public", published["data"].get("public") is True)
            check("gist description names the story",
                  published["data"].get("description")
                  == "Character.XI story: Crystal Chronicles",
                  published["data"].get("description", ""))
            check("response lists the files",
                  isinstance(result.get("files"), list)
                  and "thumb.md" in result["files"], str(result.get("files")))


            # --- GET /api/story/gist --------------------------------------
            print("\nGET /api/story/gist")
            resp = await client.get("/api/story/gist")
            check("missing id -> 400", resp.status == 400, f"got {resp.status}")

            resp = await client.get(
                f"/api/story/gist?gist_id=https://gist.github.com/user/{GIST_ID}"
            )
            data = await resp.json()
            check("status 200", resp.status == 200, f"got {resp.status} ({data})")
            story = data.get("story") or {}
            check("gist id parsed from URL", data.get("gist_id") == GIST_ID)
            check("story name from heading", story.get("name") == "Crystal Chronicles",
                  str(story.get("name")))
            check("story description parsed",
                  story.get("description") == "A crystal that remembers everything.",
                  str(story.get("description")))
            check("cover image parsed",
                  (story.get("image") or {}).get("url") == "https://i.ibb.co/cover.jpg",
                  str(story.get("image")))
            chars = story.get("characters") or []
            check("one character parsed", len(chars) == 1, str(chars))
            if chars:
                check("character name readable",
                      chars[0].get("name") == "Aiko Tanaka", str(chars[0]))
                check("character description parsed",
                      chars[0].get("description")
                      == "Aiko is a cartographer with a glass eye.", str(chars[0]))
                check("character thumb parsed",
                      (chars[0].get("image") or {}).get("url")
                      == "https://i.ibb.co/aiko.jpg", str(chars[0]))
            elems = story.get("story_elements") or []
            check("one element parsed", len(elems) == 1, str(elems))
            if elems:
                check("element type holds the name",
                      elems[0].get("name") == "Glass Spire", str(elems[0]))

            async def fake_role_gist(session, gist_id):
                return make_role_gist()

            web_server._fetch_gist = fake_role_gist
            resp = await client.get(f"/api/story/gist?gist_id={GIST_ID}")
            check("role gist -> 400", resp.status == 400, f"got {resp.status}")
            web_server._fetch_gist = fake_fetch_gist

            # --- POST /api/story/install ----------------------------------
            print("\nPOST /api/story/install")
            resp = await client.post("/api/story/install", json={})
            check("missing id -> 400", resp.status == 400, f"got {resp.status}")

            resp = await client.post(
                "/api/story/install", json={"gist_id": GIST_ID}
            )
            data = await resp.json()
            check("status 200", resp.status == 200, f"got {resp.status} ({data})")
            full_name = f"Crystal_Chronicles!{GIST_ID}"
            story_dir = stories_dir / full_name
            check("story directory created", story_dir.is_dir(), str(story_dir))
            check("install name returned", data.get("name") == full_name, str(data))
            context = story_dir / "story_context" / "context.txt"
            check("context written",
                  context.exists() and "Crystal Chronicles" in
                  context.read_text(encoding="utf-8"), str(context))
            char_file = story_dir / "characters" / "Aiko_Tanaka.txt"
            check("character file installed",
                  char_file.exists() and
                  "cartographer" in char_file.read_text(encoding="utf-8"))
            elem_file = story_dir / "storyelements" / "Glass_Spire.txt"
            check("element file installed",
                  elem_file.exists() and
                  "memory archive" in elem_file.read_text(encoding="utf-8"))
            check("character portrait downloaded",
                  (story_dir / "characters" / "Aiko_Tanaka.jpg").read_bytes() == b"IMG"
                  if (story_dir / "characters" / "Aiko_Tanaka.jpg").exists() else False)
            check("cover downloaded",
                  (story_dir / "thumb.jpg").read_bytes() == b"IMG"
                  if (story_dir / "thumb.jpg").exists() else False)
            check("backups folder created", (story_dir / "backups").is_dir())


            # --- POST /api/story/imgbb ------------------------------------
            print("\nPOST /api/story/imgbb")
            resp = await client.post("/api/story/imgbb", json={})
            check("missing image -> 400", resp.status == 400, f"got {resp.status}")

            resp = await client.post(
                "/api/story/imgbb",
                json={"image_base64": "data:image/jpeg;base64,AAAA"},
            )
            data = await resp.json()
            check("status 200", resp.status == 200, f"got {resp.status} ({data})")
            check("public url returned",
                  str(data.get("url", "")).startswith("https://i.ibb.co/"),
                  str(data))
            check("link mirrors url", data.get("link") == data.get("url"))

            # --- POST /api/ai/enhance -------------------------------------
            print("\nPOST /api/ai/enhance")
            provider = FakeProvider("Enhanced description.")
            web_server.providers = {"test-provider": provider}
            web_server.AI_PROVIDERS = ["test-provider"]
            web_server.providers_config = [
                {"name": "test-provider", "model": "test-model"}
            ]

            resp = await client.post("/api/ai/enhance", json={})
            check("missing prompt -> 400", resp.status == 400,
                  f"got {resp.status}")

            resp = await client.post(
                "/api/ai/enhance",
                json={"system": "You write descriptions.",
                      "prompt": "A lighthouse."},
            )
            data = await resp.json()
            check("status 200", resp.status == 200, f"got {resp.status} ({data})")
            check("text returned", data.get("text") == "Enhanced description.",
                  str(data))
            check("provider reported",
                  data.get("provider") == "test-provider", str(data))
            check("model taken from providers_config",
                  data.get("model") == "test-model", str(data))
            check("system message forwarded",
                  len(provider.calls) == 1
                  and provider.calls[0]["messages"][0]
                  == {"role": "system", "content": "You write descriptions."},
                  str(provider.calls))
            check("user message forwarded",
                  bool(provider.calls) and provider.calls[0]["messages"][-1]
                  == {"role": "user", "content": "A lighthouse."},
                  str(provider.calls))

            provider.reply = RuntimeError("model exploded")
            resp = await client.post("/api/ai/enhance", json={"prompt": "x"})
            check("provider failure -> 502", resp.status == 502,
                  f"got {resp.status}")

            web_server.providers = {}
            web_server.AI_PROVIDERS = []
            resp = await client.post("/api/ai/enhance", json={"prompt": "x"})
            check("no provider -> 400", resp.status == 400, f"got {resp.status}")

    finally:
        for name, value in patched.items():
            setattr(web_server, name, value)
        web_server.CONFIG["GITHUB_TOKEN"] = original_token
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    ok = asyncio.run(run())
    sys.exit(0 if ok else 1)
