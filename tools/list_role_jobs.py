"""Print each role's name and a concise job or character description."""
import argparse
import sqlite3
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from scan_gists import (
    fetch_server_models,
    load_config,
    query_llm,
    resolve_model,
    resolve_server,
)


DB_PATH = BASE_DIR / "db_vivid.sqlite"


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Print role names with a job or concise character description."
        )
    )
    parser.add_argument(
        "--limit", type=int,
        help="process at most this many roles (useful for a quick preview)",
    )
    parser.add_argument(
        "--db", type=Path, default=DB_PATH,
        help="path to the SQLite database (default: db_vivid.sqlite)",
    )
    parser.add_argument(
        "--server",
        help="LLM server number, name, or URL (default: interactive picker)",
    )
    parser.add_argument(
        "--model",
        help="model name (default: interactive picker)",
    )
    parser.add_argument(
        "--no-prompt", action="store_true",
        help="use the configured default server and model without prompting",
    )
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if not args.db.is_file():
        parser.error("database not found: %s" % args.db)

    servers, models, default_url, default_model = load_config()
    base_url = resolve_server(
        servers, default_url, args.server, args.no_prompt
    )
    if args.model or args.no_prompt:
        model = resolve_model(
            models, default_model, args.model, args.no_prompt
        )
    else:
        print("Querying %s for available models..." % base_url)
        live_models = fetch_server_models(base_url)
        if live_models:
            server = next(
                (item for item in servers if item["url"] == base_url), {}
            )
            server_default = server.get("model")
            if server_default not in live_models:
                server_default = (
                    default_model if default_model in live_models
                    else live_models[0]
                )
            model = resolve_model(
                live_models, server_default, None, False
            )
        else:
            print("Could not list models from this server; using configured "
                  "models.")
            model = resolve_model(models, default_model, None, False)
    print("Using server=%s model=%s" % (base_url, model))

    query = (
        "SELECT rolename, description FROM roles "
        "WHERE trim(coalesce(description, '')) != '' "
        "ORDER BY rolename COLLATE NOCASE"
    )
    if args.limit is not None:
        query += " LIMIT ?"

    with sqlite3.connect(str(args.db)) as conn:
        rows = conn.execute(
            query, (args.limit,) if args.limit is not None else ()
        ).fetchall()

    for role_name, description in rows:
        prompt = (
            "Read this character description. Return the character's name "
            "and a concise label in exactly this format: Name (Label). Use "
            "their occupation/job as the label when it is clear, limited to "
            "two words. Otherwise, use one or two words describing their "
            "defining personality, demeanor, or character role, grounded in "
            "the description. Always "
            "provide a useful label; never use placeholders such as "
            "'Not specified', 'Unknown', or 'N/A'. Do not add an explanation.\n\n"
            "Character description:\n%s" % description
        )
        result = query_llm(
            prompt,
            max_tokens=80,
            temperature=0,
            base_url=base_url,
            model=model,
        )
        line = " ".join(result.strip().splitlines()).strip()
        if not line:
            raise RuntimeError(
                "The LLM returned an empty result for role %r." % role_name
            )
        print(line)


if __name__ == "__main__":
    main()
