# Dataset quality and review

## Complete-dataset checks

- 64,292 unique category identities and 26 root departments match the pinned English source.
- Every title and parent reference matches the normalized upstream records; every ancestor exists and the graph is acyclic.
- Every manifest passes the vendored JSON schema and GitStore's production repository-scoped parser.
- Namespace keys, system metadata, media slots and media resources are absent from authored category manifests.
- All editorial records have two meaningful, category-specific paragraphs; paragraph pairs are unique and every local navigation link resolves.
- Rendered bytes match the deterministic renderer; interrupted editorial checkpoints preserve earlier records.

Run the full local checks with:

```sh
.venv/bin/python -m unittest discover -s tests
.venv/bin/python tools/taxonomy.py validate
.venv/bin/python tools/datasets.py validate
.venv/bin/python tools/check_parser.py --gitstore /path/to/GitStore
```

## Release Please automation verification

- All 60 fast Python tests pass, including independent fixture regeneration after
  a production version bump, version-field consistency, and rejection of tagged
  releases with bootstrap or mismatched automation versions.
- The pinned Release Please 17.6.0 engine was exercised offline with the committed
  config: initial `0.1.0`, subsequent feature `0.2.0`, both JSON version updates
  without selection-content changes, and draft releases with forced tag creation.
- Both release workflows pass `actionlint`. Default PR verification remains
  Python-only. No generated archives, Node modules, or executables are committed.
- Full canonical and starter/full lock validation passes; source, editorial,
  canonical manifests, selection locks, and existing fixture vectors are unchanged.
- Automatic GitHub App operation, OIDC signing, and GHCR writes await configured
  repository credentials/environment controls and the first release after merge;
  the offline preview does not establish those live permissions.

## Dataset selection validation (0.1.0)

The starter allowlist contains exactly 266 categories: all 3 Gift Cards, all
129 Luggage & Bags, and all 134 Media nodes. Review includes each root and every
selected ID/title from the pinned source; see [datasets/starter.ids](datasets/starter.ids).
Complete verticals keep the canonical Markdown's ancestor and direct-child
navigation valid without rewriting any body. Full membership is exactly the
64,292 source IDs and 26 roots. Both selections are ordered by ancestor depth,
then ASCII category ID. Tests exercise missing ancestors, cycles, broken local
links, duplicate/unknown IDs, lock drift, deterministic exports, existing-output
protection and cleanup after a changed-input export fails.

The production parser and vendored schema are pinned to GitStore commit
`e95ac03183cb3c4e78bdfbe4bb3f431bf6f4e961` (repository namespace inference).
The maintainer parser command checks all 64,292 canonical files against those
immutable Git objects, including unchanged Markdown bodies and parent names.
Both selections are subsets of that exact corpus, with per-file checksums in
the exported inventory. This provides parser/schema evidence without claiming
live-server compatibility or controller readiness.

Release review should compare two fresh exports of each selection, including
`dataset.json` and both MIT notices, and confirm no canonical category,
upstream or editorial file changed. Export metadata has no wall-clock time,
working-directory path or current Git HEAD dependency. Dataset version and
upstream taxonomy version remain separate. These checks do not qualify
full-taxonomy ingestion performance.

Implementation validation on 2026-11-25 passed all 16 unit tests, complete
canonical schema/editorial/hierarchy checks, and the pinned production parser
for all 64,292 manifests. Two fresh exports per selection matched every byte:
269 starter files and 64,295 full files (categories, metadata and two licenses).
Both inventories were independently checked for parent-first ordering,
canonical file checksums and complete local navigation. No canonical category,
source, editorial, schema or license file changed; LFS attributes remain intact
and there are no tracked LFS objects. No live ingestion benchmark was run.

The accompanying GitStore tests exercise concurrent API instances using the same
namespace-free root/child tree in two stores, correct parent names and ancestor
paths, cross-replica replay, path moves, authorization denials, invalid explicit
namespaces and unavailable repository scope. This is an in-process memdb
acceptance fixture; full-taxonomy deployed ingestion and Scylla qualification
have not been performed.

## Bundle checks

The seed/v1 tests generate the small CLI fixture from a committed text definition
and compare committed digest vectors, rather than store binary archives or build
full bundles in CI. They cover deterministic fixture generation, separate
archive/OCI digest verification, exact extraction, target-requirement preflight,
malformed and unsafe archive entries, expansion/count limits, tampering,
namespace/media omission, hierarchy/navigation and output preservation. See
[the bundle contract](docs/seed-bundle-v1.md) for the limits and fixture recipe.

