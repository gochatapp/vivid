"""
Regression test: role images must support MP4.

`.r i` / `.role image` (``list_roles_with_images``) and ``show_role_image``
(used by `.r N` and `.image`) should find a role's ``.mp4`` and send it as a
video (``reply_video``), because MP4 plays better than an animated WebP. MP4 is
preferred over WebP, while static images (jpg/jpeg/png) keep priority.

Run:  python testing/test_role_image_mp4.py
"""

import asyncio
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

# Make the project root importable when run directly.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import system.handlers as handlers_mod  # noqa: E402

PASS = 0
FAIL = 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


class FakeMessage:
    """Records which media type each reply used (and its caption)."""

    def __init__(self):
        self.photos = []
        self.videos = []

    async def reply_photo(self, photo=None, caption=None, **kwargs):
        self.photos.append(caption)

    async def reply_video(self, video=None, caption=None, **kwargs):
        self.videos.append(caption)


class FakeBot:
    def __init__(self, current_role=""):
        self.current_role = current_role


def make_update(user_id=42):
    message = FakeMessage()
    update = types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=user_id),
        message=message,
    )
    return update, message


async def fake_reply_and_log(update, text, is_command=True, bot=None):
    return None


def run_list_roles(roles_dir, images_dir):
    """Calls the `.r i` handler and returns the recorded FakeMessage."""
    update, message = make_update()
    bot = FakeBot()
    with mock.patch.object(handlers_mod, "CHAT_ROLES_DIR", roles_dir), \
            mock.patch.object(handlers_mod, "ROLE_IMAGES_DIR", images_dir), \
            mock.patch.object(handlers_mod, "get_bot", lambda c, u: bot), \
            mock.patch.object(handlers_mod, "get_telegram_user_id", return_value=42), \
            mock.patch.object(handlers_mod, "reply_and_log", fake_reply_and_log):
        asyncio.run(handlers_mod.list_roles_with_images(update, None))
    return message


def run_show_role_image(role_name, images_dir):
    """Calls show_role_image (used by `.r N` / `.image`) and returns the message."""
    update, message = make_update()
    bot = FakeBot()
    with mock.patch.object(handlers_mod, "ROLE_IMAGES_DIR", images_dir), \
            mock.patch.object(handlers_mod, "get_bot", lambda c, u: bot), \
            mock.patch.object(handlers_mod, "get_telegram_user_id", return_value=42):
        asyncio.run(handlers_mod.show_role_image(update, None, role_name))
    return message


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        roles_dir = tmp / "roles"
        images_dir = tmp / "images"
        roles_dir.mkdir()
        images_dir.mkdir()

        # Role text files (display names are .title()-cased -> Alpha, Beta, ...)
        for stem in ["alpha", "beta", "gamma", "delta", "epsilon"]:
            (roles_dir / f"{stem}.txt").write_text("role body", encoding="utf-8")

        # alpha: mp4 only -> video
        (images_dir / "alpha.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
        # beta: mp4 + webp -> mp4 wins (video)
        (images_dir / "beta.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
        (images_dir / "beta.webp").write_bytes(b"RIFF0000WEBP")
        # gamma: webp only -> photo
        (images_dir / "gamma.webp").write_bytes(b"RIFF0000WEBP")
        # delta: jpg only -> photo
        (images_dir / "delta.jpg").write_bytes(b"\xff\xd8\xff\xe0")
        # epsilon: no media -> neither

        print("\n`.r i` role image listing")
        msg = run_list_roles(roles_dir, images_dir)
        videos = " ".join(str(c) for c in msg.videos)
        photos = " ".join(str(c) for c in msg.photos)

        check("mp4-only role sent as video", "Alpha" in videos, videos)
        check("mp4-only role not sent as photo", "Alpha" not in photos, photos)
        check("mp4 preferred over webp (video wins)", "Beta" in videos, videos)
        check("mp4/webp role not sent as photo", "Beta" not in photos, photos)
        check("webp-only role sent as photo", "Gamma" in photos, photos)
        check("webp-only role not sent as video", "Gamma" not in videos, videos)
        check("jpg role sent as photo", "Delta" in photos, photos)
        check("jpg role not sent as video", "Delta" not in videos, videos)
        check("no-media role neither photo nor video",
              "Epsilon" not in photos and "Epsilon" not in videos)

        print("\n`.image` / role switch (show_role_image)")
        msg2 = run_show_role_image("alpha", images_dir)
        check("show_role_image sends mp4 as video", len(msg2.videos) == 1, msg2.videos)
        check("show_role_image sends no photo for mp4", not msg2.photos, msg2.photos)

        msg3 = run_show_role_image("gamma", images_dir)
        check("show_role_image sends webp as photo", len(msg3.photos) == 1, msg3.photos)
        check("show_role_image sends no video for webp", not msg3.videos, msg3.videos)

    print(f"\n=== {PASS} passed, {FAIL} failed ===")
    return FAIL == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
