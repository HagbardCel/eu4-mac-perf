"""CLI: promote grounded evidence into the frozen qualification oracle."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from automation.evidence import promote_primitive_table
from automation.state import repo_root
from automation.verification_manifest import (
    EU4_EXECUTABLE_SHA256,
    REPO_BASELINE_COMMIT,
    build_manifest,
    digest_manifest,
    member_from_file,
    save_manifest,
    verify_manifest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--executable",
        type=Path,
        default=Path("/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4"),
    )
    parser.add_argument(
        "--oracle-dir",
        type=Path,
        default=None,
        help="Default: <repo>/analysis/qualification/v0",
    )
    args = parser.parse_args(argv)
    root = repo_root(Path(__file__).resolve())
    oracle = args.oracle_dir or (root / "analysis" / "qualification" / "v0")
    oracle.mkdir(parents=True, exist_ok=True)

    evidence_path = oracle / "primitive-table.evidence.json"
    record = promote_primitive_table(args.executable, evidence_path)

    # Golden reference only (not the agent workspace candidate).
    golden = oracle / "primitive-lookup.golden.json"
    golden.write_text(
        json.dumps(
            {
                "schema": "eu4-primitive-lookup/v1",
                "table": record["output"]["table"],
                "engine_enum_4_gl_mode": record["output"]["engine_enum_4_gl_mode"],
                "out_of_range_default": record["output"]["mapping"][
                    "default_out_of_range"
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    # Never keep a candidate under the trusted oracle tree.
    stray = oracle / "primitive-lookup.candidate.json"
    if stray.exists():
        stray.unlink()

    checker = root / "campaigns" / "eu4-a01-trusted-validation" / "accept.py"
    members = [
        member_from_file(root, str(evidence_path.relative_to(root)), "frozen_oracle"),
        member_from_file(root, "automation/evidence.py", "trusted_helper"),
        member_from_file(root, "automation/macho_map.py", "trusted_helper"),
        member_from_file(root, "automation/verification_manifest.py", "trusted_helper"),
        member_from_file(root, "automation/replay_primitive.py", "trusted_helper"),
        member_from_file(root, str(checker.relative_to(root)), "checker"),
    ]
    manifest = build_manifest(
        members=members,
        repo_baseline_commit=REPO_BASELINE_COMMIT,
        eu4_executable_sha256=EU4_EXECUTABLE_SHA256,
        extra={
            "oracle_version": 0,
            "oracle_version_name": "v0",
            "target_probe": "GetOpenGLPrimitiveType",
        },
    )
    errors = verify_manifest(root, manifest)
    if errors:
        print("manifest errors:\n" + "\n".join(errors), file=sys.stderr)
        return 1
    manifest_path = oracle / "verification-manifest.json"
    digest = save_manifest(manifest_path, manifest)
    (oracle / "CURRENT").write_text("v0\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "evidence": str(evidence_path),
                "manifest": str(manifest_path),
                "manifest_sha256": digest,
                "digest_check": digest_manifest(manifest),
                "engine_enum_4_gl_mode": record["output"]["engine_enum_4_gl_mode"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
