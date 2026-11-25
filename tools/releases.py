#!/usr/bin/env python3
"""Release-time builder and resumable publisher; live CLI runs only in trusted Actions."""
import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import zlib
from pathlib import Path

import bundles
import datasets
from datasets import ROOT, encoded, require, sha256
from release_api import GitHub, REPO

WORKFLOW = ".github/workflows/release.yml"
REGISTRY = "ghcr.io/gitstore-dev/data"
RELEASE_FORMAT = "gitstore.seed.release/v1"
PROVENANCE = "provenance.jsonl"
MAX_PROOF = 16 * 1024 * 1024
BASE_METADATA = {"release.json", "SHA256SUMS"}


def validate_context(env):
    for key, expected in {
        "GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": REPO,
        "GITHUB_REF": "refs/heads/main", "GITHUB_WORKFLOW_REF": REPO + "/" + WORKFLOW + "@refs/heads/main",
        "RUNNER_ENVIRONMENT": "github-hosted",
    }.items():
        require(env.get(key) == expected, f"untrusted publication context: {key}")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", env.get("GITHUB_SHA", ""))), "workflow revision missing")


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def source_spec(tag):
    require(bool(re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[a-z0-9]+(?:[.-][a-z0-9]+)*)?", tag)), "invalid release tag")
    commit = git("rev-parse", "--verify", "refs/tags/" + tag + "^{commit}").decode().strip()
    subprocess.run(["git", "merge-base", "--is-ancestor", commit, "origin/main"], cwd=ROOT, check=True)
    definitions = json.loads(git("show", commit + ":datasets/definitions.json"))
    lock_bytes = git("show", commit + ":datasets/selections.lock.json")
    lock = json.loads(lock_bytes)
    require(definitions["dataset_version"] == lock["dataset_version"] == tag[1:], "tag and dataset version differ")
    version = git("show", commit + ":version.txt").decode().strip()
    manifest = json.loads(git("show", commit + ":.release-please-manifest.json"))
    require(version == manifest["."] == tag[1:], "tag and release automation version differ")
    require(set(definitions["selections"]) == set(lock["selections"]) == {"starter", "full"}, "release requires starter and full")
    require(lock["source"] == json.loads(git("show", commit + ":source.lock.json")), "source and selection locks differ")
    require(all(definitions["selections"][name][key] == lock["selections"][name][key]
                for name in ("starter", "full") for key in ("resources", "roots")), "selection definitions and lock differ")
    return {"tag": tag, "version": tag[1:], "commit": commit, "lock_sha256": sha256(lock_bytes), "lock": lock}


def names(version):
    return {selection: {"archive": f"gitstore-categories-{version}-{selection}.tar.gz",
                        "manifest": f"gitstore-categories-{version}-{selection}.oci.json"}
            for selection in ("starter", "full")}


def asset_names(version):
    return BASE_METADATA | {PROVENANCE} | {name for pair in names(version).values() for name in pair.values()}


def asset_limit(name):
    return bundles.MAX_ARCHIVE if name.endswith(".tar.gz") else MAX_PROOF


class Attestations:
    def verify(self, assets):
        proof = assets / PROVENANCE
        require(proof.is_file() and 0 < proof.stat().st_size <= MAX_PROOF, "producer provenance missing")
        files = sorted(p for p in assets.iterdir() if p.name != PROVENANCE)
        for path in files:
            subprocess.run([
                "gh", "attestation", "verify", str(path), "--bundle", str(proof), "--repo", REPO,
                "--signer-workflow", REPO + "/" + WORKFLOW, "--source-ref", "refs/heads/main",
                "--predicate-type", "https://slsa.dev/provenance/v1", "--deny-self-hosted-runners",
            ], check=True, stdout=subprocess.DEVNULL)


class Registry:
    def __init__(self, helper, repository=REGISTRY):
        self.helper, self.repository = str(helper), repository

    def run(self, command, tag, *args):
        result = subprocess.run([self.helper, command, "--repository", self.repository, "--tag", tag, *args],
                                check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def probe(self, tag):
        result = self.run("probe", tag)
        require(type(result["exists"]) is bool, "invalid registry probe")
        return bundles.digest(result["digest"]) if result["exists"] else None

    def publish(self, layout, tag, digest):
        self.run("publish", tag, "--layout", str(layout), "--digest", digest)

    def verify(self, layout, tag, digest):
        require(self.probe(tag) == digest, "published registry tag mismatch")
        with tempfile.TemporaryDirectory(prefix="seed-registry-readback-") as temporary:
            target = Path(temporary) / "layout"
            self.run("pull", tag, "--layout", str(target), "--digest", digest)
            for path in (layout / "blobs/sha256").iterdir():
                expected = bundles.file_hash(path, bundles.MAX_ARCHIVE)
                require(bundles.file_hash(target / "blobs/sha256" / path.name, bundles.MAX_ARCHIVE) == expected,
                        "registry content readback mismatch")
        require(self.probe(tag) == digest, "registry tag changed during verification")


def verify_layout(layout, spec, selection):
    bundle = bundles.verify_package(layout)
    require(bundle["source"]["commit"] == spec["commit"] and bundle["source"]["taxonomy"] == spec["lock"]["source"],
            "bundle source differs from release tag")
    require(bundle["dataset"]["version"] == spec["version"] and bundle["dataset"]["selection"] == selection,
            "bundle dataset identity differs from release")
    require(bundle["provenance"]["selection_lock_sha256"] == spec["lock_sha256"], "selection lock differs from source")
    require(bundle["provenance"]["compatibility"] == spec["lock"]["compatibility"], "compatibility differs from source")
    expected = spec["lock"]["selections"][selection]
    require(all(bundle["resources"][key] == expected[key] for key in
                ("resources", "roots", "manifest_bytes", "inventory_sha256")), "resource inventory differs from source")
    for notice in spec["lock"]["licenses"]:
        require(notice in bundle["files"], "license differs from source")
    return bundle


def inventory(spec, layouts, repository):
    selections, assets = [], []
    for selection in ("starter", "full"):
        layout = layouts[selection]
        bundle = verify_layout(layout, spec, selection)
        checksums = json.loads((layout / "checksums.json").read_bytes())
        pair = names(spec["version"])[selection]
        for kind, descriptor in [("archive", checksums["archive"]), ("manifest", checksums["oci_manifest"])]:
            assets.append({"name": pair[kind], "sha256": descriptor["digest"][7:], "bytes": descriptor["size"]})
        tag = spec["version"] + "-" + selection
        selections.append({
            "selection": selection, "tag": tag, "reference": repository + ":" + tag,
            "digest_reference": repository + "@" + checksums["oci_manifest"]["digest"],
            "oci_manifest": checksums["oci_manifest"], "archive": checksums["archive"],
            "archive_asset": pair["archive"], "manifest_asset": pair["manifest"],
            "requirements": bundle["requirements"], "resources": bundle["resources"],
            "file_count": bundle["file_count"], "expanded_bytes": bundle["expanded_bytes"],
        })
    source = spec["lock"]["source"]
    return {"format": RELEASE_FORMAT, "package_format": bundles.FORMAT, "dataset_version": spec["version"],
            "tag": spec["tag"], "source_commit": spec["commit"],
            "taxonomy": {key: source[key] for key in ("version", "locale", "release_url", "asset_sha256", "normalized_sha256")},
            "producer": {"repository": REPO, "workflow": WORKFLOW, "ref": "refs/heads/main"},
            "builder": {"python": platform.python_version(), "zlib": zlib.ZLIB_RUNTIME_VERSION, "oras_go": "v3.0.0"},
            "registry": repository, "selections": selections, "assets": sorted(assets, key=lambda item: item["name"])}


def checksum_text(assets):
    return "".join(f"{sha256(path.read_bytes())}  {path.name}\n" for path in sorted(assets.iterdir())
                   if path.name not in {"SHA256SUMS", PROVENANCE}).encode()


def stage_assets(spec, layouts, assets, repository):
    assets.mkdir(parents=True, exist_ok=False)
    for selection, pair in names(spec["version"]).items():
        shutil.copyfile(layouts[selection] / "seed.tar.gz", assets / pair["archive"])
        shutil.copyfile(layouts[selection] / "manifest.json", assets / pair["manifest"])
    value = inventory(spec, layouts, repository)
    (assets / "release.json").write_bytes(encoded(value))
    (assets / "SHA256SUMS").write_bytes(checksum_text(assets))
    return value


def restore_layout(assets, record, target):
    target.mkdir(parents=True, exist_ok=False)
    archive = assets / record["archive_asset"]
    manifest = (assets / record["manifest_asset"]).read_bytes()
    descriptor = bundles.descriptor(manifest, bundles.OCI_MANIFEST)
    require(descriptor == record["oci_manifest"], "release OCI digest/size mismatch")
    archive_info = bundles.file_hash(archive, bundles.MAX_ARCHIVE)
    require({"mediaType": bundles.LAYER, **archive_info} == record["archive"], "release archive digest/size mismatch")
    blobs = target / "blobs/sha256"
    blobs.mkdir(parents=True)
    (target / "manifest.json").write_bytes(manifest)
    shutil.copyfile(archive, target / "seed.tar.gz")
    (blobs / descriptor["digest"][7:]).write_bytes(manifest)
    (blobs / sha256(b"{}")).write_bytes(b"{}")
    shutil.copyfile(archive, blobs / archive_info["digest"][7:])
    (target / "oci-layout").write_bytes(encoded({"imageLayoutVersion": "1.0.0"}))
    (target / "index.json").write_bytes(encoded({"schemaVersion": 2, "mediaType": bundles.OCI_INDEX,
        "manifests": [{**descriptor, "artifactType": bundles.ARTIFACT,
                       "annotations": {"org.opencontainers.image.ref.name": record["tag"]}}]}))
    (target / "checksums.json").write_bytes(encoded({"package_format": bundles.FORMAT,
        "archive": {"mediaType": bundles.LAYER, **archive_info}, "oci_manifest": descriptor}))


def verify_assets(assets, spec, registry, attestations, layouts_root):
    require({p.name for p in assets.iterdir()} == asset_names(spec["version"]), "release checksums, provenance or payloads missing/unexpected")
    require((assets / "SHA256SUMS").read_bytes() == checksum_text(assets), "release checksums mismatch")
    attestations.verify(assets)  # Establish producer authority before trusting inventory.
    value = bundles.json_value((assets / "release.json").read_bytes())
    bundles.keys(value, "format package_format dataset_version tag source_commit taxonomy producer builder registry selections assets")
    require(value["format"] == RELEASE_FORMAT and value["package_format"] == bundles.FORMAT, "unsupported release format")
    require(value["tag"] == spec["tag"] and value["dataset_version"] == spec["version"]
            and value["source_commit"] == spec["commit"] and value["registry"] == registry.repository,
            "release identity/registry differs from source")
    require(value["producer"] == {"repository": REPO, "workflow": WORKFLOW, "ref": "refs/heads/main"}, "untrusted producer")
    require([r["selection"] for r in value["selections"]] == ["starter", "full"], "release selections missing")
    layouts = {}
    for record in value["selections"]:
        selection = record["selection"]
        expected_tag = spec["version"] + "-" + selection
        require(record["tag"] == expected_tag and record["reference"] == registry.repository + ":" + expected_tag
                and record["digest_reference"] == registry.repository + "@" + record["oci_manifest"]["digest"], "invalid OCI reference")
        require(record["archive_asset"] == names(spec["version"])[selection]["archive"]
                and record["manifest_asset"] == names(spec["version"])[selection]["manifest"], "invalid release asset names")
        target = layouts_root / selection
        restore_layout(assets, record, target)
        bundle = verify_layout(target, spec, selection)
        require(all(record[key] == bundle[key] for key in ("requirements", "resources", "expanded_bytes", "file_count")), "inventory summary differs from bundle")
        layouts[selection] = target
    bundles.keys(value["taxonomy"], "version locale release_url asset_sha256 normalized_sha256")
    require(value["taxonomy"] == {key: spec["lock"]["source"][key] for key in value["taxonomy"]}, "taxonomy differs from source")
    expected_assets = []
    for selection, pair in names(spec["version"]).items():
        for name in pair.values():
            content = (assets / name).read_bytes()
            expected_assets.append({"name": name, "sha256": sha256(content), "bytes": len(content)})
    require(value["assets"] == sorted(expected_assets, key=lambda item: item["name"]), "release asset inventory differs")
    return value, layouts


def remote_assets(github, release, version):
    require(release["tag_name"] == "v" + version, "GitHub release tag differs")
    found = github.assets(release)
    require(found.keys() <= asset_names(version), "unexpected release assets; refusing to alter release")
    return found


def download_complete(github, release, spec, destination):
    found = remote_assets(github, release, spec["version"])
    if found.keys() != asset_names(spec["version"]) or any(a["state"] != "uploaded" for a in found.values()):
        require(release["draft"], "published release missing checksums, provenance or payloads")
        return False
    destination.mkdir(parents=True, exist_ok=False)
    for name, asset in found.items():
        (destination / name).write_bytes(github.download(asset, asset_limit(name)))
    return True


def preflight_registry(value, registry):
    for record in value["selections"]:
        actual = registry.probe(record["tag"])
        require(actual is None or actual == record["oci_manifest"]["digest"], "registry digest conflict; version must not be replaced")


def upload_assets(github, release, assets, version):
    found = remote_assets(github, release, version)
    for name, existing in found.items():
        if existing["state"] == "starter" and existing["size"] == 0:
            require(release["draft"], "cannot repair a published release")
            continue
        require(github.download(existing, asset_limit(name)) == (assets / name).read_bytes(), "release asset conflict: " + name)
    for path in sorted(assets.iterdir(), key=lambda path: (path.name != "release.json", path.name)):
        content = path.read_bytes()
        existing = found.get(path.name)
        if existing and existing["state"] == "starter" and existing["size"] == 0:
            require(release["draft"], "cannot repair a published release")
            print("Removing incomplete zero-byte draft upload: " + path.name, file=sys.stderr)
            github.delete_incomplete(release, existing)
            existing = None
        if existing:
            require(github.download(existing, asset_limit(path.name)) == content, "release asset conflict: " + path.name)
            continue
        require(release["draft"], "cannot add missing assets to a published release")
        try:
            github.upload(release, path.name, content)
        except urllib.error.HTTPError as error:
            if error.code != 422:
                raise
            # A lost response/concurrent retry may already have uploaded this exact asset.
            existing = github.assets(release).get(path.name)
            require(existing is not None and github.download(existing, asset_limit(path.name)) == content,
                    "conflicting or failed asset upload: " + path.name)


def publish(github, registry, attestations, spec, assets, layouts_root):
    require(github.source_revision(spec["tag"]) == spec["commit"], "remote release tag moved")
    value, layouts = verify_assets(assets, spec, registry, attestations, layouts_root)
    preflight_registry(value, registry)
    release = github.release(spec["tag"])
    if release is None:
        release = github.create(spec)
    upload_assets(github, release, assets, spec["version"])
    # Public version tags only ever point to verified, signed payloads.
    for record in value["selections"]:
        registry.publish(layouts[record["selection"]], record["tag"], record["oci_manifest"]["digest"])
        registry.verify(layouts[record["selection"]], record["tag"], record["oci_manifest"]["digest"])
    current = github.release(spec["tag"])
    found = remote_assets(github, current, spec["version"])
    require(found.keys() == asset_names(spec["version"]), "incomplete release after upload")
    for name, asset in found.items():
        require(github.download(asset, asset_limit(name)) == (assets / name).read_bytes(), "release readback mismatch")
    require(github.source_revision(spec["tag"]) == spec["commit"], "remote tag moved during publication")
    if current["draft"]:
        github.publish(current)
    print("Published/verified " + spec["tag"])


def build_release(spec, work, repository):
    source = work / "source"
    subprocess.run(["git", "worktree", "add", "--detach", str(source), spec["commit"]], cwd=ROOT, check=True)
    build_env = {key: value for key, value in os.environ.items() if key not in {
        "GH_TOKEN", "GITHUB_TOKEN", "REGISTRY_USERNAME", "REGISTRY_TOKEN",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_RUNTIME_TOKEN"}}
    try:
        subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=source, env=build_env, check=True)
        layouts = {}
        for selection in ("starter", "full"):
            output = work / "built" / selection
            subprocess.run([sys.executable, "tools/bundles.py", "build", "--selection", selection, "--output", str(output)], cwd=source, env=build_env, check=True)
            layouts[selection] = output
        return stage_assets(spec, layouts, work / "assets", repository)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(source)], cwd=ROOT, check=True)


def prepare(github, registry, attestations, spec, work):
    work.mkdir(parents=True, exist_ok=False)
    release = github.release(spec["tag"])
    if release is not None and download_complete(github, release, spec, work / "assets"):
        value, _ = verify_assets(work / "assets", spec, registry, attestations, work / "restored")
        preflight_registry(value, registry)
        return False  # Retain exact signed bytes; do not rebuild or re-attest retries.
    # Only an incomplete draft or a new version gets a release-time build.
    value = build_release(spec, work, registry.repository)
    preflight_registry(value, registry)
    if release is not None:
        found = remote_assets(github, release, spec["version"])
        for name, asset in found.items():
            if name == PROVENANCE or (asset["state"] == "starter" and asset["size"] == 0):
                continue
            require(github.download(asset, asset_limit(name)) == (work / "assets" / name).read_bytes(), "draft asset conflict: " + name)
        if PROVENANCE in found and found[PROVENANCE]["state"] == "uploaded":
            (work / "assets" / PROVENANCE).write_bytes(github.download(found[PROVENANCE], MAX_PROOF))
            attestations.verify(work / "assets")
            return False
        if PROVENANCE in found:
            require(found[PROVENANCE]["state"] == "starter" and found[PROVENANCE]["size"] == 0,
                    "invalid unfinished provenance upload")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "publish"))
    parser.add_argument("--tag", required=True)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--registry-helper", required=True, type=Path)
    parser.add_argument("--provenance", type=Path)
    args = parser.parse_args()
    validate_context(os.environ)
    require(git("rev-parse", "HEAD").decode().strip() == os.environ["GITHUB_SHA"], "controller checkout differs from workflow revision")
    spec = source_spec(args.tag)
    github = GitHub(os.environ.get("GH_TOKEN"))
    require(github.request("GET", "")["full_name"] == REPO, "producer repository unavailable")
    require(github.source_revision(args.tag) == spec["commit"], "remote tag differs from source")
    registry, attestations = Registry(args.registry_helper.resolve()), Attestations()
    work = args.work_dir.resolve()
    if args.command == "prepare":
        needed = prepare(github, registry, attestations, spec, work)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write("needs_attestation=" + str(needed).lower() + "\n")
    else:
        if args.provenance is not None:
            bundles.file_hash(args.provenance, MAX_PROOF)
            content = args.provenance.read_bytes()
            require(0 < len(content) <= MAX_PROOF, "invalid producer proof size")
            target = work / "assets" / PROVENANCE
            require(not target.exists() or target.read_bytes() == content, "refusing to replace existing provenance")
            target.write_bytes(content)
        publish(github, registry, attestations, spec, work / "assets", work / "verified")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit("Publication stopped: " + str(error))
