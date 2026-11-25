# GitStore canonical demo data

A reusable English category catalog derived from Shopify Standard Product
Taxonomy **2026-11**: **64,292 categories, 26 departments**. This initial
dataset contains CategoryTaxonomy resources only.

## Use with GitStore

Category manifests are under `categories/<vertical-prefix>/<Shopify ID>.md`.
The stable resource name is `shopify-<Shopify ID>`; labels and parentage
match the pinned official taxonomy. Namespace is deliberately absent from
both resource metadata and parent references.

**Prerequisite:** the target GitStore API must support repository-scoped
namespace inference. Deploy the namespace-inference change to every serving
API replica before pushing this data. Older versions reject omitted
namespaces; do not push until the deployment is compatible. GraphQL clients
still provide namespaces explicitly.

Create the target store namespace and repository first. Export a selection
as described below, then copy its `categories/` files and both license notices
to the target repository with your normal authenticated Git workflow. The
target repository supplies the effective namespace; the source files stay
identical across stores.
This repository's authoring inputs and tools are not seed resources.

The full taxonomy is a large seed. Validate ingestion limits and operational
capacity before loading all categories; successful file validation is not a
full-taxonomy deployed ingestion benchmark. Controller-resolved ancestry is
eventually consistent after admission.

## Starter and full selections

**Starter is the convenience default. Full requires explicit opt-in.** GitStore
dataset version **0.1.0** identifies these selections; Shopify taxonomy version
**2026-11** identifies the upstream classification. These versions evolve
independently. No registry release is published by the export command.

| Selection | Categories | Root verticals | Manifest bytes |
| --- | ---: | ---: | ---: |
| `starter` (default) | 266 | 3 | 632,868 |
| `full` (explicit) | 64,292 | 26 | 177,054,512 |

The starter includes complete **Gift Cards** (3), **Luggage & Bags** (129), and
**Media** (134) verticals for a small retail demo covering gifts, bags, books
and recorded content. Media is a taxonomy label here: these are category
resources, with no media files or references. The exact 266 Shopify IDs are
committed in [datasets/starter.ids](datasets/starter.ids); no random sampling
or live upstream lookup occurs. Complete verticals retain both ancestor links
and every direct-child link without changing canonical descriptions. It is a
convenience selection, not a representative sample of all retail sectors.

After installing the Python requirements below, export from the repository root:

```sh
# Default: the reviewed starter selection.
.venv/bin/python tools/datasets.py export --output dist/starter
# Explicit opt-in: the complete taxonomy.
.venv/bin/python tools/datasets.py export --selection full --output dist/full
```

The output directory must not exist. Exports inside this checkout must be
under ignored `dist/`. Export validates the entire canonical catalogue
and checks the committed selection lock before writing. Each export contains
only selected `categories/` Markdown, `LICENSE`, `LICENSE-SHOPIFY`, and
`dataset.json`. It copies manifest bytes exactly; namespace keys, system fields,
media slots, File resources and binaries are absent. Source/editorial JSONL,
schemas, authoring tools, Git history and LFS configuration are not copied.

`dataset.json` is the deterministic handoff to the future OCI bundle builder,
not an OCI format contract. Its ordered `inventory` records each Shopify ID,
stable name, exact title, parent name, path, byte length and SHA-256. Order is
ancestor depth first, then ASCII category ID, so parents precede children.
Metadata includes counts per vertical, root count, total manifest bytes,
membership/inventory checksums, pinned upstream asset and source checksums,
license checksums, required namespace-inference support and schema/parser
revision. `inventory_sha256` hashes the UTF-8 inventory JSON serialized with
sorted keys, two-space indentation, unescaped Unicode and a final newline;
`ids_sha256` hashes ordered IDs, one per line with a final newline. There are
no timestamps or machine-specific paths in export metadata.

The schema and parser pin are compatibility evidence, not proof that a running
server supports this data. Check the target deployment before importing. A
functional import does not qualify production capacity. In particular, the full
selection is about 169 MiB of Markdown; do not assume one initial push fits
server admission limits. Export ordering supports a future importer making
separate, bounded pushes; export itself does not perform ingestion.

Datasets and any packaging tools remain separate from GitStore API,
controller-manager and Git-service binaries/images. OCI packaging/publication
and `gs seed` execution are separate work items.

## Data-only OCI bundles

The [seed/v1 contract](docs/seed-bundle-v1.md) packages either selection as an
OCI image layout plus a standalone offline archive. It is architecture-independent
data. The same `seed.tar.gz` bytes are the OCI layer and offline download; the
archive checksum and OCI manifest digest identify different objects.

Build from committed canonical, selection and builder inputs:

```sh
.venv/bin/python tools/bundles.py build --output dist/bundle-starter
.venv/bin/python tools/bundles.py build --selection full --output dist/bundle-full
# Verify all OCI descriptors, payload inventory and category semantics offline.
.venv/bin/python tools/bundles.py verify --package dist/bundle-starter
```

Each output includes `oci-layout`, `index.json`, content-addressed blobs,
`manifest.json`, `seed.tar.gz` and `checksums.json`. The builder prints separate
archive/OCI descriptors. The payload adds `bundle.json` to the exact selection
export, recording the source commit, dataset/selection identity, Shopify source,
compatibility requirements, provenance, ordered inventories and exact expanded
size/file counts. Outputs must be fresh; repository outputs go under `dist/`.

For offline verification, supply an independently approved **archive** digest
from the release channel. To inspect verified files, optionally extract to a
fresh directory:

```sh
.venv/bin/python tools/bundles.py verify \
  --archive dist/bundle-starter/seed.tar.gz \
  --archive-digest sha256:<archive-checksum> --extract-to dist/unpacked
```

