import json
import sqlite3
import sys

import requests

GID = "ff132596b023676f7a277a99b67b0f57"

conn = sqlite3.connect("db_vivid.sqlite")
print("tables:", [r[0] for r in conn.execute(
    "select name from sqlite_master where type='table'")])
for tbl, col in (("roles", "owner"), ("stories", "owner")):
    try:
        print(tbl, "owners:", conn.execute(
            "select owner, count(*) from %s group by owner" % tbl).fetchall())
    except sqlite3.Error as e:
        print(tbl, "err", e)
for tbl, col in (("roles", "gist_id"), ("stories", "gist_id")):
    try:
        print(tbl, "has gist:", conn.execute(
            "select count(*) from %s where gist_id=?" % tbl, (GID,)).fetchone())
    except sqlite3.Error as e:
        print(tbl, "err", e)

data = json.load(open("db_chatgo.json", encoding="utf-8"))
for k in ("roles", "stories"):
    ids = [r.get("gist_id") for r in data.get(k, [])]
    owners = sorted({r.get("owner") for r in data.get(k, [])})
    print(k, "count:", len(ids), "owners:", owners, "has gist:", GID in ids)

sys.path.insert(0, ".")
from scan_gists import resolve_token  # noqa: E402

token, src = resolve_token()
print("token source:", src, "len:", len(token or ""))

h = {"Accept": "application/vnd.github+json",
     "User-Agent": "VividChat-Gist-Scanner/1.0"}
if token:
    h["Authorization"] = "Bearer " + token

r = requests.get("https://api.github.com/gists/" + GID, headers=h, timeout=30)
print("API status:", r.status_code)
if r.status_code == 200:
    g = r.json()
    print("owner:", (g.get("owner") or {}).get("login"),
          "public:", g.get("public"))
    print("desc:", g.get("description"))
    print("files:", list(g.get("files", {})))
    print("created:", g.get("created_at"), "updated:", g.get("updated_at"))
else:
    print(r.text[:300])

# Does user-search discovery see the owner's gists at all?
r2 = requests.get("https://gist.github.com/search?q=" +
                  "user%3AVividChatAppX", headers=h, timeout=30)
print("user search status:", r2.status_code,
      "has gist on page:", GID in r2.text)
