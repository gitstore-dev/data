import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import datasets


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        root = {"id": "x", "vertical": "x", "title": "Root", "parent": None}
        child = dict(root, id="x-1", title="Child", parent="x")
        self.categories = {"x-1": child, "x": root}  # Intentionally child first.
        self.patch_root = patch.object(datasets, "ROOT", self.root)
        self.patch_root.start()
        self.addCleanup(self.patch_root.stop)
        (self.root / "ids").write_text("x-1\nx\n")
        for identifier, links in [("x", "[Child](x-1.md)"), ("x-1", "[Root](x.md)")]:
            path = self.root / "categories/x" / f"{identifier}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {identifier}\n\n{links}\n\n[License](../../LICENSE-SHOPIFY)\n")
        for license in datasets.LICENSES:
            (self.root / license).write_text("License notice\n")
        self.definition = {"ids_file": "ids", "resources": 2, "roots": 1}

    def test_release_version_consistency_and_bootstrap(self):
        def write(version, dataset, lock=None, manifest=None):
            (self.root / "datasets").mkdir(exist_ok=True)
            for name, value in {
                ".release-please-manifest.json": {".": manifest or version},
                "release-please-config.json": {"packages": {".": {"initial-version": "0.1.0"}}},
                "datasets/definitions.json": {"dataset_version": dataset},
                "datasets/selections.lock.json": {"dataset_version": lock or dataset},
            }.items():
                (self.root / name).write_text(json.dumps(value))
            (self.root / "version.txt").write_text(version + "\n")
        for version, dataset in [("0.0.0", "0.1.0"), ("0.1.0", "0.1.0"), ("0.2.0", "0.2.0")]:
            write(version, dataset)
            datasets.verify_release_versions()
        for args in [("0.0.0", "0.2.0"), ("0.2.0", "0.1.0"),
                     ("0.2.0", "0.2.0", "0.1.0"), ("0.2.0", "0.2.0", None, "0.1.0")]:
            write(*args)
            with self.subTest(args=args), self.assertRaises(ValueError):
                datasets.verify_release_versions()

    def test_parent_first_order_is_independent_of_source_and_allowlist_order(self):
        expected = ["x", "x-1"]
        self.assertEqual(expected, datasets.selected_ids(self.definition, self.categories))
        (self.root / "ids").write_text("x\nx-1\n")
        self.assertEqual(expected, datasets.selected_ids(self.definition, dict(reversed(self.categories.items()))))
        self.assertEqual(expected, datasets.selected_ids(
            {"all_source_ids": True, "resources": 2, "roots": 1}, self.categories))

    def test_invalid_membership_fails_closed(self):
        for content, error in [("x\nx\n", "duplicate"), ("x\ny\n", "missing from source"),
                               ("x\n", "resource count")]:
            with self.subTest(content=content):
                (self.root / "ids").write_text(content)
                with self.assertRaisesRegex(ValueError, error):
                    datasets.selected_ids(self.definition, self.categories)

    def test_missing_ancestor_and_cycle_are_rejected(self):
        (self.root / "ids").write_text("x-1\n")
        with self.assertRaisesRegex(ValueError, "missing ancestor"):
            datasets.selected_ids(dict(self.definition, resources=1), self.categories)
        self.categories["x"]["parent"] = "x-1"
        with self.assertRaisesRegex(AssertionError, "cycle"):
            datasets.selected_ids(dict(self.definition, resources=1), self.categories)

    def test_ancestor_closed_selection_with_missing_navigation_target_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "broken link"):
            datasets.inventory(["x"], self.categories)
        records = datasets.inventory(["x", "x-1"], self.categories)
        self.assertEqual([None, "shopify-x"], [r["parent"] for r in records])
        self.assertEqual(["Root", "Child"], [r["title"] for r in records])

    def test_external_local_links_are_rejected(self):
        path = self.root / "categories/x/x.md"
        path.write_text("[Outside](../../../elsewhere)\n")
        with self.assertRaisesRegex(ValueError, "broken link"):
            datasets.inventory(["x", "x-1"], self.categories)

    def export_inputs(self):
        records = datasets.inventory(["x", "x-1"], self.categories)
        licenses = [{"path": name, "sha256": datasets.sha256((self.root / name).read_bytes()),
                     "bytes": (self.root / name).stat().st_size} for name in datasets.LICENSES]
        current = {"dataset_version": "0.1.0", "licenses": licenses,
                   "selections": {"starter": {"resources": 2}}, "ordering": datasets.ORDER}
        return current, records

    def test_exports_are_byte_identical_and_have_only_payload_metadata_and_licenses(self):
        current, records = self.export_inputs()
        first, second = self.root / "dist/first", self.root / "dist/second"
        canonical_before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        for output in (first, second):
            datasets.export(output, "starter", current, records)
        def files(root):
            return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(files(first), files(second))
        self.assertEqual({"dataset.json", "LICENSE", "LICENSE-SHOPIFY", "categories/x/x.md", "categories/x/x-1.md"}, set(files(first)))
        metadata = json.loads((first / "dataset.json").read_bytes())
        self.assertEqual(records, metadata["inventory"])
        for record in records:
            self.assertEqual((self.root / record["path"]).read_bytes(), (first / record["path"]).read_bytes())
        for path, original in canonical_before.items():
            self.assertEqual(original, (self.root / path).read_bytes())

    def test_export_refuses_existing_output_and_repository_paths(self):
        current, records = self.export_inputs()
        with self.assertRaisesRegex(ValueError, "already exists"):
            datasets.export(self.root / "categories", "starter", current, records)
        with self.assertRaisesRegex(ValueError, "under dist"):
            datasets.export(self.root / "new-canonical", "starter", current, records)

    def test_changed_source_during_export_leaves_no_partial_output(self):
        current, records = self.export_inputs()
        (self.root / records[0]["path"]).write_text("changed\n")
        output = self.root / "dist/failed"
        with self.assertRaisesRegex(ValueError, "changed during export"):
            datasets.export(output, "starter", current, records)
        self.assertFalse(output.exists())
        self.assertEqual([], list(output.parent.glob(".seed-export-*")))

    def test_lock_drift_is_rejected(self):
        path = self.root / "datasets/selections.lock.json"
        path.parent.mkdir()
        path.write_bytes(datasets.encoded({"resources": 2}))
        datasets.verify_lock({"resources": 2})
        with self.assertRaisesRegex(ValueError, "lock differs"):
            datasets.verify_lock({"resources": 3})


if __name__ == "__main__":
    unittest.main()