Layout verification can instead pin `--oci-digest sha256:<manifest-checksum>`.
Digest verification provides integrity, not publisher authentication. Neither
source URLs nor a self-consistent layout establish trust. Publication and
signing/provenance policy belong to the release workflow in data #3.

Importers must check the target's required capabilities and resource APIs
before writes. For a caller that has independently established target support,
the verifier can check explicitly supplied sets using
`--target-capability repository-scoped-namespace-inference` and
`--target-api catalog.gitstore.dev/v1beta1`. Omitting both checks content only;
the tool never connects to a target or seeds data. Unknown formats, unsafe
entries, excessive expansion, tampering, missing ancestors and broken links
fail verification before extraction. See the contract for bounds and fixtures.

These maintainer commands use the existing Python requirements and standard
library. Packages contain no tools, runtime, third-party executables or media.
OCI publication and resumable `gs seed` remain separate work; core GitStore
binaries/images receive no data or dependencies.

## Content and provenance

Each file has complete author-writable frontmatter, two category-specific
explanatory paragraphs, ancestry links, direct-child navigation, and source
attribution. Official titles, IDs, hierarchy and classification attributes
come from Shopify. Paragraphs are GitStore-authored, deterministic demo copy
grounded in that context; they are **not official Shopify descriptions**.
They make no claim that classification attributes are required GitStore
fields, product certifications, or legal requirements.

`source.lock.json` pins the release asset URL, its compressed SHA-256,
normalized-data checksum, per-shard checksums, locale and counts. The compact source in
`upstream/categories.en/<vertical>.jsonl` retains category identities, hierarchy,
titles and classification attributes; product attributes and return reasons
are not emitted as GitStore resources. Shopify's license is preserved in
[LICENSE-SHOPIFY](LICENSE-SHOPIFY).

## Reproduce and maintain

Use Python 3.10+ and Git LFS. Set up an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
git lfs install --local
```

Run from the repository root:

```sh
# Re-download the pinned asset and verify its locked checksum.
.venv/bin/python tools/taxonomy.py import
# Fill missing descriptions in resumable batches; keep existing editorial copy.
.venv/bin/python tools/taxonomy.py editorial --batch-size 500
# Render from committed source and editorial records; no network or AI service.
.venv/bin/python tools/taxonomy.py render
# Validate every schema, hierarchy edge, link, description and rendered byte.
.venv/bin/python tools/taxonomy.py validate
```

Both source and editorial datasets contain one JSONL shard per taxonomy vertical
(26 shards each), stored as ordinary Git text. Tooling rejects shards of
50,000,000 bytes or larger.

Editorial records in `editorial/descriptions.en/<vertical>.jsonl` are keyed by the
Shopify category ID. Edit these records to improve prose, then render again.
The editorial command fills missing IDs and preserves completed records.
Each completed editorial batch is checkpointed by atomic replacement of its vertical shard;
an interrupted batch can be rerun without losing prior records. Re-running render must produce no diff.
Intentional upstream upgrades require reviewing IDs, removals, parent changes,
prose and the source lock together.

Validate selections and their reviewed lock after any intentional source,
editorial or membership change:

```sh
.venv/bin/python -m unittest discover -s tests
.venv/bin/python tools/datasets.py validate
# Maintainer check: use the locked production parser from committed Git objects.
.venv/bin/python tools/check_parser.py --gitstore /path/to/GitStore
```

Pull requests and pushes to `main` run one CI verification job: unit tests,
canonical schema/hierarchy/editorial/link validation, and selection-lock checks.
It generates a tiny OCI/archive fixture in temporary directories and checks
committed digest vectors, conformance and tampering cases. Only text fixture
definitions/vectors are stored in Git. It uses committed inputs only, with no
upstream downloads, full exports,
GitStore builds, live services or publication. The pinned production-parser
check remains a maintainer check.

The parser check needs Git, Go (including the pinned GitStore module's toolchain)
and its Go modules available. It archives the revision in
[datasets/compatibility.json](datasets/compatibility.json) into a temporary
directory and compiles a small validation harness there. It does not modify
the supplied GitStore clone or use its uncommitted code. Every canonical
manifest passes through the repository-scoped production parser; names,
titles, parent names and description bytes are compared, and the vendored
schema must exactly match that revision. This does not exercise live admission,
authorization, controller readiness or ingestion performance. Neither this Go
harness nor its dependencies are part of an export.

For an intentional selection change, edit `datasets/definitions.json` and the
explicit starter allowlist, review membership and rationale, update the dataset
version as appropriate, then refresh the lock explicitly:

```sh
.venv/bin/python tools/datasets.py lock
.venv/bin/python tools/datasets.py validate
```

Review `datasets/selections.lock.json` with the input changes. Validation never
refreshes it silently. It binds source/editorial checksums and both selection
inventories; missing ancestors, cycles, broken navigation, count drift and
manifest drift fail validation. Intentional upstream upgrades also require
reviewing compatibility and re-running the pinned parser check. For repeated
exports, choose two fresh output directories; all file bytes, including
`dataset.json`, must match.

Review coverage, acceptance limits and sample links are recorded in [QUALITY.md](QUALITY.md).

## Media

There are no media resources, references, files or LFS objects in this release.
Future files under `media/**` use Git LFS via committed attributes.
Resource Markdown, source records and tooling remain ordinary Git text.
Generated seed archives and OCI blobs are built during verification or release,
outside Git and LFS; repository build output belongs under ignored `dist/`.

```sh
git check-attr filter diff merge text -- media/example.png
git lfs ls-files
```

The first command should report LFS filters and unset text conversion; the
second should be empty for this initial dataset.

## License

MIT; retain Shopify's attribution and license when redistributing derived data.
See [LICENSE](LICENSE) and [LICENSE-SHOPIFY](LICENSE-SHOPIFY).
