# Publishing and consuming data releases

The producer is [`release.yml`](../.github/workflows/release.yml) in
`gitstore-dev/data`. It builds starter/full bundles **at release time** and
publishes to **`ghcr.io/gitstore-dev/data`** and this repository's GitHub Releases.
Generated archives, OCI blobs and signatures remain outside Git and Git LFS.
Core API, controller-manager and Git-service builds/startup have no dependency
on this workflow, registry or data release. This does not seed a running store.

## Producer trust and setup

- Only explicit `workflow_dispatch` on this repository's `main` is accepted.
  There are no PR, tag-push or release-event publication triggers. PR verification
  remains read-only; its small synthetic fixture tests require no registry,
  signing identity, Go build or service.
- The input is an **existing** `v<dataset-version>` tag, for example `v0.1.0`.
  It must resolve to a commit reachable from reviewed `origin/main`, and match
  both dataset definitions and the selection lock. Annotated tags are supported.
  The tag is checked against GitHub again before and after publication.
- Configure a `data-release` environment restricted to `main`. Configure trusted
  maintainer approval according to the organization's release policy. Protect
  `main`, the release workflow and version tags with review/rulesets; do not move
  published version tags. The environment settings/rulesets are repository
  administration prerequisites, not changes made by this PR.
- The producer job alone requests `contents: write`, `packages: write`,
  `attestations: write`, and `id-token: write`. Checkout does not persist Git
  credentials. Registry and GitHub API credentials are step-scoped ephemeral
  `GITHUB_TOKEN` values, not credentials for a GitStore installation. No PAT or
  long-lived signing key is needed for the producer. Tagged source tests/builders
  receive an environment with publication/OIDC credentials removed; this is
  hygiene within a trusted job, not isolation for unreviewed build code.
- Use GitHub-hosted runners. Verification pins the certificate's repository,
  workflow `gitstore-dev/data/.github/workflows/release.yml`, source ref `main`,
  SLSA provenance predicate, and hosted-runner requirement. Forks, PR events,
  other refs and other signer workflows are rejected.
- The package source annotation associates the OCI artifacts with this repository.
  Grant this repository Actions access to an existing GHCR `data` package if
  needed. Newly created GHCR packages may be private even for public source:
  an organization maintainer must explicitly choose public visibility in the
  package settings for anonymous demo-data pulls. Keeping it private is supported.
  See GitHub's [container-registry authentication and visibility rules](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

Actions are pinned by commit; Python `3.12.12`, Go `1.26.11`, and ORAS Go SDK
`v3.0.0` are selected explicitly. The SDK dependency graph is checksummed in
`tools/registry/go.sum`. Ubuntu `24.04` hosted runner images can receive updates;
this is not a promise of a frozen OS image. The inventory records actual
Python/zlib versions, and committed codec vectors detect fixture drift. If an
unsigned partial draft cannot be reproduced under its original builder profile,
publication stops on the byte conflict: restore the reviewed original profile or
use a new version. Never refresh old assets to make a retry succeed. A complete
signed release is reused verbatim and does not depend on a later builder profile.

## Create a release

1. Merge/review data changes and update `datasets/definitions.json` and
   `datasets/selections.lock.json` for the new dataset version. Keep the Shopify
   taxonomy version distinct. Merge the producer workflow before its first use.
2. Create/push the matching version tag on the reviewed commit using the normal
   maintainer Git workflow. The producer neither creates nor updates Git tags.
3. Dispatch from `main`, for example:

   ```sh
   gh workflow run release.yml --repo gitstore-dev/data --ref main -f tag=v0.1.0
   ```

The controller stays at the dispatch's main revision. It checks out a clean
temporary worktree at the tagged data revision, runs its unit tests, validates
the canonical catalogue/locks and builds both bundles there. Release metadata
identifies the tagged **data source** commit; the signed attestation certificate
and statement identify the **producer workflow** execution/revision on `main`.
These revisions may differ when releasing a reviewed older tag.

Each selection gets a version tag, for example `0.1.0-starter` and `0.1.0-full`.
`release.json` lists their exact `ghcr.io/...@sha256:...` references, OCI/archive
descriptors, source revision, Shopify release, dataset/package versions,
requirements, counts and expanded sizes. No floating `latest` tag is created.
Prerelease dataset versions produce prerelease GitHub Releases.

For version `0.1.0`, the seven GitHub Release assets are:

```text
gitstore-categories-0.1.0-starter.tar.gz
gitstore-categories-0.1.0-starter.oci.json
gitstore-categories-0.1.0-full.tar.gz
gitstore-categories-0.1.0-full.oci.json
release.json
SHA256SUMS
provenance.jsonl
```

The archives are exactly the OCI layer bytes, not a repackaging. The `.oci.json`
files are exactly the final OCI manifests. `SHA256SUMS` covers both archives,
both OCI manifests and `release.json`. GitHub's attestation action signs all six
base assets, **including `SHA256SUMS`**, and the producer verifies all six against
the proof before uploading. The proof is not included in its own checksums or
subjects. OCI manifest digests are therefore bound directly by their signed
manifest-file subjects, as well as by the signed release inventory.

## Retry, conflicts and retention

There is one non-cancelling concurrency group per version. Re-dispatch the same
tag to recover an interrupted attempt; each attempt uses a fresh local workdir.

- New/unsigned partial versions are built at release time. Existing draft assets
  are compared byte-for-byte before uploads. Matching assets are retained;
  missing ones are added. An accepted upload with a lost response is discovered
  on retry. A 422 race response is resolved by comparing the existing bytes.
- Only a **zero-byte `starter` upload in a still-draft release** may be deleted
  and retried. This repairs GitHub's unfinished-upload placeholder; it does not
  replace an uploaded artifact. The repair is logged. No whole release or Git
  tag is deleted by the producer.
