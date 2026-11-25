#!/usr/bin/env python3
"""Build and verify data-only seed/v1 archives and OCI image layouts, offline."""
import argparse
import collections
import gzip
import hashlib
import io
import json
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
import zlib
from pathlib import Path

import yaml
from jsonschema.exceptions import ValidationError

import datasets
import taxonomy
from datasets import ROOT, encoded, require, sha256

FORMAT = "gitstore.seed/v1"
ARTIFACT = "application/vnd.gitstore.seed.v1"
LAYER = "application/vnd.gitstore.seed.v1.tar+gzip"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
EMPTY = "application/vnd.oci.empty.v1+json"
REPOSITORY = "https://github.com/gitstore-dev/data"
API = "catalog.gitstore.dev/v1beta1"
CAPABILITY = "repository-scoped-namespace-inference"
MAX_FILES = 70_000
MAX_EXPANDED = 512 * 1024 * 1024
MAX_TAR = 640 * 1024 * 1024
MAX_ARCHIVE = 128 * 1024 * 1024
MAX_METADATA = 64 * 1024 * 1024
MAX_RESOURCE = 1024 * 1024
CHUNK = 64 * 1024
GZIP_HEADER = bytes.fromhex("1f8b08000000000002ff")


def keys(value, expected):
    require(isinstance(value, dict) and value.keys() == set(expected.split()),
            f"unexpected fields; expected {expected}")


def integer(value, maximum):
    require(type(value) is int and 0 <= value <= maximum, "invalid or excessive size/count")
    return value


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value), "invalid SHA-256 digest")
    return value


def json_value(content):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"duplicate JSON key: {key}")
            result[key] = value
        return result
    result = json.loads(content, object_pairs_hook=pairs,
                        parse_constant=lambda _: require(False, "non-finite JSON number"))
    require(encoded(result) == content, "noncanonical JSON")
    return result


def file_limit(name):
    if name in {"bundle.json", "dataset.json"}:
        return MAX_METADATA
    if name in datasets.LICENSES:
        return CHUNK
    require(isinstance(name, str) and re.fullmatch(r"categories/([a-z]{2})/([a-z0-9-]+)\.md", name),
            f"unsafe or unexpected path: {name}")
    vertical, identifier = name.split("/")[1:]
    require(identifier[:-3].split("-", 1)[0] == vertical, "category path/vertical mismatch")
    return MAX_RESOURCE


def file_hash(path, maximum):
    require(path.is_file() and not path.is_symlink(), f"not a regular file: {path}")
    require(path.stat().st_size <= maximum, f"oversized file: {path}")
    result, size = hashlib.sha256(), 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK):
            size += len(chunk)
            require(size <= maximum, "file grew beyond limit")
            result.update(chunk)
    return {"digest": "sha256:" + result.hexdigest(), "size": size}


def descriptor(content, media_type):
    return {"mediaType": media_type, "digest": "sha256:" + sha256(content), "size": len(content)}


def header(name, size):
    info = tarfile.TarInfo(name)
    info.size, info.mode = size, 0o644
    return info


def bundle_manifest(metadata, files, source_commit, lock_sha256):
    require(re.fullmatch(r"[0-9a-f]{40}", source_commit), "source commit must be a full Git SHA-1")
    require(re.fullmatch(r"[0-9a-f]{64}", lock_sha256), "invalid selection lock checksum")
    bundle = {
        "package_format": FORMAT,
        "dataset": {"name": "shopify-categories", "version": metadata["dataset_version"],
                    "selection": metadata["selection"], "metadata": "dataset.json"},
        "source": {"repository": REPOSITORY, "commit": source_commit, "taxonomy": metadata["source"]},
        "requirements": {"resource_apis": [API], "capabilities": metadata["compatibility"]["required_server_features"]},
        "resources": {key: metadata[key] for key in
                      ("resources", "roots", "manifest_bytes", "ordering", "inventory_sha256")},
        "provenance": {"selection_lock_sha256": lock_sha256, "compatibility": metadata["compatibility"]},
        "licenses": list(datasets.LICENSES), "files": files,
        "file_count": len(files) + 1, "expanded_bytes": 0,
    }
    # Account for the manifest itself without a self-referential checksum.
    for _ in range(10):
        size = sum(item["bytes"] for item in files) + len(encoded(bundle))
        if size == bundle["expanded_bytes"]:
            return bundle
        bundle["expanded_bytes"] = size
    raise ValueError("expanded-size accounting did not converge")


