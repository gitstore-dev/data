import copy
import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bundles
import datasets
import release_api
import releases
from release_fakes import FakeGitHub, FakeRegistry, TestProvenance, fixture_release


class PublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.shared.cleanup)
        cls.spec, cls.canonical = fixture_release(Path(cls.shared.name).resolve())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.assets = self.root / "assets"
        shutil.copytree(self.canonical, self.assets)
        self.github, self.registry = FakeGitHub(self.spec), FakeRegistry()
        self.proof = TestProvenance()
        self.attempt = 0

    def publish(self):
        self.attempt += 1
        releases.publish(self.github, self.registry, self.proof, self.spec, self.assets, self.root / f"verified-{self.attempt}")

    def build(self, spec, work, repository):
        shutil.copytree(self.assets, work / "assets", ignore=shutil.ignore_patterns(releases.PROVENANCE))
        return json.loads((work / "assets/release.json").read_bytes())

    def test_complete_publication_and_retry_keep_exact_bytes(self):
        self.publish()
        self.assertFalse(self.github.value["draft"])
        self.assertEqual(7, len(self.github.records))
        self.assertEqual(2, len(self.registry.tags))
        first = copy.deepcopy(self.github.records)
        self.publish()
        self.assertEqual(first, self.github.records)
        self.assertEqual(7, len(self.github.uploads))
        self.assertEqual(2, len(self.registry.uploads))
        self.assertEqual(1, self.github.publishes)

    def test_stale_asset_listing_and_422_compare_existing_bytes(self):
        release = self.github.create(self.spec)
        self.github.upload(release, "release.json", (self.assets / "release.json").read_bytes())
        original = self.github.assets
        calls = 0
        def stale_once(value):
            nonlocal calls
            calls += 1
            return {} if calls == 1 else original(value)
        with patch.object(self.github, "assets", side_effect=stale_once):
            self.publish()
        self.assertEqual(7, len(self.github.uploads))
        self.assertFalse(self.github.value["draft"])

    def test_upload_lost_response_resumes_without_replacing_assets(self):
        self.github.fail_after = releases.names(self.spec["version"])["starter"]["archive"]
        with self.assertRaises(ConnectionError):
            self.publish()
        retained = copy.deepcopy(self.github.records)
        self.assertTrue(self.github.value["draft"])
        self.publish()
        self.assertTrue(all(self.github.records[name] == item for name, item in retained.items()))
        self.assertEqual(7, len(self.github.uploads))
        self.assertFalse(self.github.value["draft"])

    def test_partial_registry_publication_reuses_signed_release_without_build(self):
        self.registry.fail_after = self.spec["version"] + "-starter"
        with self.assertRaises(ConnectionError):
            self.publish()
        retained = copy.deepcopy(self.github.records)
        with patch.object(releases, "build_release", side_effect=AssertionError("must reuse signed bytes")):
            needed = releases.prepare(self.github, self.registry, self.proof, self.spec, self.root / "resume")
        self.assertFalse(needed)
        self.assertEqual(retained, self.github.records)
        self.publish()
        self.assertEqual(2, len(self.registry.uploads))

    def test_partial_draft_rebuild_checks_existing_bytes_and_requests_attestation(self):
        release = self.github.create(self.spec)
        self.github.upload(release, "release.json", (self.assets / "release.json").read_bytes())
        with patch.object(releases, "build_release", side_effect=self.build):
            needed = releases.prepare(self.github, self.registry, self.proof, self.spec, self.root / "prepared")
        self.assertTrue(needed)
        self.assertEqual(1, len(self.github.records))  # Prepare is read-only remotely.

    def test_conflicting_draft_assets_are_not_overwritten(self):
        release = self.github.create(self.spec)
        self.github.upload(release, "release.json", b"conflict")
        with self.assertRaisesRegex(ValueError, "asset conflict"):
            self.publish()
        self.assertEqual(b"conflict", self.github.records["release.json"]["content"])
        self.assertEqual(1, len(self.github.uploads))
        with patch.object(releases, "build_release", side_effect=self.build):
            with self.assertRaisesRegex(ValueError, "draft asset conflict"):
                releases.prepare(self.github, self.registry, self.proof, self.spec, self.root / "prepared")

    def test_registry_digest_conflict_stops_before_any_release_mutation(self):
        self.registry.tags[self.spec["version"] + "-full"] = "sha256:" + "1" * 64
        with self.assertRaisesRegex(ValueError, "registry digest conflict"):
            self.publish()
        self.assertIsNone(self.github.value)
        self.assertEqual([], self.github.uploads)

    def test_registry_readback_failure_keeps_release_draft(self):
        with patch.object(self.registry, "verify", side_effect=ValueError("corrupt readback")):
            with self.assertRaisesRegex(ValueError, "corrupt readback"):
                self.publish()
        self.assertTrue(self.github.value["draft"])
        self.assertEqual(0, self.github.publishes)
        self.publish()
        self.assertFalse(self.github.value["draft"])

    def test_unauthorized_github_and_registry_are_not_treated_as_absence(self):
        for destination in (self.github, self.registry):
            destination.authorized = False
            with self.subTest(destination=type(destination)), self.assertRaises((PermissionError, urllib.error.HTTPError)):
                self.publish()
            self.assertIsNone(self.github.value)
            self.assertEqual([], self.registry.uploads)
            destination.authorized = True

    def test_missing_or_untrusted_provenance_and_checksums_stop_before_mutation(self):
        for name in (releases.PROVENANCE, "SHA256SUMS"):
            path = self.assets / name
            content = path.read_bytes()
            path.unlink()
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.publish()
            path.write_bytes(content)
        proof = json.loads((self.assets / releases.PROVENANCE).read_bytes())
        proof["workflow"] = "attacker/data/.github/workflows/release.yml"
        (self.assets / releases.PROVENANCE).write_bytes(datasets.encoded(proof))
        with self.assertRaisesRegex(ValueError, "untrusted synthetic provenance"):
            self.publish()
        self.assertIsNone(self.github.value)

    def test_published_release_missing_integrity_assets_is_not_repaired(self):
        self.publish()
        for name in (releases.PROVENANCE, "SHA256SUMS"):
            original = self.github.records.pop(name)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "published release missing"):
                releases.prepare(self.github, self.registry, self.proof, self.spec, self.root / name.replace(".", "-"))
            self.github.records[name] = original
        self.assertEqual(7, len(self.github.uploads))

    def test_empty_failed_draft_upload_is_the_only_deletable_asset(self):
        release = self.github.create(self.spec)
        self.github.records["release.json"] = {"id": 99, "name": "release.json", "state": "starter", "size": 0, "content": b""}
        self.publish()
        self.assertEqual(["release.json"], self.github.deletions)
        self.assertFalse(self.github.value["draft"])

    def test_remote_tag_movement_stops_before_mutation(self):
        self.github.revision = "0" * 40
        with self.assertRaisesRegex(ValueError, "remote release tag moved"):
            self.publish()
        self.assertIsNone(self.github.value)

    def test_incomplete_empty_provenance_upload_can_be_resumed(self):
        release = self.github.create(self.spec)
        for path in self.assets.iterdir():
            if path.name != releases.PROVENANCE:
                self.github.upload(release, path.name, path.read_bytes())
        self.github.records[releases.PROVENANCE] = {"id": 99, "name": releases.PROVENANCE, "state": "starter", "size": 0, "content": b""}
        with patch.object(releases, "build_release", side_effect=self.build):
            self.assertTrue(releases.prepare(self.github, self.registry, self.proof, self.spec, self.root / "resume-proof"))
        self.publish()
        self.assertEqual([releases.PROVENANCE], self.github.deletions)
        self.assertFalse(self.github.value["draft"])

    def test_signature_verifier_pins_repository_workflow_source_and_hosted_runner(self):
        with patch.object(releases.subprocess, "run") as run:
            releases.Attestations().verify(self.assets)
        self.assertEqual(6, run.call_count)
        for call in run.call_args_list:
            command = call.args[0]
            self.assertIn("--deny-self-hosted-runners", command)
            self.assertEqual(releases.REPO + "/" + releases.WORKFLOW, command[command.index("--signer-workflow") + 1])
            self.assertEqual("refs/heads/main", command[command.index("--source-ref") + 1])
            self.assertEqual(releases.REPO, command[command.index("--repo") + 1])

    def test_source_tests_and_builders_do_not_inherit_publication_credentials(self):
        secrets = {key: "test-only" for key in ("GH_TOKEN", "GITHUB_TOKEN", "REGISTRY_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_RUNTIME_TOKEN")}
        with patch.dict(releases.os.environ, secrets), patch.object(releases.subprocess, "run") as run, patch.object(releases, "stage_assets", return_value={}):
            releases.build_release(self.spec, self.root / "build", releases.REGISTRY)
        child_calls = [call for call in run.call_args_list if call.args[0][0] == releases.sys.executable]
        self.assertEqual(3, len(child_calls))
        for call in child_calls:
            self.assertFalse(secrets.keys() & call.kwargs["env"].keys())