- Complete assets with valid existing provenance are downloaded, fully verified
  and reused without rebuilding or generating a new, different signature. A
  published release missing required assets/checksums/provenance fails closed;
  it is not silently repaired or rewritten.
- OCI blobs/manifests are copied by digest using ORAS SDK. Only a genuinely absent
  version tag may be created; a matching tag is reused, and a conflicting digest
  aborts. Authentication/transport/server errors are not treated as absence.
  Graph upload retries reuse existing content-addressed blobs. Final remote
  manifests and blobs are pulled back and compared with the source layout.
- The release remains a draft until all seven assets, their readback, the proof,
  both registry references and the source Git tag pass verification. Failure can
  leave signed OCI version tags or an incomplete draft; the same-version retry
  finishes the transaction. There is no cross-service atomic transaction.

**GHCR version tags are mutable at the service level.** OCI has no portable
create-only tag CAS. The producer serializes/rechecks its own writes, but cannot
exclude another credential writing between a probe and tag creation. Restrict
the package namespace to this exclusive producer, audit repository/package write
access, and prohibit out-of-band tag changes. Consumers pin immutable digests;
do not treat a mutable tag as an authenticity boundary. Published releases/assets
and version tags are retained without automated deletion/replacement. Enable
GitHub immutable releases where available as an additional repository control.

## Pull, verify, mirror and disconnect

Registry credentials are independent of GitStore API/Git credentials. Public
GHCR packages permit anonymous pulls. For private packages, use a registry
credential with `read:packages` and any required organization SSO authorization
(GitHub documents PAT **classic** support); use ORAS's credential store/login via
stdin, never a token in command arguments or a GitStore bearer token:

```sh
oras login ghcr.io --username "$REGISTRY_USER" --password-stdin < /secure/path/registry-token
```

Download the release assets to a fresh directory (GitHub authentication is
needed for private releases), check `SHA256SUMS`, and verify producer provenance:

```sh
set -e
gh release download v0.1.0 --repo gitstore-dev/data --dir ./data-v0.1.0
cd data-v0.1.0
shasum -a 256 -c SHA256SUMS
for artifact in release.json SHA256SUMS gitstore-categories-*.tar.gz gitstore-categories-*.oci.json; do
  gh attestation verify "$artifact" --bundle provenance.jsonl --repo gitstore-dev/data \
    --signer-workflow gitstore-dev/data/.github/workflows/release.yml \
    --source-ref refs/heads/main --predicate-type https://slsa.dev/provenance/v1 \
    --deny-self-hosted-runners
done
```

Hashes alone do not authenticate the producer. For fully disconnected signature
verification, provision GitHub CLI's trusted roots in advance (`gh attestation
trusted-root`, then `--custom-trusted-root`); preserve the proof alongside the
assets. Repository/owner identity is checked as recorded at signing; do not
silently transfer trust to a renamed/reused repository name.

After verifying the release inventory, select starter by default and use its
**manifest** digest for OCI retrieval:

```sh
manifest_digest="$(jq -r '.selections[] | select(.selection == "starter") | .oci_manifest.digest' release.json)"
oras pull "ghcr.io/gitstore-dev/data@$manifest_digest" --output ./pulled
```

Use the separate `archive.digest` from that same selection when verifying
`pulled/seed.tar.gz` or the downloaded release `.tar.gz` with the
[offline bundle verifier](seed-bundle-v1.md). A manifest digest is not an archive
checksum. Target namespace-inference/API capability checks remain prerequisites
before seeding; this workflow does not establish target readiness or capacity.

ORAS can copy the artifact to an operator-owned mirror or offline OCI layout:

```sh
oras cp "ghcr.io/gitstore-dev/data@$manifest_digest" "$MIRROR/data:0.1.0-starter"
oras resolve "$MIRROR/data:0.1.0-starter"  # must equal manifest_digest
oras cp --to-oci-layout "ghcr.io/gitstore-dev/data@$manifest_digest" ./offline-layout:0.1.0-starter
```

Transport that layout directory and the seven release assets/proof via the
operator's offline channel. Copying manifests without mutation preserves their
digests. Provenance is distributed as a release sidecar, **not** an OCI referrer;
copy it separately. Offline users may consume the archive directly or mirror
the layout with `oras cp --from-oci-layout`. ORAS/GitHub CLI and producer helper
binaries are operator/release tools, never bundled in core GitStore services.

## Rehearsal and test limits

`python -m unittest discover -s tests` exercises publication state, policy,
partial uploads, asset/tag conflicts and required proof/checksums with small
generated text-defined fixtures. It does not build full bundles or start services
in ordinary PR CI. The release helper can be built independently:

```sh
GOWORK=off go -C tools/registry build -mod=readonly -o /tmp/seed-registry .
```

For a disposable rehearsal, start an owned `registry:3.0.0` container bound only
to loopback, then run the command below with its mapped port and a fresh registry
repository. Stop/remove that exact container afterwards; use no retained volume:

```sh
python tests/rehearse_publication.py --registry-helper /tmp/seed-registry \
  --registry 127.0.0.1:5000/rehearsal/data
```

The rehearsal uses the native ORAS SDK against a real disposable OCI registry
and the production REST adapter against a loopback GitHub Releases protocol
stub. It verifies upload/readback equivalence, lost responses, exact retry reuse,
finalization, digest conflicts, 401 denial and checksum tampering. Its provenance
is explicitly synthetic. Real GitHub OIDC signing, environment permissions and
GHCR organization access require the first approved dispatch; they are not
claimed as locally exercised. No live release is published by these tests.
