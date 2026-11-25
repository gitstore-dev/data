import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("taxonomy", Path(__file__).resolve().parents[1] / "tools/taxonomy.py")
taxonomy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(taxonomy)

class EditorialRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.editorial = self.root / "editorial"
        self.source.mkdir()
        self.editorial.mkdir()
        self.category = {"id": "x", "title": "Example", "full_name": "Example", "parent": None, "vertical": "x", "attributes": []}
        (self.source / "x.jsonl").write_text(json.dumps(self.category) + "\n")
        self.patch_source = patch.object(taxonomy, "SOURCE", self.source)
        self.patch_editorial = patch.object(taxonomy, "EDITORIAL", self.editorial)
        self.patch_source.start(); self.patch_editorial.start()
        self.addCleanup(self.patch_source.stop); self.addCleanup(self.patch_editorial.stop)

    def test_completed_editorial_is_preserved_on_resume(self):
        original = {"id": "x", "paragraphs": ["Edited first paragraph.", "Edited second paragraph."]}
        path = self.editorial / "x.jsonl"
        path.write_text(taxonomy.dump(original) + "\n")
        before = path.read_bytes()
        taxonomy.editorial(1)
        self.assertEqual(before, path.read_bytes())

    def test_interrupted_checkpoint_keeps_prior_records(self):
        second = dict(self.category, id="x-2", title="Second", full_name="Second")
        third = dict(self.category, id="y", vertical="y", title="Other", full_name="Other")
        taxonomy.write_source([self.category, second, third])
        existing = {"id": "x", "paragraphs": ["First.", "Second."]}
        path = self.editorial / "x.jsonl"
        other_path = self.editorial / "y.jsonl"
        path.write_text(taxonomy.dump(existing) + "\n")
        other_path.write_text(taxonomy.dump(dict(existing, id="y")) + "\n")
        before = path.read_bytes()
        other_before = other_path.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("interrupted checkpoint")):
            with self.assertRaises(OSError):
                taxonomy.editorial(1)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(other_before, other_path.read_bytes())
        taxonomy.editorial(1)
        self.assertEqual({"x", "x-2", "y"}, {r["id"] for r in taxonomy.rows(self.editorial)})
        self.assertEqual(other_before, other_path.read_bytes())

    def test_source_round_trip_preserves_rows_and_checksums(self):
        second = dict(self.category, id="y", vertical="y", title="Other")
        taxonomy.write_source([second, self.category])
        self.assertEqual([self.category, second], taxonomy.rows(self.source))
        checksum, shards = taxonomy.source_checksums()
        self.assertEqual({"x.jsonl", "y.jsonl"}, shards.keys())
        taxonomy.write_source([self.category, second])
        self.assertEqual((checksum, shards), taxonomy.source_checksums())
        (self.source / "y.jsonl").unlink()
        self.assertNotEqual((checksum, shards), taxonomy.source_checksums())

    def test_duplicate_source_and_editorial_ids_are_rejected(self):
        path = self.source / "x.jsonl"
        path.write_text((taxonomy.dump(self.category) + "\n") * 2)
        with self.assertRaisesRegex(AssertionError, "duplicate source IDs"):
            taxonomy.index()
        row = {"id": "x", "paragraphs": ["First.", "Second."]}
        (self.editorial / "x.jsonl").write_text((taxonomy.dump(row) + "\n") * 2)
        with self.assertRaisesRegex(AssertionError, "duplicate editorial IDs"):
            taxonomy.descriptions_index()

    def test_records_in_wrong_vertical_are_rejected(self):
        (self.source / "x.jsonl").rename(self.source / "wrong.jsonl")
        with self.assertRaisesRegex(AssertionError, "belongs in x.jsonl"):
            taxonomy.index()
        (self.editorial / "wrong.jsonl").write_text(taxonomy.dump({"id": "x", "paragraphs": []}) + "\n")
        with self.assertRaisesRegex(AssertionError, "belongs in x.jsonl"):
            taxonomy.descriptions_index()

    def test_oversized_shard_does_not_replace_existing_content(self):
        path = self.editorial / "x.jsonl"
        path.write_text("original\n")
        with patch.object(taxonomy, "MAX_SHARD_BYTES", 10):
            with self.assertRaisesRegex(ValueError, "JSONL shard"):
                taxonomy.write(path, "a" * 10)
        self.assertEqual("original\n", path.read_text())

    def test_irrelevant_inherited_attribute_is_not_editorialized(self):
        category = dict(self.category, title="Memorial Urns", full_name="Memorial Urns",
            attributes=[{"name": "Band color", "description": "Watch band color."},
                        {"name": "Material", "description": "Primary material."}])
        paragraphs = taxonomy.prose(category, {"x": category}, {"x": []})
        self.assertNotIn("Band color", paragraphs[1])
        self.assertIn("Material", paragraphs[1])

if __name__ == "__main__":
    unittest.main()