class ProducerPolicyTests(unittest.TestCase):
    def context(self):
        return {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REPOSITORY": releases.REPO,
                "GITHUB_REF": "refs/heads/main", "GITHUB_WORKFLOW_REF": releases.REPO + "/" + releases.WORKFLOW + "@refs/heads/main",
                "RUNNER_ENVIRONMENT": "github-hosted", "GITHUB_SHA": "1" * 40}

    def test_untrusted_events_forks_refs_and_self_hosted_contexts_are_rejected(self):
        releases.validate_context(self.context())
        for key, value in [("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REPOSITORY", "fork/data"),
                           ("GITHUB_REF", "refs/heads/feature"), ("RUNNER_ENVIRONMENT", "self-hosted"),
                           ("GITHUB_WORKFLOW_REF", "attacker/data/.github/workflows/release.yml@refs/heads/main")]:
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "untrusted publication"):
                releases.validate_context(dict(self.context(), **{key: value}))

    def test_release_tag_is_validated_before_git_or_network(self):
        with patch.object(releases, "git", side_effect=AssertionError("must validate first")):
            for tag in ["main", "v01.0.0", "v0.1.0;evil", "v0.1.0\n", "../v0.1.0"]:
                with self.subTest(tag=tag), self.assertRaises(ValueError):
                    releases.source_spec(tag)

    def test_tag_version_and_main_ancestry_are_required(self):
        lock = datasets.read_json("datasets/selections.lock.json")
        definitions = datasets.read_json("datasets/definitions.json")
        automation_version = lock["dataset_version"]
        def read(*args):
            if args[0] == "rev-parse":
                return b"1111111111111111111111111111111111111111\n"
            path = args[1].split(":", 1)[1]
            if path == "version.txt":
                return automation_version.encode()
            value = {".release-please-manifest.json": {".": automation_version},
                     "datasets/definitions.json": definitions, "datasets/selections.lock.json": lock,
                     "source.lock.json": lock["source"]}[path]
            return datasets.encoded(value)
        with patch.object(releases, "git", side_effect=read), patch.object(releases.subprocess, "run") as run:
            spec = releases.source_spec("v" + definitions["dataset_version"])
            self.assertEqual(definitions["dataset_version"], spec["version"])
            self.assertIn("--is-ancestor", run.call_args.args[0])
            for invalid in ("0.0.0", "99.0.0"):
                automation_version = invalid
                with self.subTest(automation_version=invalid), self.assertRaisesRegex(
                        ValueError, "tag and release automation version differ"):
                    releases.source_spec("v" + lock["dataset_version"])
            automation_version = lock["dataset_version"]
            definitions["dataset_version"] = "99.0.0"
            with self.assertRaisesRegex(ValueError, "tag and dataset version differ"):
                releases.source_spec("v" + lock["dataset_version"])
        with patch.object(releases, "git", return_value=b"1" * 40), patch.object(
                releases.subprocess, "run", side_effect=releases.subprocess.CalledProcessError(1, "git merge-base")):
            with self.assertRaises(releases.subprocess.CalledProcessError):
                releases.source_spec("v0.1.0")

    def test_redirects_strip_cross_origin_credentials_and_reject_non_github_targets(self):
        request = urllib.request.Request("https://api.github.com/asset", headers={"Authorization": "Bearer test-only"})
        handler = release_api.SafeRedirect()
        redirected = handler.redirect_request(request, None, 302, "", {}, "https://release-assets.githubusercontent.com/asset")
        self.assertIsNone(redirected.get_header("Authorization"))
        for destination in ["https://attacker.example/asset", "http://api.github.com/asset", "https://user@api.github.com/asset"]:
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "", {}, destination)

    def test_draft_lookup_uses_authenticated_paginated_release_listing(self):
        client = release_api.GitHub("test-only")
        missing = urllib.error.HTTPError("https://api.github.com", 404, "Not Found", {}, io.BytesIO())
        missing.close()
        draft = {"id": 1, "tag_name": "v0.1.0", "draft": True}
        with patch.object(client, "request", side_effect=[missing, [{"tag_name": "other"}] * 100, [draft]]) as request:
            self.assertEqual(draft, client.release("v0.1.0"))
            self.assertIn("page=2", request.call_args.args[1])
        denied = urllib.error.HTTPError("https://api.github.com", 403, "Forbidden", {}, io.BytesIO())
        denied.close()
        with patch.object(client, "request", side_effect=denied) as request:
            with self.assertRaises(urllib.error.HTTPError):
                client.release("v0.1.0")
            self.assertEqual(1, request.call_count)

    def test_workflow_is_dispatch_only_and_write_permissions_are_job_scoped(self):
        value = bundles.yaml.load((datasets.ROOT / releases.WORKFLOW).read_text(), Loader=bundles.yaml.BaseLoader)
        self.assertEqual({"workflow_dispatch"}, set(value["on"]))
        self.assertEqual({"contents": "read"}, value["permissions"])
        job = value["jobs"]["publish"]
        self.assertIn("github.ref == 'refs/heads/main'", job["if"])
        self.assertEqual("data-release", job["environment"])
        self.assertEqual("false", value["concurrency"]["cancel-in-progress"])
        for step in job["steps"]:
            if "uses" in step:
                self.assertRegex(step["uses"], r"@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
