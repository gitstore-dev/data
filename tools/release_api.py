"""Small bounded GitHub Releases REST adapter for the producer workflow."""
import json
import urllib.error
import urllib.parse
import urllib.request

from datasets import require

REPO = "gitstore-dev/data"
MAX_JSON = 2 * 1024 * 1024


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        require(target.scheme == "https" and target.hostname in {
            "api.github.com", "uploads.github.com", "release-assets.githubusercontent.com",
            "objects.githubusercontent.com"} and target.port in (None, 443)
            and target.username is None and target.password is None, "unsafe GitHub redirect")
        redirected = super().redirect_request(request, fp, code, message, headers, newurl)
        if urllib.parse.urlsplit(request.full_url).netloc != target.netloc:
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self, token, *, api="https://api.github.com", uploads="https://uploads.github.com", opener=None):
        require(bool(token), "GitHub token missing")
        self.api, self.uploads, self.token = api, uploads, token
        self.opener = opener or urllib.request.build_opener(SafeRedirect())

    def request(self, method, path, *, data=None, maximum=MAX_JSON, binary=False, upload=False):
        url = (self.uploads if upload else self.api) + "/repos/" + REPO + path
        headers = {"Authorization": "Bearer " + self.token, "User-Agent": "gitstore-data-release",
                   "X-GitHub-Api-Version": "2026-03-10",
                   "Accept": "application/octet-stream" if binary else "application/vnd.github+json"}
        if isinstance(data, bytes):
            headers["Content-Type"] = "application/octet-stream"
        elif data is not None:
            data = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=120) as response:
                content = response.read(maximum + 1)
                require(len(content) <= maximum, "GitHub response exceeds limit")
        except urllib.error.HTTPError as error:
            error.close()
            raise
        return content if binary else (json.loads(content) if content else None)

    def release(self, tag):
        try:
            return self.request("GET", "/releases/tags/" + urllib.parse.quote(tag, safe=""))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
        # The by-tag REST endpoint may not return a pending draft. Authenticated
        # release listings include drafts; do not create a duplicate on retry.
        matches = []
        for page in range(1, 11):
            values = self.request("GET", f"/releases?per_page=100&page={page}")
            matches.extend(value for value in values if value["tag_name"] == tag)
            require(len(matches) <= 1, "ambiguous releases for version tag")
            if len(values) < 100:
                return matches[0] if matches else None
        raise ValueError("release lookup exceeded bounded pagination; absence unresolved")

    def assets(self, release):
        result = []
        for page in range(1, 4):
            items = self.request("GET", f"/releases/{release['id']}/assets?per_page=100&page={page}")
            result.extend(items)
            if len(items) < 100:
                break
        else:
            raise ValueError("too many release assets")
        require(len({item["name"] for item in result}) == len(result), "duplicate release asset names")
        return {item["name"]: item for item in result}

    def download(self, asset, maximum):
        require(type(asset["id"]) is int and asset["id"] > 0 and asset["state"] == "uploaded"
                and 0 < asset["size"] <= maximum, "incomplete or oversized release asset")
        content = self.request("GET", f"/releases/assets/{asset['id']}", binary=True, maximum=maximum)
        require(len(content) == asset["size"], "release asset size mismatch")
        return content

    def source_revision(self, tag):
        value = self.request("GET", "/git/ref/tags/" + urllib.parse.quote(tag, safe=""))["object"]
        for _ in range(8):
            if value["type"] == "commit":
                return value["sha"]
            require(value["type"] == "tag", "unsupported Git tag object")
            value = self.request("GET", "/git/tags/" + value["sha"])["object"]
        raise ValueError("Git tag nesting exceeds limit")

    def create(self, spec):
        return self.request("POST", "/releases", data={
            "tag_name": spec["tag"], "target_commitish": spec["commit"], "name": "GitStore data " + spec["version"],
            "draft": True, "prerelease": "-" in spec["version"], "make_latest": "false",
            "body": "Seed data bundles. See release.json for source, compatibility, counts and immutable digests. "
                    "Verify SHA256SUMS and provenance.jsonl before use. No live ingestion qualification is implied.",
        })

    def upload(self, release, name, content):
        return self.request("POST", f"/releases/{release['id']}/assets?name=" + urllib.parse.quote(name, safe=""),
                            data=content, upload=True)

    def delete_incomplete(self, release, asset):
        current = self.request("GET", f"/releases/{release['id']}")
        require(current["draft"] and asset["state"] == "starter" and asset["size"] == 0,
                "only empty unfinished draft uploads may be deleted")
        self.request("DELETE", f"/releases/assets/{asset['id']}")

    def publish(self, release):
        return self.request("PATCH", f"/releases/{release['id']}", data={"draft": False, "make_latest": "false"})