Before a package release, build both selections twice from the same committed
inputs into fresh directories, compare all output bytes, verify each layout,
and extract/compare every category with the canonical files. Repeat-build
identity includes source commit and builder/codec versions. A small fixture
guards compression drift across local and CI environments; release automation
should pin its builder environment. These package checks do not perform live
GitStore ingestion or qualify production capacity.

Initial package-format validation on 2026-11-25 used Python 3.14.6, zlib 1.2.12
and the canonical selection inputs. The table records those test builds;
compressed sizes also depend on the declared source-commit value:

| Selection | Categories | Payload files | Expanded bytes | Archive bytes |
| --- | ---: | ---: | ---: | ---: |
| Starter | 266 | 270 | 763,031 | 65,779 |
| Full | 64,292 | 64,296 | 207,441,883 | 13,741,399 |

All eight OCI/offline output files matched across two builds of each selection.
Layout and archive verification passed, and extraction preserved every category,
both licenses and the original selection metadata exactly. ORAS 1.4.0 resolved
the fixture manifest and pulled identical fixture/full payloads from local OCI
layouts without a registry. All 36 unit tests passed. The ORAS client was a
temporary maintainer tool, not a repository, CI or service dependency. Canonical
inputs, schema/selection locks and the single CI workflow remained unchanged.
No registry publication or live GitStore ingestion was performed.

## Publication checks

Release publication is separate from normal read-only verification CI. The
[release guide](docs/releases.md) records producer permissions, trust policy,
version retention, retry/recovery and public/private/offline consumption.

Validation on 2026-11-25 passed 58 fast tests, workflow `actionlint`, Go helper
build/package checks and `go vet`. The release-time worktree builder was also
exercised with the merged canonical source
`c2e2ae39624da534bc5d3706d40486f1deb608a2`: it validated/built 266 starter and
64,292 full resources and generated the release inventory, manifests, matching
archive assets and checksums. No generated files were added to Git or LFS.

A disposable `registry:3.0.0` rehearsal used the native ORAS SDK helper for real
OCI uploads/readback, with the production GitHub REST adapter against a loopback
Releases protocol stub. It covered pending-draft lookup, accepted uploads with
lost responses, byte-preserving retries, completed signed-asset reuse, exact
offline/OCI equivalence, registry conflicts, 401 denial and checksum tampering.
The owned registry containers were removed after each rehearsal.

The rehearsal's proof was explicitly synthetic. Real GitHub OIDC signing, GHCR
organization access, package visibility and environment controls remain checks
for the first approved dispatch after merge; no live release/tag/artifact was
published by implementation validation. Core-service artifacts/startup and the
existing lightweight verification workflow remain unchanged.

## Editorial sample review

Reviewed roots, an intermediate node where available, and a terminal node
from every vertical. Review checked title fidelity, useful classification
context, child and ancestor navigation, attribution, and attribute relevance.
Additional samples covered live animals, infant bathing, chemical products,
mature books, collectible firearms, memorial urns, and automotive services.
Source attribute definitions are descriptive fields, never assertions that
products meet a certification, safety standard or legal requirement.

Shopify sometimes supplies broadly inherited attributes that are unsuitable
for category prose. The editorial generator mentions an attribute only when
its name relates to the classification path or is a general comparison field.
For example, memorial-urn copy excludes watch-band color and bundle copy
excludes hoodie/glove-specific dimensions. The full source attribute records
remain available unchanged for future editorial review.