def oci_manifest(bundle, archive):
    return {
        "schemaVersion": 2, "mediaType": OCI_MANIFEST, "artifactType": ARTIFACT,
        "config": descriptor(b"{}", EMPTY),
        "layers": [{"mediaType": LAYER, **archive,
                    "annotations": {"org.opencontainers.image.title": "seed.tar.gz"}}],
        "annotations": {"org.opencontainers.image.source": REPOSITORY,
                        "org.opencontainers.image.revision": bundle["source"]["commit"],
                        "org.opencontainers.image.version": bundle["dataset"]["version"],
                        "org.opencontainers.image.licenses": "MIT",
                        "dev.gitstore.seed.selection": bundle["dataset"]["selection"]},
    }


def output_path(output):
    output = output.resolve()
    require(not output.exists(), f"output already exists: {output}")
    require(not output.is_relative_to(ROOT) or output.is_relative_to(ROOT / "dist"),
            "repository outputs must be under dist/")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def write_package(output, metadata, inputs, source_commit, lock_sha256):
    """Package an exact mapping of allowed paths to bytes or local source Paths."""
    output = output_path(output)
    inputs = dict(inputs, **{"dataset.json": encoded(metadata)})
    files = []
    for name, content in sorted(inputs.items()):
        maximum = file_limit(name)
        if isinstance(content, Path):
            item = file_hash(content, maximum)
        else:
            require(len(content) <= maximum, "oversized content")
            item = {"digest": "sha256:" + sha256(content), "size": len(content)}
        files.append({"path": name, "bytes": item["size"], "sha256": item["digest"][7:]})
    bundle = bundle_manifest(metadata, files, source_commit, lock_sha256)
    require(bundle["file_count"] <= MAX_FILES and bundle["expanded_bytes"] <= MAX_EXPANDED, "bundle exceeds limits")
    with tempfile.TemporaryDirectory(prefix=".seed-package-", dir=output.parent) as temporary:
        staging = Path(temporary) / "package"
        staging.mkdir()
        archive = staging / "seed.tar.gz"
        with archive.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                                                     compresslevel=9, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as tar:
                content = encoded(bundle)
                tar.addfile(header("bundle.json", len(content)), io.BytesIO(content))
                for item in files:
                    content = inputs[item["path"]]
                    content = content.read_bytes() if isinstance(content, Path) else content
                    require(len(content) == item["bytes"] and sha256(content) == item["sha256"], "input changed while packaging")
                    tar.addfile(header(item["path"], len(content)), io.BytesIO(content))
        archive_info = file_hash(archive, MAX_ARCHIVE)
        # Full verification before exposing a completed package.
        verify_archive(archive, archive_info["digest"])
        manifest_bytes = encoded(oci_manifest(bundle, archive_info))
        manifest_info = descriptor(manifest_bytes, OCI_MANIFEST)
        blobs = staging / "blobs/sha256"
        blobs.mkdir(parents=True)
        (blobs / sha256(b"{}")).write_bytes(b"{}")
        (blobs / sha256(manifest_bytes)).write_bytes(manifest_bytes)
        shutil.copyfile(archive, blobs / archive_info["digest"][7:])
        (staging / "manifest.json").write_bytes(manifest_bytes)
        (staging / "oci-layout").write_bytes(encoded({"imageLayoutVersion": "1.0.0"}))
        index = {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [{
            **manifest_info, "artifactType": ARTIFACT, "annotations": {
                "org.opencontainers.image.ref.name": metadata["dataset_version"] + "-" + metadata["selection"]}}]}
        (staging / "index.json").write_bytes(encoded(index))
        (staging / "checksums.json").write_bytes(encoded({
            "package_format": FORMAT, "archive": {"mediaType": LAYER, **archive_info},
            "oci_manifest": manifest_info}))
        staging.rename(output)
    return {"archive": archive_info, "oci_manifest": manifest_info}


def decompress(archive, destination):
    decoder = zlib.decompressobj(31)
    total = 0
    with archive.open("rb") as source:
        require(source.read(10) == GZIP_HEADER, "noncanonical gzip header")
        source.seek(0)
        while compressed := source.read(CHUNK):
            require(not decoder.eof, "trailing gzip member or data")
            while compressed:
                chunk = decoder.decompress(compressed, CHUNK)
                total += len(chunk)
                require(total <= MAX_TAR, "tar expansion exceeds limit")
                destination.write(chunk)
                require(not decoder.unused_data, "trailing gzip member or data")
                compressed = decoder.unconsumed_tail
    require(decoder.eof, "truncated gzip")
    destination.seek(0)
    return total


