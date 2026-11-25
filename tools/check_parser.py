#!/usr/bin/env python3
"""Check all canonical manifests with the locked production GitStore parser.

Uses committed Git objects, never the checkout's working tree. Go/Git and their
dependencies are maintainer validation tools; they are not exported seed data.
"""
import argparse
import io
import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

from datasets import ROOT, read_json, require, sha256
import taxonomy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gitstore", type=Path, required=True,
                        help="local GitStore clone containing the locked revision")
    args = parser.parse_args()
    compatibility = read_json("datasets/compatibility.json")
    categories = taxonomy.index()
    source = read_json("source.lock.json")
    require(len(categories) == source["categories"] == 64292, "canonical source count changed")
    require(taxonomy.source_checksums() == (source["normalized_sha256"], source["source_shards"]),
            "canonical source checksums changed")
    revision = compatibility["gitstore_revision"]
    # Archive only immutable, pinned objects; do not execute or alter concurrent work.
    archive = subprocess.check_output(["git", "-C", str(args.gitstore), "archive", revision])
    with tempfile.TemporaryDirectory(prefix="gitstore-parser-") as temporary:
        checkout = Path(temporary).resolve()
        with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
            # Only directories/regular files are needed. GitStore has instruction
            # symlinks; skip these rather than following any archive links.
            for member in tree.getmembers():
                target = checkout / member.name
                require(target.resolve().is_relative_to(checkout), "unsafe Git archive member")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(tree.extractfile(member).read())
        schema = (checkout / compatibility["upstream_schema_path"]).read_bytes()
        require(sha256(schema) == compatibility["schema_sha256"], "pinned upstream schema mismatch")
        require(schema == (ROOT / compatibility["vendored_schema_path"]).read_bytes(), "vendored schema differs")
        harness = checkout / "gitstore-api/cmd/seed-parser-check/main.go"
        harness.parent.mkdir(parents=True)
        harness.write_bytes((ROOT / "tools/parser_check.go").read_bytes())
        cases = [{"path": str(ROOT / taxonomy.location(c)), "name": "shopify-" + c["id"],
                  "title": c["title"], "parent": "shopify-" + c["parent"] if c["parent"] else ""}
                 for c in categories.values()]
        env = dict(os.environ, GOWORK="off")
        subprocess.run(["go", "run", "-mod=readonly", "./cmd/seed-parser-check"],
                       cwd=checkout / "gitstore-api", input=json.dumps(cases).encode(),
                       env=env, check=True)
    print(f"PASS: production parser and schema at {revision}; {len(cases)} canonical manifests")


if __name__ == "__main__":
    main()
