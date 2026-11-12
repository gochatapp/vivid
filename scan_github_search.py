
import re
import requests
from urllib.parse import quote

# Filename tags GitHub gist search can find. Gist search breaks filename tokens
# on "." and "_", so the old dotted prefix (gochatapp.github.io, see
# scan_gists.LEGACY_PREFIXES) cannot be searched: filename:gochatapp.github.io
# returns tens of thousands of unrelated *.github.io gists. The tags carry no
# separator, so each one is a whole token and is searched directly.
SEARCH_TERMS = ("GOCHATAPPROLETAG", "GOCHATAPPSTORYTAG")


def search_url(term):
    return ("https://gist.github.com/search?q="
            + quote(f"filename:{term}"))

HEADERS = {
    "User-Agent": "VividChat-Gist-Scanner/1.0",
    "Accept": "application/vnd.github+json",
}

session = requests.Session()
session.headers.update(HEADERS)


def main():
    print(f"Searching GitHub Gists for filename tags: "
          f"{', '.join(SEARCH_TERMS)}\n")

    # One query per tag: gist search matches whole filename tokens,
    # not substrings, so both tags must be searched separately.
    gist_ids = set()
    for term in SEARCH_TERMS:
        url = search_url(term)
        print(f"Search URL ({term}): {url}")
        response = session.get(url, timeout=30)
        response.raise_for_status()

        # Extract Gist IDs from search-result links.
        found = set(re.findall(
            r'href="/[^/"?#]+/([0-9a-f]{20,})'
            r'(?=[/?#"]|$)',
            response.text,
            re.IGNORECASE,
        ))
        print(f"  {len(found)} gist(s), {len(found - gist_ids)} new\n")
        gist_ids |= found

    if not gist_ids:
        print("No Gist IDs found in the search pages.")
        print("GitHub may have changed its HTML or requested a login.")
        print("Try opening a Search URL in your browser.")
        return

    print(f"Found {len(gist_ids)} matching Gists.\n")

    for gist_id in sorted(gist_ids):
        api_url = f"https://api.github.com/gists/{gist_id}"

        try:
            result = session.get(api_url, timeout=30)
            result.raise_for_status()
            gist = result.json()
        except requests.RequestException as exc:
            print(f"Could not retrieve Gist {gist_id}: {exc}")
            continue

        print("=" * 70)
        print("Description:", gist.get("description") or "(none)")
        print("Author:", (gist.get("owner") or {}).get("login", "Unknown"))
        print("Gist URL:", gist.get("html_url"))
        print("Created:", gist.get("created_at"))
        print("Last updated:", gist.get("updated_at"))
        print("\nFiles:")

        for filename, details in gist.get("files", {}).items():
            print(f"\n  Filename: {filename}")
            print(f"  Raw URL:  {details.get('raw_url')}")
            print(f"  Size:     {details.get('size')} bytes")

    print("\nSearch complete.")


if __name__ == "__main__":
    main()
