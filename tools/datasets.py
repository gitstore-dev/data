#!/usr/bin/env python3
"""Validate, lock and export canonical starter/full selections without rewriting data."""
import argparse
import collections
import hashlib
import json
import posixpath
import re
import shutil
import tempfile
from pathlib import Path

import taxonomy

ROOT = Path(__file__).resolve().parents[1]
LICENSES = ("LICENSE", "LICENSE-SHOPIFY")
ORDER = "ancestor depth ascending, then Shopify category ID in ASCII order"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(content):
    return hashlib.sha256(content).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def read_json(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def selected_ids(definition, categories):
    if definition.get("all_source_ids") is True:
        require("ids_file" not in definition, "selection has two membership rules")
        ids = list(categories)
    else:
        ids = (ROOT / definition["ids_file"]).read_text(encoding="utf-8").splitlines()
    membership = set(ids)
    require(len(membership) == len(ids), "duplicate selection IDs")
    require(membership <= categories.keys(), "selection IDs missing from source")
    require(len(ids) == definition["resources"], "selection resource count changed")
    depths = {}
    for identifier in ids:
        ancestors = taxonomy.ancestry(categories[identifier], categories)
        require(all(c["id"] in membership for c in ancestors), f"missing ancestor for {identifier}")
        depths[identifier] = len(ancestors)
    ordered = sorted(ids, key=lambda identifier: (depths[identifier], identifier))
    require(sum(categories[i]["parent"] is None for i in ids) == definition["roots"],
            "selection root count changed")
    return ordered


def inventory(ids, categories):
    paths = {taxonomy.location(categories[i]).as_posix() for i in ids}
    available = paths | set(LICENSES)
    records = []
    for identifier in ids:
        c = categories[identifier]
        path = taxonomy.location(c).as_posix()
        content = (ROOT / path).read_bytes()
        # Ancestors alone are insufficient: canonical bodies link every child.
        for target in re.findall(r"\]\(([^)]+)\)", content.decode("utf-8")):
            if "://" not in target:
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
                require(resolved in available, f"selection has broken link: {path} -> {target}")
        records.append({
            "id": identifier, "name": "shopify-" + identifier,
            "title": c["title"], "path": path,
            "parent": "shopify-" + c["parent"] if c["parent"] else None,
            "bytes": len(content), "sha256": sha256(content),
        })
    return records


def snapshot():
    definitions = read_json("datasets/definitions.json")
    require(definitions["format_version"] == 1, "unsupported selection format")
    require(definitions["default_selection"] == "starter", "starter must remain the default")
    require(set(definitions["selections"]) == {"starter", "full"}, "unexpected selections")
    compatibility = read_json("datasets/compatibility.json")
    schema = ROOT / compatibility["vendored_schema_path"]
    require(sha256(schema.read_bytes()) == compatibility["schema_sha256"], "schema checksum changed")
    categories = taxonomy.index()
    source = read_json("source.lock.json")
    require(source["version"] == taxonomy.VERSION and source["locale"] == "en", "unexpected source version/locale")
    require(len(categories) == source["categories"] == 64292, "full source count changed")
    # Authoring evidence binds editorial records as well as the rendered files.
    inputs = {"source.lock.json": sha256((ROOT / "source.lock.json").read_bytes())}
    inputs.update({p.relative_to(ROOT).as_posix(): sha256(p.read_bytes())
                   for p in sorted((ROOT / "editorial/descriptions.en").glob("*.jsonl"))})
    licenses = [{"path": name, "sha256": sha256((ROOT / name).read_bytes()),
                 "bytes": (ROOT / name).stat().st_size} for name in LICENSES]
    require(all(item["bytes"] > 0 for item in licenses), "empty license")
    selections, inventories = {}, {}
    for name, definition in definitions["selections"].items():
        ids = selected_ids(definition, categories)
        if name == "full":
            require(set(ids) == categories.keys(), "full selection must contain every source ID")
        records = inventory(ids, categories)
        inventories[name] = records
        selections[name] = {
            "resources": len(ids), "roots": definition["roots"],
            "verticals": dict(sorted(collections.Counter(categories[i]["vertical"] for i in ids).items())),
            "manifest_bytes": sum(record["bytes"] for record in records),
            "ids_sha256": sha256(("\n".join(ids) + "\n").encode()),
            "inventory_sha256": sha256(encoded(records)), "rationale": definition["rationale"],
        }
    return {
        "format_version": 1, "dataset_version": definitions["dataset_version"],
        "default_selection": "starter", "ordering": ORDER,
        "dependency_closure": "all ancestors and local navigation targets included",
        "source": source, "authoring_inputs": inputs, "compatibility": compatibility,
        "licenses": licenses, "selections": selections,
    }, inventories


def verify_lock(current):
    require(read_json("datasets/selections.lock.json") == current,
            "selection lock differs; review changes then run tools/datasets.py lock")


def export(output, selection, current, records):
    output = output.resolve()
    require(not output.exists(), f"output already exists: {output}")
    require(not output.is_relative_to(ROOT) or output.is_relative_to(ROOT / "dist"),
            "exports inside the repository must be under dist/")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Build completely before publishing. A failure must not expose a partial export.
    with tempfile.TemporaryDirectory(prefix=".seed-export-", dir=output.parent) as temporary:
        staging = Path(temporary) / "payload"
        staging.mkdir()
        for record in [*records, *current["licenses"]]:
            source = ROOT / record["path"]
            target = staging / record["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            require(sha256(target.read_bytes()) == record["sha256"], f"file changed during export: {source}")
        metadata = {key: value for key, value in current.items()
                    if key not in {"selections", "authoring_inputs", "default_selection"}}
        metadata.update({"selection": selection, **current["selections"][selection], "inventory": records})
        (staging / "dataset.json").write_bytes(encoded(metadata))
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("lock", help="explicitly refresh the reviewed selection lock")
    commands.add_parser("validate", help="validate canonical data and selection lock")
    exporter = commands.add_parser("export", help="export exact Markdown bytes and licenses")
    exporter.add_argument("--selection", choices=("starter", "full"), default="starter")
    exporter.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        taxonomy.validate()
        current, inventories = snapshot()
        if args.command == "lock":
            taxonomy.write(ROOT / "datasets/selections.lock.json", encoded(current).decode())
        else:
            verify_lock(current)
        if args.command == "export":
            export(args.output, args.selection, current, inventories[args.selection])
            print(f"Exported {args.selection}: {len(inventories[args.selection])} unchanged manifests to {args.output}")
        else:
            summary = "; ".join(f"{name}={item['resources']}/{item['roots']} roots"
                                for name, item in current["selections"].items())
            print(f"PASS: {summary}; locked inventories and complete navigation")
    except (ValueError, AssertionError, KeyError, FileNotFoundError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