def check_requirements(bundle, capabilities=None, resource_apis=None):
    require(bundle["package_format"] == FORMAT, "unsupported package format")
    requirements = bundle["requirements"]
    keys(requirements, "resource_apis capabilities")
    require(requirements["resource_apis"] == [API], "unsupported resource API")
    caps = requirements["capabilities"]
    require(isinstance(caps, list) and caps and all(isinstance(c, str) and c for c in caps)
            and len(set(caps)) == len(caps) and CAPABILITY in caps, "invalid required capabilities")
    require((capabilities is None) == (resource_apis is None), "supply both target capabilities and resource APIs")
    if capabilities is not None:
        require(set(caps) <= set(capabilities), "target lacks required capabilities")
        require(set(requirements["resource_apis"]) <= set(resource_apis), "target lacks required resource APIs")


def validate_manifest(bundle, content, capabilities, resource_apis):
    keys(bundle, "package_format dataset source requirements resources provenance licenses files file_count expanded_bytes")
    check_requirements(bundle, capabilities, resource_apis)
    keys(bundle["resources"], "resources roots manifest_bytes ordering inventory_sha256")
    integer(bundle["resources"]["resources"], MAX_FILES)
    integer(bundle["resources"]["roots"], MAX_FILES)
    integer(bundle["resources"]["manifest_bytes"], MAX_EXPANDED)
    digest("sha256:" + bundle["resources"]["inventory_sha256"])
    keys(bundle["dataset"], "name version selection metadata")
    require(bundle["dataset"]["name"] == "shopify-categories" and bundle["dataset"]["metadata"] == "dataset.json", "unsupported dataset")
    require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?", bundle["dataset"]["version"]), "invalid dataset version")
    require(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", bundle["dataset"]["selection"]), "invalid selection")
    keys(bundle["source"], "repository commit taxonomy")
    require(bundle["source"]["repository"] == REPOSITORY and re.fullmatch(r"[0-9a-f]{40}", bundle["source"]["commit"]), "invalid source provenance")
    require(bundle["licenses"] == list(datasets.LICENSES), "required license notices missing")
    integer(bundle["file_count"], MAX_FILES)
    integer(bundle["expanded_bytes"], MAX_EXPANDED)
    require(isinstance(bundle["files"], list) and len(bundle["files"]) + 1 == bundle["file_count"], "file count mismatch")
    expected, expanded = {}, len(content)
    for item in bundle["files"]:
        keys(item, "path bytes sha256")
        name = item["path"]
        require(name != "bundle.json" and name not in expected, "duplicate or self-referential inventory path")
        integer(item["bytes"], file_limit(name))
        digest("sha256:" + item["sha256"])
        expected[name] = item
        expanded += item["bytes"]
    require(list(expected) == sorted(expected), "file inventory is not sorted")
    require(expanded == bundle["expanded_bytes"], "expanded size mismatch")
    require({"dataset.json", *datasets.LICENSES} <= expected.keys(), "metadata or licenses missing")
    return expected


def verify_resources(metadata, bundle, actual, read_document):
    from jsonschema import Draft202012Validator
    compatibility = bundle["provenance"]["compatibility"]
    keys(bundle["provenance"], "compatibility selection_lock_sha256")
    digest("sha256:" + bundle["provenance"]["selection_lock_sha256"])
    schema = (ROOT / "schemas/category.schema.json").read_bytes()
    require(sha256(schema) == compatibility["schema_sha256"], "unsupported category schema")
    validator = Draft202012Validator(json.loads(schema))
    keys(metadata, "format_version dataset_version ordering dependency_closure source compatibility licenses selection resources roots verticals manifest_bytes ids_sha256 inventory_sha256 rationale inventory")
    require(type(metadata["format_version"]) is int and metadata["format_version"] == 1, "unsupported dataset metadata version")
    require(metadata["compatibility"] == compatibility, "compatibility metadata mismatch")
    require(compatibility["resource_api_version"] == API and compatibility["resource_kind"] == "CategoryTaxonomy"
            and compatibility["required_server_features"] == bundle["requirements"]["capabilities"], "inconsistent resource requirements")
    require(metadata["source"] == bundle["source"]["taxonomy"], "taxonomy provenance mismatch")
    require(metadata["source"]["locale"] == "en" and metadata["source"]["version"] == "2026-11", "unsupported taxonomy source")
    require(metadata["dataset_version"] == bundle["dataset"]["version"] and metadata["selection"] == bundle["dataset"]["selection"], "dataset identity mismatch")
    require({key: metadata[key] for key in bundle["resources"]} == bundle["resources"], "resource summary mismatch")
    records = metadata["inventory"]
    require(isinstance(records, list) and len(records) == metadata["resources"] and records, "resource count mismatch")
    require(sha256(encoded(records)) == metadata["inventory_sha256"], "resource inventory checksum mismatch")
    require(sha256(("\n".join(r["id"] for r in records) + "\n").encode()) == metadata["ids_sha256"], "membership checksum mismatch")
    names, paths, depths, order, verticals = set(), set(), {}, [], collections.Counter()
    size, roots = 0, 0
    available = set(actual)
    for record in records:
        keys(record, "id name title path parent bytes sha256")
        name, path, parent = record["name"], record["path"], record["parent"]
        require(path == f"categories/{record['id'].split('-', 1)[0]}/{record['id']}.md" and name == "shopify-" + record["id"], "resource identity/path mismatch")
        require(path in actual and name not in names and path not in paths, "missing or duplicate resource")
        require(parent is None or parent in names, "missing parent, cycle, or incorrect dependency order")
        require({key: record[key] for key in ("bytes", "sha256")} ==
                {key: actual[path][key] for key in ("bytes", "sha256")}, "resource file checksum mismatch")
        document, body = read_document(path)
        validator.validate(document)
        require(document["apiVersion"] == API and document["kind"] == "CategoryTaxonomy", "invalid resource kind/API")
        require(set(document["metadata"]) == {"name", "annotations"} and document["metadata"]["name"] == name, "unexpected metadata or namespace")
        require(set(document["spec"]) == ({"title", "parentRef"} if parent else {"title"}) and document["spec"]["title"] == record["title"], "unexpected spec, media or title")
        if parent:
            require(document["spec"]["parentRef"] == {"apiVersion": API, "kind": "CategoryTaxonomy", "name": parent}, "invalid parent reference or namespace")
        require(document["metadata"]["annotations"]["taxonomy.shopify.com/id"] == "gid://shopify/TaxonomyCategory/" + record["id"]
                and document["metadata"]["annotations"]["taxonomy.shopify.com/version"] == metadata["source"]["version"], "source annotation mismatch")
        require(body.strip().startswith("# " + taxonomy.escaped(record["title"])) and "## Source" in body, "missing description/attribution")
        for target in re.findall(r"\]\(([^)]+)\)", body):
            if "://" not in target:
                require(posixpath.normpath(posixpath.join(posixpath.dirname(path), target)) in available, "broken local navigation")
        depths[name] = depths[parent] + 1 if parent else 0
        order.append((depths[name], record["id"]))
        names.add(name)
        paths.add(path)
        verticals[record["id"].split("-", 1)[0]] += 1
        size += record["bytes"]
        roots += parent is None
    require(order == sorted(order) and metadata["ordering"] == datasets.ORDER, "noncanonical dependency ordering")
    require(metadata["roots"] == roots and metadata["manifest_bytes"] == size and metadata["verticals"] == dict(verticals), "resource accounting mismatch")
    require(paths | {"dataset.json", *datasets.LICENSES} == available, "unexpected payload content")
    require(metadata["licenses"] == [actual[name] for name in datasets.LICENSES], "license inventory mismatch")
    for name in datasets.LICENSES:
        require(actual[name]["bytes"] > 0, "empty license")


def verify_archive(archive, expected_digest, extract_to=None, capabilities=None, resource_apis=None):
    """Verify before extraction; never use extractall or follow archive paths/links."""
    archive_info = file_hash(archive, MAX_ARCHIVE)
    require(archive_info["digest"] == digest(expected_digest), "archive digest mismatch")
    output = output_path(extract_to) if extract_to is not None else None
    with tempfile.TemporaryFile() as raw:
        tar_size = decompress(archive, raw)
        actual, offsets = {}, {}
        bundle, expected, metadata, expanded = None, None, None, 0
        while True:
            block = raw.read(512)
            require(len(block) == 512, "truncated tar header/footer")
            if not block.strip(b"\0"):
                footer = 512
                while chunk := raw.read(CHUNK):
                    require(not chunk.strip(b"\0"), "nonzero trailing tar data")
                    footer += len(chunk)
                expected_size = ((raw.tell() - footer + 1024 + 10239) // 10240) * 10240
                require(footer >= 1024 and tar_size == expected_size, "noncanonical tar footer")
                break
            info = tarfile.TarInfo.frombuf(block, "ascii", "strict")
            name = info.name
            integer(info.size, file_limit(name))
            require(block == header(name, info.size).tobuf(format=tarfile.USTAR_FORMAT), "unsafe or noncanonical tar entry")
            require(name not in actual and len(actual) < MAX_FILES, "duplicate tar entry or too many files")
            offset = raw.tell()
            content = raw.read(info.size)
            require(len(content) == info.size, "truncated tar content")
            padding = raw.read(-info.size % 512)
            require(len(padding) == -info.size % 512 and not padding.strip(b"\0"), "invalid tar padding")
            expanded += info.size
            require(expanded <= MAX_EXPANDED, "expanded content exceeds limit")
            if bundle is None:
                require(name == "bundle.json", "bundle.json must be first")
                bundle = json_value(content)
                expected = validate_manifest(bundle, content, capabilities, resource_apis)
                continue
            require(name in expected, "unexpected tar entry")
            item = {"path": name, "bytes": info.size, "sha256": sha256(content)}
            require(item == expected[name], "payload checksum/size mismatch")
            actual[name], offsets[name] = item, (offset, info.size)
            if name == "dataset.json":
                metadata = json_value(content)
            elif name in datasets.LICENSES:
                text = content.decode("utf-8")
                require(all(c.isprintable() or c in "\n\r\t" for c in text)
                        and "Permission is hereby granted" in text and "THE SOFTWARE IS PROVIDED" in text,
                        "invalid MIT license notice")
        require(bundle is not None and metadata is not None and actual.keys() == expected.keys(), "incomplete payload")
        require(list(actual) == list(expected), "noncanonical archive order")
        require(len(actual) + 1 == bundle["file_count"] and expanded == bundle["expanded_bytes"], "payload accounting mismatch")
        class UniqueLoader(getattr(yaml, "CSafeLoader", yaml.SafeLoader)):
            pass

        def unique_mapping(loader, node):
            result = {}
            for key_node, value_node in node.value:
                key = loader.construct_object(key_node)
                require(isinstance(key, str) and key not in result, "duplicate or non-string YAML key")
                result[key] = loader.construct_object(value_node)
            return result

        UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)

        def read_document(path):
            offset, size = offsets[path]
            raw.seek(offset)
            text = raw.read(size).decode("utf-8")
            require(text.startswith("---\n") and len(parts := text.split("---\n", 2)) == 3, "invalid resource frontmatter")
            return yaml.load(parts[1], Loader=UniqueLoader), parts[2]

        verify_resources(metadata, bundle, actual, read_document)
        if output is not None:
            with tempfile.TemporaryDirectory(prefix=".seed-extract-", dir=output.parent) as temporary:
                staging = Path(temporary) / "payload"
                staging.mkdir()
                (staging / "bundle.json").write_bytes(encoded(bundle))
                for name, (offset, size) in offsets.items():
                    target = staging / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    raw.seek(offset)
                    target.write_bytes(raw.read(size))
                staging.rename(output)
    return bundle


def verify_package(package, expected_oci=None, **kwargs):
    # Verify the entire OCI envelope before allowing even inspection extraction.
    extract_to = kwargs.pop("extract_to", None)
    def read(name):
        path = package / name
        require(not any(p.is_symlink() for p in [path, *path.parents] if p != package.parent), "layout contains symlink")
        file_hash(path, MAX_METADATA)
        return path.read_bytes()
    require(json_value(read("oci-layout")) == {"imageLayoutVersion": "1.0.0"}, "unsupported OCI layout")
    checksums = json_value(read("checksums.json"))
    keys(checksums, "package_format archive oci_manifest")
    require(checksums["package_format"] == FORMAT, "unsupported checksum format")
    manifest_bytes = read("manifest.json")
    manifest_info = descriptor(manifest_bytes, OCI_MANIFEST)
    require(checksums["oci_manifest"] == manifest_info, "OCI manifest checksum mismatch")
    if expected_oci is not None:
        require(manifest_info["digest"] == digest(expected_oci), "OCI manifest digest mismatch")
    manifest = json_value(manifest_bytes)
    require(isinstance(manifest, dict) and manifest.get("artifactType") == ARTIFACT and manifest.get("mediaType") == OCI_MANIFEST,
            "unsupported OCI artifact/media type")
    archive_info = file_hash(package / "seed.tar.gz", MAX_ARCHIVE)
    require(checksums["archive"] == {"mediaType": LAYER, **archive_info}, "archive descriptor mismatch")
    bundle = verify_archive(package / "seed.tar.gz", archive_info["digest"], **kwargs)
    require(manifest == oci_manifest(bundle, archive_info), "OCI metadata/layer/config mismatch")
    require(read("blobs/sha256/" + manifest_info["digest"][7:]) == manifest_bytes, "OCI manifest blob mismatch")
    require(read("blobs/sha256/" + sha256(b"{}")) == b"{}", "OCI empty config mismatch")
    archive_blob = package / "blobs/sha256" / archive_info["digest"][7:]
    require(file_hash(archive_blob, MAX_ARCHIVE) == archive_info, "OCI archive blob mismatch")
    index = {"schemaVersion": 2, "mediaType": OCI_INDEX, "manifests": [{
        **manifest_info, "artifactType": ARTIFACT, "annotations": {
            "org.opencontainers.image.ref.name": bundle["dataset"]["version"] + "-" + bundle["dataset"]["selection"]}}]}
    require(json_value(read("index.json")) == index, "OCI index mismatch")
    expected = {"oci-layout", "index.json", "checksums.json", "manifest.json", "seed.tar.gz",
                "blobs/sha256/" + manifest_info["digest"][7:], "blobs/sha256/" + sha256(b"{}"),
                "blobs/sha256/" + archive_info["digest"][7:]}
    found, directories = set(), set()
    for path in package.rglob("*"):
        require(not path.is_symlink(), "layout contains symlink")
        name = path.relative_to(package).as_posix()
        if path.is_dir():
            directories.add(name)
        else:
            require(path.is_file(), "layout contains non-regular file")
            found.add(name)
    require(found == expected and directories == {"blobs", "blobs/sha256"}, "unexpected layout content")
    if extract_to is not None:
        verify_archive(package / "seed.tar.gz", archive_info["digest"], extract_to=extract_to, **kwargs)
    return bundle


def build(selection, output):
    # Source provenance must describe committed inputs, including selection locks.
    paths = ["categories", "upstream", "editorial", "schemas", "datasets", "tools", "requirements.txt", "source.lock.json", *datasets.LICENSES]
    status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all", "--", *paths], cwd=ROOT)
    require(not status.strip(), "commit canonical, selection and builder inputs before building")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    taxonomy.validate()
    current, inventories = datasets.snapshot()
    datasets.verify_lock(current)
    records = inventories[selection]
    metadata = datasets.export_metadata(selection, current, records)
    inputs = {item["path"]: ROOT / item["path"] for item in [*records, *current["licenses"]]}
    return write_package(output, metadata, inputs, revision,
                         sha256((ROOT / "datasets/selections.lock.json").read_bytes()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build")
    builder.add_argument("--selection", choices=("starter", "full"), default="starter")
    builder.add_argument("--output", type=Path, required=True)
    verifier = commands.add_parser("verify")
    sources = verifier.add_mutually_exclusive_group(required=True)
    sources.add_argument("--package", type=Path)
    sources.add_argument("--archive", type=Path)
    verifier.add_argument("--archive-digest")
    verifier.add_argument("--oci-digest")
    verifier.add_argument("--extract-to", type=Path)
    verifier.add_argument("--target-capability", action="append")
    verifier.add_argument("--target-api", action="append")
    args = parser.parse_args()
    try:
        if args.command == "build":
            print(encoded(build(args.selection, args.output)).decode(), end="")
        else:
            kwargs = dict(extract_to=args.extract_to, capabilities=args.target_capability, resource_apis=args.target_api)
            if args.package is not None:
                require(args.archive_digest is None, "archive digest is for archive verification")
                bundle = verify_package(args.package, args.oci_digest, **kwargs)
            else:
                require(args.archive_digest is not None and args.oci_digest is None, "archive verification requires an archive digest, not an OCI digest")
                bundle = verify_archive(args.archive, args.archive_digest, **kwargs)
            print(f"PASS: {bundle['dataset']['selection']}; {bundle['resources']['resources']} resources; integrity verified offline")
    except (ValueError, AssertionError, KeyError, TypeError, OSError, EOFError, UnicodeError,
            RecursionError, tarfile.TarError, zlib.error, yaml.YAMLError, ValidationError) as error:
        parser.exit(1, f"ERROR: {error}\n")


if __name__ == "__main__":
    main()
