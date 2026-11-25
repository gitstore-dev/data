import gzip
import io
import json
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bundles
import datasets
from bundle_fixture import make_fixture

FIXTURE = Path(__file__).resolve().parent / "fixtures/seed-v1"


class BundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated_directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.generated_directory.cleanup)
        cls.generated_fixture = Path(cls.generated_directory.name).resolve() / "fixture"
        make_fixture(cls.generated_fixture)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.package = self.root / "package"
        shutil.copytree(self.generated_fixture / "package", self.package)
        self.archive = self.package / "seed.tar.gz"
        self.vectors = json.loads((FIXTURE / "vectors.json").read_bytes())
        self.archive_digest = self.vectors["identities"]["archive"]["digest"]

    def entries(self):
        with tarfile.open(self.archive) as archive:
            return [(info, archive.extractfile(info).read()) for info in archive]

    def repack(self, entries):
        path = self.root / "mutated.tar.gz"
        with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0, compresslevel=9) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as tar:
                for info, content in entries:
                    tar.addfile(info, io.BytesIO(content) if info.isreg() else None)
        return path, bundles.file_hash(path, bundles.MAX_ARCHIVE)["digest"]

    def changed_payload(self, change):
        entries = {info.name: data for info, data in self.entries()}
        metadata = json.loads(entries["dataset.json"])
        change(entries, metadata)
        for record in metadata["inventory"]:
            if record["path"] in entries:
                content = entries[record["path"]]
                record.update(bytes=len(content), sha256=bundles.sha256(content))
        metadata["manifest_bytes"] = sum(r["bytes"] for r in metadata["inventory"])
        metadata["inventory_sha256"] = bundles.sha256(bundles.encoded(metadata["inventory"]))
        entries["dataset.json"] = bundles.encoded(metadata)
        original = json.loads(entries.pop("bundle.json"))
        files = [{"path": name, "bytes": len(content), "sha256": bundles.sha256(content)} for name, content in sorted(entries.items())]
        manifest = bundles.bundle_manifest(metadata, files, original["source"]["commit"], original["provenance"]["selection_lock_sha256"])
        entries = {"bundle.json": bundles.encoded(manifest), **dict(sorted(entries.items()))}
        return self.repack([(bundles.header(name, len(content)), content) for name, content in entries.items()])

    def test_fixture_vectors_and_offline_oci_archive_identity(self):
        manifest_digest = self.vectors["identities"]["oci_manifest"]["digest"]
        bundle = bundles.verify_package(self.package, manifest_digest)
        self.assertEqual(3, bundle["resources"]["resources"])
        self.assertEqual(self.vectors["expanded_bytes"], bundle["expanded_bytes"])
        self.assertNotEqual(manifest_digest, self.archive_digest)
        blob = self.package / "blobs/sha256" / self.archive_digest[7:]
        self.assertEqual(self.archive.read_bytes(), blob.read_bytes())
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            bundles.verify_archive(self.archive, manifest_digest)

    def test_fixture_regenerates_byte_for_byte_and_preserves_canonical_resources(self):
        make_fixture(self.root / "regenerated")
        def contents(root):
            return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
        self.assertEqual(contents(self.generated_fixture), contents(self.root / "regenerated"))
        self.assertEqual((FIXTURE / "vectors.json").read_bytes(),
                         (self.root / "regenerated/vectors.json").read_bytes())
        output = self.root / "unpacked"
        bundles.verify_archive(self.archive, self.archive_digest, extract_to=output)
        for path in output.glob("categories/*/*.md"):
            self.assertEqual(path.read_bytes(), (datasets.ROOT / path.relative_to(output)).read_bytes())
        self.assertEqual(self.vectors["file_count"], len(contents(output)))
        self.assertEqual(self.vectors["expanded_bytes"], sum(len(c) for c in contents(output).values()))

    def test_fixture_is_independent_of_production_version_bumps(self):
        source = self.root / "source"
        paths = ["upstream/categories.en/gc.jsonl", "datasets/selections.lock.json",
                 *datasets.LICENSES, *[f"categories/gc/{name}.md" for name in ("gc", "gc-1", "gc-2")]]
        for relative in paths:
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(datasets.ROOT / relative, target)
        lock_path = source / "datasets/selections.lock.json"
        lock = json.loads(lock_path.read_bytes())
        lock["dataset_version"] = "0.2.0"
        lock_path.write_bytes(datasets.encoded(lock))
        with patch.object(datasets, "ROOT", source):
            make_fixture(self.root / "next-version")
        self.assertEqual((FIXTURE / "vectors.json").read_bytes(),
                         (self.root / "next-version/vectors.json").read_bytes())
        self.assertEqual(self.archive.read_bytes(), (self.root / "next-version/package/seed.tar.gz").read_bytes())

    def test_target_preflight_happens_before_extraction(self):
        for caps, apis in [([], [bundles.API]), ([bundles.CAPABILITY], []), (None, [bundles.API])]:
            output = self.root / "rejected"
            with self.subTest(caps=caps, apis=apis), self.assertRaises(ValueError):
                bundles.verify_archive(self.archive, self.archive_digest, extract_to=output, capabilities=caps, resource_apis=apis)
            self.assertFalse(output.exists())
        bundles.verify_archive(self.archive, self.archive_digest, capabilities=[bundles.CAPABILITY], resource_apis=[bundles.API])

    def test_tampered_archive_is_rejected_before_parsing(self):
        content = bytearray(self.archive.read_bytes())
        content[-1] ^= 1
        self.archive.write_bytes(content)
        with patch.object(bundles, "decompress", side_effect=AssertionError("must not decompress")):
            with self.assertRaisesRegex(ValueError, "archive digest mismatch"):
                bundles.verify_archive(self.archive, self.archive_digest)

    def test_unsupported_format_is_rejected_before_extraction(self):
        entries = self.entries()
        manifest = json.loads(entries[0][1])
        manifest["package_format"] = "gitstore.seed/v2"
        content = bundles.encoded(manifest)
        entries[0] = bundles.header("bundle.json", len(content)), content
        path, checksum = self.repack(entries)
        with self.assertRaisesRegex(ValueError, "unsupported package format"):
            bundles.verify_archive(path, checksum, extract_to=self.root / "output")
        self.assertFalse((self.root / "output").exists())

    def test_unsafe_archive_paths_and_entry_types_are_rejected(self):
        for name in ["../escape", "/absolute", "categories/../escape.md", "C:\\escape", "categories/gc/script.sh", ".git/config", "media/image.png"]:
            entries = self.entries()
            entries[-1][0].name = name
            path, checksum = self.repack(entries)
            with self.subTest(name=name), self.assertRaises(ValueError):
                bundles.verify_archive(path, checksum)
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.XHDTYPE]:
            entries = self.entries()
            info = entries[-1][0]
            info.type, info.size, info.linkname = kind, 0, "../escape"
            entries[-1] = info, b""
            path, checksum = self.repack(entries)
            with self.subTest(kind=kind), self.assertRaises((ValueError, tarfile.TarError)):
                bundles.verify_archive(path, checksum)

    def test_tar_permissions_owner_and_time_must_be_canonical(self):
        for field, value in [("mode", 0o755), ("uid", 1), ("gid", 1), ("mtime", 1), ("uname", "root")]:
            entries = self.entries()
            setattr(entries[-1][0], field, value)
            path, checksum = self.repack(entries)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "noncanonical tar entry"):
                bundles.verify_archive(path, checksum)

    def test_duplicate_missing_and_reordered_entries_are_rejected(self):
        original = self.entries()
        for entries in [original + [original[-1]], original[:-1], [original[0], *reversed(original[1:])]]:
            path, checksum = self.repack(entries)
            with self.assertRaises(ValueError):
                bundles.verify_archive(path, checksum)

    def test_gzip_footer_truncation_trailing_members_and_bad_crc_are_rejected(self):
        original = self.archive.read_bytes()
        for content in [original[:-1], original + original, original + b"junk", original[:-8] + b"\0" * 8]:
            self.archive.write_bytes(content)
            checksum = "sha256:" + bundles.sha256(content)
            with self.assertRaises((ValueError, bundles.zlib.error)):
                bundles.verify_archive(self.archive, checksum)

    def test_compression_expansion_file_and_count_limits_are_enforced(self):
        for field in ["MAX_ARCHIVE", "MAX_TAR", "MAX_EXPANDED", "MAX_RESOURCE", "MAX_FILES"]:
            with self.subTest(field=field), patch.object(bundles, field, 1), self.assertRaises(ValueError):
                bundles.verify_archive(self.archive, self.archive_digest)

    def test_resource_namespace_media_file_kind_and_duplicate_yaml_are_rejected(self):
        path = "categories/gc/gc-1.md"
        for field in ["namespace", "media", "kind", "duplicate"]:
            def change(entries, metadata):
                _, frontmatter, body = entries[path].decode().split("---\n", 2)
                document = bundles.yaml.safe_load(frontmatter)
                if field == "namespace":
                    document["metadata"]["namespace"] = "store-a"
                elif field == "media":
                    document["spec"]["media"] = []
                elif field == "kind":
                    document["kind"] = "File"
                content = "---\n" + bundles.yaml.safe_dump(document) + ("kind: CategoryTaxonomy\n" if field == "duplicate" else "") + "---\n" + body
                entries[path] = content.encode()
            archive, checksum = self.changed_payload(change)
            with self.subTest(field=field), self.assertRaises((ValueError, bundles.ValidationError)):
                bundles.verify_archive(archive, checksum)

    def test_incomplete_hierarchy_and_broken_navigation_are_rejected(self):
        def missing_parent(entries, metadata):
            metadata["inventory"][1]["parent"] = "shopify-absent"
        archive, checksum = self.changed_payload(missing_parent)
        with self.assertRaisesRegex(ValueError, "missing parent"):
            bundles.verify_archive(archive, checksum)
        def bad_link(entries, metadata):
            entries["categories/gc/gc-1.md"] += b"\n[Missing](absent.md)\n"
        archive, checksum = self.changed_payload(bad_link)
        with self.assertRaisesRegex(ValueError, "broken local navigation"):
            bundles.verify_archive(archive, checksum)

    def test_oci_tampering_is_rejected_without_extraction(self):
        for name in ["index.json", "manifest.json", "blobs/sha256/" + bundles.sha256(b"{}")]:
            original = (self.package / name).read_bytes()
            (self.package / name).write_bytes(b"{}\n")
            output = self.root / "rejected"
            with self.subTest(name=name), self.assertRaises(ValueError):
                bundles.verify_package(self.package, extract_to=output)
            self.assertFalse(output.exists())
            (self.package / name).write_bytes(original)

    def test_extra_layout_files_and_symlinks_are_rejected(self):
        extra = self.package / "run.sh"
        extra.write_text("echo unexpected\n")
        with self.assertRaisesRegex(ValueError, "unexpected layout content"):
            bundles.verify_package(self.package)
        extra.unlink()
        extra.symlink_to(self.archive)
        with self.assertRaisesRegex(ValueError, "symlink"):
            bundles.verify_package(self.package)

    def test_existing_output_is_never_overwritten(self):
        output = self.root / "existing"
        output.mkdir()
        (output / "retained").write_text("keep\n")
        with self.assertRaisesRegex(ValueError, "already exists"):
            bundles.verify_archive(self.archive, self.archive_digest, extract_to=output)
        self.assertEqual("keep\n", (output / "retained").read_text())

    def test_duplicate_json_and_invalid_payload_accounting_are_rejected(self):
        original = self.entries()
        for field, value in [("expanded_bytes", 1), ("file_count", 1)]:
            entries = list(original)
            manifest = json.loads(entries[0][1])
            manifest[field] = value
            content = bundles.encoded(manifest)
            entries[0] = bundles.header("bundle.json", len(content)), content
            archive, checksum = self.repack(entries)
            with self.subTest(field=field), self.assertRaises(ValueError):
                bundles.verify_archive(archive, checksum)
        entries = list(original)
        content = entries[0][1].replace(b'{\n', b'{\n  "package_format": "gitstore.seed/v1",\n', 1)
        entries[0] = bundles.header("bundle.json", len(content)), content
        archive, checksum = self.repack(entries)
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            bundles.verify_archive(archive, checksum)

    def test_nonzero_tar_padding_and_footer_are_rejected(self):
        original = gzip.decompress(self.archive.read_bytes())
        first_size = self.entries()[0][0].size
        for offset in [512 + first_size, len(original) - 1]:
            changed = bytearray(original)
            changed[offset] = 1
            with self.archive.open("wb") as target, gzip.GzipFile(fileobj=target, mode="wb", filename="", mtime=0, compresslevel=9) as stream:
                stream.write(changed)
            checksum = "sha256:" + bundles.sha256(self.archive.read_bytes())
            with self.assertRaises(ValueError):
                bundles.verify_archive(self.archive, checksum)

    def test_unsupported_oci_type_fails_before_payload_parse(self):
        manifest = json.loads((self.package / "manifest.json").read_bytes())
        manifest["artifactType"] = "application/vnd.gitstore.seed.v2"
        content = bundles.encoded(manifest)
        (self.package / "manifest.json").write_bytes(content)
        checksums = json.loads((self.package / "checksums.json").read_bytes())
        checksums["oci_manifest"] = bundles.descriptor(content, bundles.OCI_MANIFEST)
        (self.package / "checksums.json").write_bytes(bundles.encoded(checksums))
        with patch.object(bundles, "verify_archive", side_effect=AssertionError("must not parse payload")):
            with self.assertRaisesRegex(ValueError, "unsupported OCI artifact"):
                bundles.verify_package(self.package)

    def test_builder_rejects_uncommitted_inputs_and_unexpected_files(self):
        with patch.object(bundles.subprocess, "check_output", return_value=b" M tools/bundles.py\n"):
            with self.assertRaisesRegex(ValueError, "commit canonical"):
                bundles.build("starter", self.root / "unbuilt")
        entries = {info.name: content for info, content in self.entries()}
        metadata = json.loads(entries.pop("dataset.json"))
        manifest = json.loads(entries.pop("bundle.json"))
        entries["run.sh"] = b"unexpected\n"
        output = self.root / "unbuilt"
        with self.assertRaisesRegex(ValueError, "unexpected path"):
            bundles.write_package(output, metadata, entries, manifest["source"]["commit"], manifest["provenance"]["selection_lock_sha256"])
        self.assertFalse(output.exists())

    def test_binary_or_incomplete_license_notice_is_rejected(self):
        for replacement in [b"incomplete notice\n", b"\x00Permission is hereby granted THE SOFTWARE IS PROVIDED"]:
            def change(entries, metadata):
                entries["LICENSE-SHOPIFY"] = replacement
                for item in metadata["licenses"]:
                    if item["path"] == "LICENSE-SHOPIFY":
                        item.update(bytes=len(replacement), sha256=bundles.sha256(replacement))
            archive, checksum = self.changed_payload(change)
            with self.assertRaisesRegex(ValueError, "invalid MIT license"):
                bundles.verify_archive(archive, checksum)


if __name__ == "__main__":
    unittest.main()
