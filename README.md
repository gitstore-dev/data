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

Create the target store namespace and repository first, then push an
appropriate branch of this repository to its GitStore Git URL using your
normal authenticated Git workflow. The target repository supplies the
effective namespace; the source files stay identical across stores.
There is no configured remote in this initial local repository.

The full taxonomy is a large seed. Validate ingestion limits and operational
capacity before loading all categories; successful file validation is not a
full-taxonomy deployed ingestion benchmark. Controller-resolved ancestry is
eventually consistent after admission.

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

The vendored category schema reflects repository-scoped namespace inference.
The accompanying GitStore change also validates all generated files through
its production parser.

Review coverage, acceptance limits and sample links are recorded in [QUALITY.md](QUALITY.md).

## Media

There are no media resources, references, files or LFS objects in this release.
Future files under `media/**` use Git LFS via committed attributes.
Resource Markdown, source records and tooling remain ordinary Git text.

```sh
git check-attr filter diff merge text -- media/example.png
git lfs ls-files
```

The first command should report LFS filters and unset text conversion; the
second should be empty for this initial dataset.

## License

MIT; retain Shopify's attribution and license when redistributing derived data.
See [LICENSE](LICENSE) and [LICENSE-SHOPIFY](LICENSE-SHOPIFY).
