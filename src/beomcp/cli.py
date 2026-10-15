import argparse
import json
import sys
from pathlib import Path

from .projection import build_projection


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="onto")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("project", help="build schema.projection.json from TBox + shapes")
    p.add_argument("--tbox", default="ontology/TBox.ttl")
    p.add_argument("--shapes", default="ontology/shapes.ttl")
    p.add_argument("--out", default="ontology/schema.projection.json")

    args = parser.parse_args(argv)
    if args.command == "project":
        data = build_projection(Path(args.tbox), Path(args.shapes))
        text = json.dumps(data, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
