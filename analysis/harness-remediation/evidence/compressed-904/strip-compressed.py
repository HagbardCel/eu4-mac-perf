"""Produce the decoded-only event stream for compressed-904.

Removes every texture-level member named "compressed" so the replay host's
compressed_chain_status() sees no block records and takes its supported
decoded upload branch.  All other bytes/values are preserved.
"""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    events = [json.loads(line) for line in
              args.source.read_text(encoding="utf-8").splitlines() if line.strip()]
    removed = []
    for event in events:
        for texture in event.get("textures") or []:
            for level in texture.get("levels") or []:
                if "compressed" in level:
                    block = level.pop("compressed")
                    removed.append({
                        "texture": texture.get("name"),
                        "level": level.get("level"),
                        "format": (block or {}).get("format"),
                        "sha256": (block or {}).get("sha256"),
                        "bytes": (block or {}).get("bytes"),
                    })
    lines = [json.dumps(event, separators=(",", ":"), ensure_ascii=False)
             for event in events]
    args.destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    value = {
        "source": str(args.source),
        "destination": str(args.destination),
        "events": len(events),
        "removed_compressed_members": len(removed),
        "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
        "destination_sha256": hashlib.sha256(
            args.destination.read_bytes()).hexdigest(),
        "source_bytes": args.source.stat().st_size,
        "destination_bytes": args.destination.stat().st_size,
        "removed": removed,
    }
    if args.report:
        args.report.write_text(json.dumps(value, indent=2) + "\n",
                               encoding="utf-8")
    print(json.dumps({key: value[key] for key in (
        "events", "removed_compressed_members", "source_sha256",
        "destination_sha256", "destination_bytes")}, indent=2))


if __name__ == "__main__":
    main()
