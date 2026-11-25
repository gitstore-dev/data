"""Disposable loopback OCI registry + GitHub Releases protocol-stub rehearsal.

This does not contact GHCR/GitHub or create real OIDC attestations. Production
uses GitHub's signed attestation action/verifier; this exercise uses clearly
marked synthetic proof and the same publication state machine/HTTP adapter.
"""
import argparse
import contextlib
import copy
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import release_api
import releases
from datasets import require
from release_fakes import FakeGitHub, TestProvenance, fixture_release


@contextlib.contextmanager
def release_server(backend):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, value, status=200):
            content = value if isinstance(value, bytes) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream" if isinstance(value, bytes) else "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def handle_request(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path.startswith("/v2/") or self.headers.get("Authorization") != "Bearer REHEARSAL-ONLY":
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="rehearsal"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            prefix = "/repos/" + releases.REPO
            require(parsed.path.startswith(prefix), "unexpected rehearsal API path")
            path = urllib.parse.unquote(parsed.path[len(prefix):])
            if self.command == "GET":
                if path.startswith("/git/ref/tags/"):
                    return self.reply({"object": {"type": "commit", "sha": backend.revision}})
                if path == "/releases":
                    return self.reply([backend.release(backend.spec["tag"])] if backend.value else [])
                if path.startswith("/releases/tags/"):
                    # Match GitHub's pending-draft lookup behavior rather than
                    # pretending a draft always exists at the published URL.
                    value = backend.release(backend.spec["tag"])
                    return self.reply(value if value and not value["draft"] else {"message": "Not Found"},
                                      200 if value and not value["draft"] else 404)
                if path == "/releases/1":
                    value = backend.release(backend.spec["tag"])
                    return self.reply(value or {"message": "Not Found"}, 200 if value else 404)
                if path == "/releases/1/assets":
                    return self.reply(list(backend.assets(backend.value).values()))
                if path.startswith("/releases/assets/"):
                    identifier = int(path.rsplit("/", 1)[1])
                    asset = next(a for a in backend.records.values() if a["id"] == identifier)
                    return self.reply(asset["content"])
            length = int(self.headers.get("Content-Length", "0"))
            require(length <= releases.bundles.MAX_ARCHIVE, "oversized rehearsal request")
            content = self.rfile.read(length)
            if self.command == "POST" and path == "/releases":
                value = json.loads(content)
                require(value["tag_name"] == backend.spec["tag"] and value["target_commitish"] == backend.spec["commit"]
                        and value["draft"] is True, "incorrect draft creation")
                return self.reply(backend.create(backend.spec), 201)
            if self.command == "POST" and path == "/releases/1/assets":
                name = urllib.parse.parse_qs(parsed.query)["name"][0]
                try:
                    result = backend.upload(backend.value, name, content)
                except ConnectionError:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                return self.reply(result, 201)
            if self.command == "PATCH" and path == "/releases/1":
                require(json.loads(content)["draft"] is False, "unexpected release edit")
                return self.reply(backend.publish(backend.value))
            if self.command == "DELETE" and path.startswith("/releases/assets/"):
                identifier = int(path.rsplit("/", 1)[1])
                asset = next(a for a in backend.records.values() if a["id"] == identifier)
                backend.delete_incomplete(backend.value, asset)
                return self.reply(b"", 204)
            return self.reply({"message": "unexpected path"}, 404)

        do_GET = do_POST = do_PATCH = do_DELETE = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        yield release_api.GitHub("REHEARSAL-ONLY", api=url, uploads=url), url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry-helper", type=Path, required=True)
    parser.add_argument("--registry", required=True, help="127.0.0.1:<port>/disposable/repository")
    args = parser.parse_args()
    host = urllib.parse.urlsplit("//" + args.registry)
    require(host.hostname == "127.0.0.1" and host.port is not None and host.path not in {"", "/"}, "rehearsal must use a loopback registry")
    os.environ.pop("REGISTRY_TOKEN", None)
    os.environ.pop("REGISTRY_USERNAME", None)
    helper = args.registry_helper.resolve()
    with tempfile.TemporaryDirectory(prefix="seed-publication-rehearsal-") as temporary:
        root = Path(temporary).resolve()
        spec, assets = fixture_release(root / "release", args.registry)
        backend, proof = FakeGitHub(spec), TestProvenance()
        registry = releases.Registry(helper, args.registry)
        with release_server(backend) as (github, api):
            backend.fail_after = releases.names(spec["version"])["starter"]["archive"]
            try:
                releases.publish(github, registry, proof, spec, assets, root / "attempt-1")
            except ConnectionError:
                pass
            else:
                raise AssertionError("expected simulated lost upload response")
            retained = copy.deepcopy(backend.records)
            require(backend.value["draft"], "partial release became public")
            releases.publish(github, registry, proof, spec, assets, root / "attempt-2")
            require(all(backend.records[name] == value for name, value in retained.items()), "retry replaced existing assets")
            require(not backend.value["draft"] and len(backend.uploads) == 7, "release did not finish idempotently")
            require(not releases.prepare(github, registry, proof, spec, root / "resume"), "signed retry requested a rebuild")
            releases.publish(github, registry, proof, spec, root / "resume/assets", root / "attempt-3")
            require(len(backend.uploads) == 7 and backend.publishes == 1, "completed retry mutated release")
            # Probe the same loopback server's explicit 401 registry endpoint.
            denial = subprocess.run([str(helper), "probe", "--repository", api.removeprefix("http://") + "/denied",
                                     "--tag", spec["version"] + "-starter"], capture_output=True)
            require(denial.returncode != 0 and not denial.stdout, "unauthorized registry was treated as absent")
            original = backend.records["SHA256SUMS"]
            backend.records["SHA256SUMS"] = dict(original, content=b"tampered\n", size=9)
            try:
                releases.prepare(github, registry, proof, spec, root / "tampered")
            except ValueError:
                pass
            else:
                raise AssertionError("tampered checksums accepted")
            backend.records["SHA256SUMS"] = original
        # Native SDK tag conflict: populate a new disposable repository with the
        # other selection's digest, then prove publisher refuses to replace it.
        conflict_repo = args.registry + "-conflict"
        conflict_spec, conflict_assets = fixture_release(root / "conflict", conflict_repo)
        conflict_registry = releases.Registry(helper, conflict_repo)
        inventory = json.loads((conflict_assets / "release.json").read_bytes())
        full = inventory["selections"][1]
        conflict_registry.publish(root / "conflict/built/full", inventory["selections"][0]["tag"], full["oci_manifest"]["digest"])
        rejected = FakeGitHub(conflict_spec)
        with release_server(rejected) as (github, _):
            try:
                releases.publish(github, conflict_registry, proof, conflict_spec, conflict_assets, root / "conflict-verification")
            except ValueError:
                pass
            else:
                raise AssertionError("registry conflict accepted")
            require(rejected.value is None, "registry conflict mutated GitHub release")
        print("PASS: native OCI upload/readback, offline equivalence, lost-response recovery, signed-byte reuse, HTTP API draft/finalize, conflicts, auth denial and tampered checksums")
        print("Rehearsal only: GitHub Releases was a loopback protocol stub; provenance was synthetic, not a live OIDC signature.")


if __name__ == "__main__":
    main()
