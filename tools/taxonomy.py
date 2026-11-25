#!/usr/bin/env python3
"""Deterministic Shopify import, resumable editorial generation and rendering."""
import argparse
import collections
import gzip
import hashlib
import json
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "2026-11"
URL = f"https://github.com/Shopify/product-taxonomy/releases/download/v{VERSION}/categories.en.json.gz"
SOURCE = ROOT / "upstream/categories.en"
EDITORIAL = ROOT / "editorial/descriptions.en"
SCHEMA = ROOT / "schemas/category.schema.json"
MAX_SHARD_BYTES = 50_000_000

def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def write(path, content):
    if path.suffix == ".jsonl" and len(content.encode("utf-8")) >= MAX_SHARD_BYTES:
        raise ValueError(f"{path}: JSONL shard must stay below {MAX_SHARD_BYTES:,} bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != content:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)

def rows(path):
    if not path.exists():
        return []
    if path.is_dir():
        records = []
        for shard in sorted(path.glob("*.jsonl")):
            for record in rows(shard):
                vertical = record.get("vertical", record["id"].split("-", 1)[0])
                assert vertical == shard.stem, f"record {record['id']} belongs in {vertical}.jsonl, not {shard}"
                records.append(record)
        return records
    return [json.loads(line) for line in path.read_text().splitlines() if line]

def index():
    data = rows(SOURCE)
    assert len({c["id"] for c in data}) == len(data), "duplicate source IDs"
    return {c["id"]: c for c in data}

def descriptions_index():
    records = rows(EDITORIAL)
    assert len({r["id"] for r in records}) == len(records), "duplicate editorial IDs"
    return {r["id"]: r for r in records}

def source_checksums():
    digest = hashlib.sha256()
    shards = {}
    for path in sorted(SOURCE.glob("*.jsonl")):
        content = path.read_bytes()
        digest.update(content)
        shards[path.name] = hashlib.sha256(content).hexdigest()
    return digest.hexdigest(), shards

def write_source(categories):
    grouped = collections.defaultdict(list)
    for category in categories:
        grouped[category["vertical"]].append(category)
    unexpected = {p.stem for p in SOURCE.glob("*.jsonl")} - grouped.keys()
    assert not unexpected, f"unexpected source shards: {unexpected}"
    for vertical, records in sorted(grouped.items()):
        write(SOURCE / f"{vertical}.jsonl", "".join(dump(c) + "\n" for c in records))

def location(c):
    return Path("categories") / c["vertical"] / (c["id"] + ".md")

def escaped(text):
    return re.sub(r"([\\\[\]*_\x60])", r"\\\1", text)

def link(c, current):
    import posixpath
    path = posixpath.relpath(location(c).as_posix(), location(current).parent.as_posix())
    return f"[{escaped(c['title'])}]({path})"

def ancestry(c, categories):
    result, seen = [], set()
    while c["parent"]:
        assert c["id"] not in seen, "taxonomy cycle"
        seen.add(c["id"])
        c = categories[c["parent"]]
        result.append(c)
    return list(reversed(result))

def children_index(categories):
    children = collections.defaultdict(list)
    for c in categories.values():
        if c["parent"]:
            children[c["parent"]].append(c)
    return children

def import_source():
    with urllib.request.urlopen(URL, timeout=120) as response:
        compressed = response.read()
    checksum = hashlib.sha256(compressed).hexdigest()
    lock_path = ROOT / "source.lock.json"
    if lock_path.exists():
        assert json.loads(lock_path.read_text())["asset_sha256"] == checksum, "pinned asset changed"
    document = json.loads(gzip.decompress(compressed))
    assert document["version"] == VERSION
    normalized = []
    for vertical in document["verticals"]:
        for c in vertical["categories"]:
            normalized.append({
                "id": c["id"].rsplit("/", 1)[-1],
                "gid": c["id"], "title": c["name"], "full_name": c["full_name"],
                "parent": c["parent_id"].rsplit("/", 1)[-1] if c["parent_id"] else None,
                "vertical": vertical["prefix"],
                "attributes": [{"name": a["name"], "description": a["description"]} for a in c["attributes"]],
            })
    assert len(normalized) == 64292 and len(document["verticals"]) == 26
    write_source(normalized)
    normalized_checksum, shard_checksums = source_checksums()
    write(lock_path, json.dumps({
        "version": VERSION, "locale": "en", "asset_url": URL,
        "asset_sha256": checksum, "normalized_sha256": normalized_checksum,
        "source_shards": shard_checksums,
        "normalization_order": "vertical prefix ascending; upstream category order within each vertical",
        "categories": len(normalized), "verticals": len(document["verticals"]),
        "release_url": f"https://github.com/Shopify/product-taxonomy/releases/tag/v{VERSION}",
        "normalization": "category identity, hierarchy and attributes only; return reasons excluded",
    }, indent=2) + "\n")
    license_url = f"https://raw.githubusercontent.com/Shopify/product-taxonomy/v{VERSION}/LICENSE"
    write(ROOT / "LICENSE-SHOPIFY", urllib.request.urlopen(license_url, timeout=60).read().decode())
    print(f"Imported {len(normalized)} categories; compressed asset SHA-256 {checksum}", flush=True)

def prose(c, categories, children):
    title = c["title"]
    ancestors = ancestry(c, categories)
    branches = children[c["id"]]
    if ancestors:
        context = " → ".join(a["title"] for a in ancestors)
        first = f"The “{title}” category belongs to {context}. It groups items under the official Shopify classification “{c['full_name']}”, keeping this product family distinct within its parent, {ancestors[-1]['title']}."
    else:
        first = f"The “{title}” category is a top-level department in Shopify’s product taxonomy. It brings together the product families assigned to {title}, providing the starting point for classification throughout this branch of the catalog."
    if branches:
        examples = ", ".join(b["title"] for b in branches[:5])
        first += f" Its immediate branches include {examples}" + (" and additional specialized categories." if len(branches) > 5 else ".")
    else:
        first += " This is a terminal category in this release, so it provides the most specific available placement along this classification path."
    attrs = c["attributes"]
    # Some upstream attributes are inherited broadly (e.g. watch-band color
    # on urns). Mention a field only when its name relates to the category
    # path or is a general descriptive comparison field.
    def tokens(text):
        return {w.rstrip("s") for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2}
    terms = tokens(c["full_name"]) - {"accessorie", "supplie", "part", "other", "product", "with", "for", "and", "set"}
    general = {"color", "size", "material", "pattern", "shape", "finish", "fabric", "age group", "target gender", "orientation", "language", "length", "width", "height", "weight"}
    ranked = []
    for i, attr in enumerate(attrs):
        related = bool(tokens(attr["name"]) & terms)
        if related or attr["name"].lower() in general:
            ranked.append((not related, i, attr))
    selected = [a for _, _, a in sorted(ranked, key=lambda row: row[:2])[:4]]
    if selected:
        facets = "; ".join(f"{a['name']}: {a['description'].rstrip('.')}" for a in selected)
        second = f"When describing products in {title}, useful Shopify classification details include {facets}. These are descriptive comparison points rather than required GitStore category fields; retain the product’s actual characteristics when choosing a category."
    else:
        second = f"Choose {title} when the product’s primary purpose fits this classification path. Compare its position with adjacent categories under {ancestors[-1]['title'] if ancestors else title} before assigning a product, using the product’s actual function rather than a promotional name."
    if branches:
        second += " Where an immediate branch describes the product more precisely, follow that branch and continue to the most appropriate specific category."
    else:
        second += " Use this placement for products that fit the named category; products serving a different purpose should be classified in the appropriate neighboring branch."
    return [first, second]

def editorial(batch_size):
    categories = index()
    children = children_index(categories)
    existing = descriptions_index()
    assert existing.keys() <= categories.keys(), "editorial IDs absent from source"
    grouped = collections.defaultdict(list)
    for category in categories.values():
        grouped[category["vertical"]].append(category)
    completed = len(existing)
    for vertical, nodes in sorted(grouped.items()):
        path = EDITORIAL / f"{vertical}.jsonl"
        records = [dump(r) + "\n" for r in rows(path)]
        pending = [c for c in nodes if c["id"] not in existing]
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            for c in batch:
                records.append(dump({"id": c["id"], "paragraphs": prose(c, categories, children)}) + "\n")
            write(path, "".join(records))
            completed += len(batch)
            print(f"Editorial coverage {completed}/{len(categories)} ({vertical})", flush=True)

def markdown(c, categories, children, editorial_row):
    meta = {"name": "shopify-" + c["id"], "annotations": {
        "taxonomy.shopify.com/id": c["gid"],
        "taxonomy.shopify.com/version": VERSION,
        "taxonomy.shopify.com/full-name": c["full_name"],
        "gitstore.dev/description-source": "GitStore-authored; generated from Shopify classification context",
    }}
    spec = {"title": c["title"]}
    if c["parent"]:
        spec["parentRef"] = {"apiVersion": "catalog.gitstore.dev/v1beta1", "kind": "CategoryTaxonomy", "name": "shopify-" + c["parent"]}
    header = "\n".join([
        "---", "apiVersion: catalog.gitstore.dev/v1beta1", "kind: CategoryTaxonomy",
        "metadata: " + dump(meta), "spec: " + dump(spec), "---", "",
        "# " + escaped(c["title"]), "", *[p + "\n" for p in editorial_row["paragraphs"]],
        "## Classification", "",
        "**Path:** " + " → ".join([link(a, c) for a in ancestry(c, categories)] + [escaped(c["title"])]), "",
        "**Shopify ID:** \x60" + c["gid"] + "\x60", "",
    ])
    header += "\n"
    branches = children[c["id"]]
    if branches:
        header += "## Direct subcategories\n\n" + "\n".join("- " + link(b, c) for b in branches) + "\n\n"
    else:
        header += "## Classification depth\n\nThis category has no subcategories in release 2026-11.\n\n"
    header += f"## Source\n\nOfficial label and hierarchy: [Shopify Standard Product Taxonomy {VERSION}](https://github.com/Shopify/product-taxonomy/releases/tag/v{VERSION}).\n\nExplanatory paragraphs are GitStore-authored demo copy generated from the category’s classification context and attributes, not official Shopify descriptions. Shopify taxonomy data is used under the MIT license; see [LICENSE-SHOPIFY](../../LICENSE-SHOPIFY).\n"
    return header

def render():
    categories = index()
    children = children_index(categories)
    descriptions = descriptions_index()
    assert descriptions.keys() == categories.keys(), "editorial coverage incomplete"
    for c in categories.values():
        write(ROOT / location(c), markdown(c, categories, children, descriptions[c["id"]]))
    print(f"Rendered {len(categories)} manifests", flush=True)

def validate():
    import yaml
    from jsonschema import Draft202012Validator
    categories = index()
    lock = json.loads((ROOT / "source.lock.json").read_text())
    checksum, shards = source_checksums()
    assert checksum == lock["normalized_sha256"], "normalized source checksum mismatch"
    assert shards == lock["source_shards"], "source shard inventory/checksum mismatch"
    assert len(shards) == lock["verticals"] == 26
    assert len(categories) == 64292
    assert len({c["vertical"] for c in categories.values()}) == 26
    assert sum(c["parent"] is None for c in categories.values()) == 26
    validator = Draft202012Validator(json.loads(SCHEMA.read_text()))
    descriptions = descriptions_index()
    assert descriptions.keys() == categories.keys(), "editorial coverage mismatch"
    source_verticals = {c["vertical"] for c in categories.values()}
    assert {p.stem for p in EDITORIAL.glob("*.jsonl")} == source_verticals
    for path in [*SOURCE.glob("*.jsonl"), *EDITORIAL.glob("*.jsonl")]:
        assert path.stat().st_size < MAX_SHARD_BYTES, f"oversized shard: {path}"
    children = children_index(categories)
    bodies = set()
    expected = {ROOT / location(c) for c in categories.values()}
    assert set((ROOT / "categories").glob("*/*.md")) == expected
    for c in categories.values():
        assert re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?", "shopify-" + c["id"])
        path = ROOT / location(c)
        content = path.read_text()
        _, frontmatter, body = content.split("---\n", 2)
        document = yaml.load(frontmatter, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
        validator.validate(document)
        assert document["metadata"]["name"] == "shopify-" + c["id"]
        assert document["spec"]["title"] == c["title"]
        assert "namespace" not in document["metadata"] and "media" not in document["spec"]
        assert document["kind"] == "CategoryTaxonomy"
        assert "namespace" not in document["spec"].get("parentRef", {})
        if c["parent"]:
            assert document["spec"]["parentRef"]["name"] == "shopify-" + c["parent"]
        else:
            assert "parentRef" not in document["spec"]
        ancestry(c, categories)
        paragraphs = descriptions[c["id"]]["paragraphs"]
        assert len(paragraphs) == 2 and all(len(p.split()) >= 25 and c["title"] in p for p in paragraphs)
        digest = hashlib.sha256("\n".join(paragraphs).encode()).hexdigest()
        assert digest not in bodies, c["id"]
        bodies.add(digest)
        assert content == markdown(c, categories, children, descriptions[c["id"]]), path
        for target in re.findall(r"\]\(([^)]+)\)", body):
            if "://" not in target:
                assert (path.parent / target).is_file(), (path, target)
    assert not (ROOT / "media").exists(), "initial release must contain no media"
    print(f"PASS: {len(categories)} schemas, exact source identities/titles, 26 roots, complete acyclic hierarchy, editorial coverage, deterministic output and local links", flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["import", "editorial", "render", "validate"])
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    assert args.batch_size > 0
    {"import": import_source, "editorial": lambda: editorial(args.batch_size), "render": render, "validate": validate}[args.command]()

if __name__ == "__main__":
    main()
