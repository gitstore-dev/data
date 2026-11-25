"""Synthetic three-category releases and test doubles; never production signing."""
import copy
import io
import json
import urllib.error
from pathlib import Path

import bundles
import datasets
import releases
from bundle_fixture import make_fixture


def fixture_release(root, repository=releases.REGISTRY):
    make_fixture(root / "fixture")
    fixture = root / "fixture/package"
    original = bundles.verify_archive(fixture / "seed.tar.gz", json.loads((fixture / "checksums.json").read_bytes())["archive"]["digest"],
                                      extract_to=root / "fixture-inputs")
    metadata = json.loads((root / "fixture-inputs/dataset.json").read_bytes())
    lock = copy.deepcopy(datasets.read_json("datasets/selections.lock.json"))
    lock["dataset_version"] = metadata["dataset_version"]
    item = {key: metadata[key] for key in ("resources", "roots", "manifest_bytes", "verticals", "inventory_sha256", "ids_sha256", "rationale")}
    lock["selections"] = {selection: copy.deepcopy(item) for selection in ("starter", "full")}
    spec = {"tag": "v" + lock["dataset_version"], "version": lock["dataset_version"],
            "commit": original["source"]["commit"], "lock": lock, "lock_sha256": datasets.sha256(datasets.encoded(lock))}
    layouts = {}
    for selection in ("starter", "full"):
        value = datasets.export_metadata(selection, lock, metadata["inventory"])
        inputs = {item["path"]: datasets.ROOT / item["path"] for item in [*metadata["inventory"], *metadata["licenses"]]}
        output = root / "built" / selection
        bundles.write_package(output, value, inputs, spec["commit"], spec["lock_sha256"])
        layouts[selection] = output
    releases.stage_assets(spec, layouts, root / "assets", repository)
    TestProvenance.sign(root / "assets")
    return spec, root / "assets"


class TestProvenance:
    """Noncryptographic test double. The live CLI always uses gh attestation verify."""
    @staticmethod
    def subjects(assets):
        return {path.name: datasets.sha256(path.read_bytes()) for path in assets.iterdir() if path.name != releases.PROVENANCE}

    @classmethod
    def sign(cls, assets):
        (assets / releases.PROVENANCE).write_bytes(datasets.encoded({
            "kind": "SYNTHETIC-REHEARSAL-ONLY", "workflow": releases.REPO + "/" + releases.WORKFLOW,
            "ref": "refs/heads/main", "subjects": cls.subjects(assets)}))

    def verify(self, assets):
        proof = assets / releases.PROVENANCE
        datasets.require(proof.is_file(), "producer provenance missing")
        value = json.loads(proof.read_bytes())
        datasets.require(value["kind"] == "SYNTHETIC-REHEARSAL-ONLY"
                         and value["workflow"] == releases.REPO + "/" + releases.WORKFLOW
                         and value["ref"] == "refs/heads/main" and value["subjects"] == self.subjects(assets),
                         "untrusted synthetic provenance")


class FakeGitHub:
    def __init__(self, spec):
        self.spec, self.value, self.records = spec, None, {}
        self.uploads, self.deletions, self.publishes = [], [], 0
        self.fail_after = None
        self.authorized = True
        self.revision = spec["commit"]

    def check(self):
        if not self.authorized:
            error = urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", {}, io.BytesIO())
            error.close()
            raise error

    def source_revision(self, tag):
        self.check()
        return self.revision

    def release(self, tag):
        self.check()
        return copy.deepcopy(self.value)

    def create(self, spec):
        self.check()
        self.value = {"id": 1, "tag_name": spec["tag"], "draft": True}
        return self.release(spec["tag"])

    def assets(self, release):
        self.check()
        return {name: {key: value for key, value in item.items() if key != "content"} for name, item in self.records.items()}

    def download(self, asset, maximum):
        self.check()
        item = next(value for value in self.records.values() if value["id"] == asset["id"])
        datasets.require(item["state"] == "uploaded" and len(item["content"]) <= maximum, "incomplete/oversized asset")
        return item["content"]

    def upload(self, release, name, content):
        self.check()
        if name in self.records:
            error = urllib.error.HTTPError("https://uploads.github.com", 422, "Already exists", {}, io.BytesIO())
            error.close()
            raise error
        self.uploads.append(name)
        self.records[name] = {"id": len(self.uploads), "name": name, "size": len(content), "state": "uploaded", "content": content}
        if self.fail_after == name:
            self.fail_after = None
            raise ConnectionError("upload response lost after acceptance")
        return {key: value for key, value in self.records[name].items() if key != "content"}

    def delete_incomplete(self, release, asset):
        datasets.require(self.value["draft"] and asset["state"] == "starter" and asset["size"] == 0, "not an unfinished draft upload")
        self.deletions.append(asset["name"])
        del self.records[asset["name"]]

    def publish(self, release):
        self.publishes += 1
        self.value["draft"] = False
        return self.release(self.spec["tag"])


class FakeRegistry:
    def __init__(self, repository=releases.REGISTRY):
        self.repository, self.tags, self.uploads = repository, {}, []
        self.fail_after = None
        self.authorized = True

    def probe(self, tag):
        if not self.authorized:
            raise PermissionError("registry denied")
        return self.tags.get(tag)

    def publish(self, layout, tag, digest):
        existing = self.probe(tag)
        datasets.require(existing is None or existing == digest, "registry conflict")
        if existing is None:
            self.uploads.append(tag)
            self.tags[tag] = digest
        if self.fail_after == tag:
            self.fail_after = None
            raise ConnectionError("registry response lost after acceptance")

    def verify(self, layout, tag, digest):
        datasets.require(self.probe(tag) == digest, "registry readback mismatch")