| Department | Count | Root | Intermediate | Terminal sample |
| --- | ---: | --- | --- | --- |
| Animals & Pet Supplies | 1,648 | [Animals & Pet Supplies](categories/ap/ap.md) | [Live Animals](categories/ap/ap-1.md) | [Birds](categories/ap/ap-1-2.md) |
| Apparel & Accessories | 2,544 | [Apparel & Accessories](categories/aa/aa.md) | [Clothing](categories/aa/aa-1.md) | [Joggers](categories/aa/aa-1-1-1-1.md) |
| Arts & Entertainment | 5,915 | [Arts & Entertainment](categories/ae/ae.md) | [Hobbies & Creative Arts](categories/ae/ae-2.md) | [Event Tickets](categories/ae/ae-1.md) |
| Baby & Toddler | 700 | [Baby & Toddler](categories/bt/bt.md) | [Baby Bathing](categories/bt/bt-1.md) | [Bath Rings](categories/bt/bt-1-1-1.md) |
| Bundles | 1 | [Bundles](categories/bu/bu.md) | No intermediate level | [Bundles](categories/bu/bu.md) |
| Business & Industrial | 1,970 | [Business & Industrial](categories/bi/bi.md) | [Advertising & Marketing](categories/bi/bi-1.md) | [Booklet Brochures](categories/bi/bi-1-1-3.md) |
| Cameras & Optics | 1,268 | [Cameras & Optics](categories/co/co.md) | [Camera & Optic Accessories](categories/co/co-1.md) | [26-Pin Multicore Component Video Cables](categories/co/co-1-1-1-8.md) |
| Electronics | 6,212 | [Electronics](categories/el/el.md) | [Arcade Equipment](categories/el/el-1.md) | [Multiplayer Basketball Arcade Games](categories/el/el-1-1-2.md) |
| Food & Beverages | 4,311 | [Food & Beverages](categories/fb/fb.md) | [Beverages](categories/fb/fb-1.md) | [Ales](categories/fb/fb-1-1-1-1.md) |
| Furniture | 1,553 | [Furniture](categories/fr/fr.md) | [Baby & Toddler Furniture](categories/fr/fr-1.md) | [Changing Station Furniture Sets](categories/fr/fr-1-1-4.md) |
| Gift Cards | 3 | [Gift Cards](categories/gc/gc.md) | No intermediate level | [Closed-Loop Gift Cards](categories/gc/gc-2.md) |
| Hardware | 5,788 | [Hardware](categories/ha/ha.md) | [Building Consumables](categories/ha/ha-1.md) | [Acid Spill Neutralizers](categories/ha/ha-1-1-1-1.md) |
| Health & Beauty | 3,569 | [Health & Beauty](categories/hb/hb.md) | [Health Care](categories/hb/hb-1.md) | [Acupressure Belts](categories/hb/hb-1-1-3-5.md) |
| Home & Garden | 9,617 | [Home & Garden](categories/hg/hg.md) | [Bathroom Accessories](categories/hg/hg-1.md) | [Bathtub Baskets](categories/hg/hg-1-1-1-2.md) |
| Luggage & Bags | 129 | [Luggage & Bags](categories/lb/lb.md) | [Backpacks](categories/lb/lb-1.md) | [Framed Hiking Backpacks](categories/lb/lb-1-15-1.md) |
| Mature | 232 | [Mature](categories/ma/ma.md) | [Erotic](categories/ma/ma-1.md) | [Erotic Art Books](categories/ma/ma-1-6-3.md) |
| Media | 134 | [Media](categories/me/me.md) | [Academic Papers & Studies](categories/me/me-8.md) | [Academic Journal Volumes](categories/me/me-8-5.md) |
| Office Supplies | 842 | [Office Supplies](categories/os/os.md) | [Book Accessories](categories/os/os-1.md) | [Book Cover Films](categories/os/os-1-1-4.md) |
| Product Add-Ons | 8 | [Product Add-Ons](categories/pa/pa.md) | No intermediate level | [Extended Warranties](categories/pa/pa-1.md) |
| Religious & Ceremonial | 52 | [Religious & Ceremonial](categories/rc/rc.md) | [Memorial Ceremony Supplies](categories/rc/rc-1.md) | [Memorial Urns](categories/rc/rc-1-1.md) |
| Services | 293 | [Services](categories/se/se.md) | [Automotive Services](categories/se/se-1.md) | [Airbag Electronics Services](categories/se/se-1-1-3.md) |
| Software | 306 | [Software](categories/so/so.md) | [Cloud Services](categories/so/so-4.md) | [Cloud Application Services](categories/so/so-4-3.md) |
| Sporting Goods | 10,817 | [Sporting Goods](categories/sg/sg.md) | [Athletics](categories/sg/sg-1.md) | [American Football Lineman Gloves](categories/sg/sg-1-1-1-2.md) |
| Toys & Games | 2,519 | [Toys & Games](categories/tg/tg.md) | [Game Timers](categories/tg/tg-1.md) | [Analog Game Timers](categories/tg/tg-1-1.md) |
| Uncategorized | 1 | [Uncategorized](categories/na/na.md) | No intermediate level | [Uncategorized](categories/na/na.md) |
| Vehicles & Parts | 3,860 | [Vehicles & Parts](categories/vp/vp.md) | [Vehicle Parts & Accessories](categories/vp/vp-1.md) | [Aircraft Agricultural Spray Systems](categories/vp/vp-1-1-10.md) |

Descriptions are generated from a finite set of editorial patterns populated
with category-specific source context and attributes. Sampling and automated
checks cover the initial edition; individual descriptions remain editable in
the committed editorial records.
