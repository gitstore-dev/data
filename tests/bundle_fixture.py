"""Generate the small CLI conformance fixture from its committed text definition.

Run: python tests/bundle_fixture.py --output /tmp/seed-v1-fixture
Keep generated layouts/archives outside Git; never publish this as a dataset.
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bundles
import datasets
import taxonomy

DEFINITION = datasets.ROOT / "tests/fixtures/seed-v1/definition.json"


def make_fixture(output):
    definition = json.loads(DEFINITION.read_bytes())
    current = copy.deepcopy(datasets.read_json("datasets/selections.lock.json"))
    categories = {row["id"]: row for row in taxonomy.rows(datasets.ROOT / "upstream/categories.en/gc.jsonl")}
    ids = sorted(categories, key=lambda identifier: (len(taxonomy.ancestry(categories[identifier], categories)), identifier))
    records = datasets.inventory(ids, categories)
    datasets.require(ids == definition["ids"], "fixture definition differs from canonical Gift Cards membership")
    current["dataset_version"] = definition["dataset_version"]
    selection = definition["selection"]
    current["selections"][selection] = {
        "resources": 3, "roots": 1, "manifest_bytes": sum(r["bytes"] for r in records),
        "verticals": {"gc": 3}, "inventory_sha256": datasets.sha256(datasets.encoded(records)),
        "ids_sha256": datasets.sha256(("\n".join(ids) + "\n").encode()),
        "rationale": definition["rationale"],
    }
    metadata = datasets.export_metadata(selection, current, records)
    inputs = {item["path"]: datasets.ROOT / item["path"] for item in [*records, *current["licenses"]]}
    output.mkdir(parents=True, exist_ok=False)
    identities = bundles.write_package(output / "package", metadata, inputs, definition["source_commit"],
                                       datasets.sha256((datasets.ROOT / "datasets/selections.lock.json").read_bytes()))
    bundle = bundles.verify_package(output / "package")
    vectors = {"package_format": bundles.FORMAT, "identities": identities,
               "file_count": bundle["file_count"], "expanded_bytes": bundle["expanded_bytes"],
               "resources": bundle["resources"]["resources"], "requirements": bundle["requirements"],
               "empty_config": bundles.descriptor(b"{}", bundles.EMPTY),
               "source_commit": definition["source_commit"]}
    (output / "vectors.json").write_bytes(datasets.encoded(vectors))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    make_fixture(parser.parse_args().output)
