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
```

The accompanying GitStore tests exercise concurrent API instances using the same
namespace-free root/child tree in two stores, correct parent names and ancestor
paths, cross-replica replay, path moves, authorization denials, invalid explicit
namespaces and unavailable repository scope. This is an in-process memdb
acceptance fixture; full-taxonomy deployed ingestion and Scylla qualification
have not been performed.

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
