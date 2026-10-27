"""Synthetic archive integrity and path-containment checks; no network/data use."""
import hashlib
from pathlib import Path
import stat
import tempfile
import unittest
from zipfile import ZipFile, ZipInfo

from fetch_data import digest, expected_path, extract_verified


class SnapshotFetcherTests(unittest.TestCase):
    member = "Synthetic/2024/records.csv"
    payload = b"id,value\nfixture,1\n"

    def archive(self, root, names=None, mode=None):
        path = root / "fixture.zip"
        with ZipFile(path, "w") as handle:
            for name in names or [self.member]:
                if mode is None:
                    handle.writestr(name, self.payload)
                else:
                    info = ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = mode << 16
                    handle.writestr(info, self.payload)
        spec = {"member": self.member, "bytes": len(self.payload),
                "sha256": hashlib.sha256(self.payload).hexdigest(), "zip_sha256": digest(path)}
        return path, spec

    def test_extracts_only_verified_expected_member(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, spec = self.archive(root)
            target = extract_verified(archive, root / "inputs", spec)
            self.assertEqual(target, (root / "inputs" / self.member).resolve())
            self.assertEqual(target.read_bytes(), self.payload)

    def test_rejects_traversal_and_unexpected_members(self):
        for names in (["../outside.csv"], [self.member, "unexpected.txt"], ["C:/outside.csv"]):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive, spec = self.archive(root, names)
                with self.assertRaises(ValueError):
                    extract_verified(archive, root / "inputs", spec)
                self.assertFalse((root / "outside.csv").exists())

    def test_rejects_symlink_and_other_special_members(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive, spec = self.archive(root, mode=mode)
                with self.assertRaises(ValueError):
                    extract_verified(archive, root / "inputs", spec)

    def test_csv_and_archive_checksums_are_both_required(self):
        for field in ("sha256", "zip_sha256"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive, spec = self.archive(root)
                spec[field] = "0" * 64
                with self.assertRaises(ValueError):
                    extract_verified(archive, root / "inputs", spec)
                self.assertFalse((root / "inputs" / self.member).exists())

    def test_size_mismatch_and_existing_file_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, spec = self.archive(root)
            with self.assertRaises(ValueError):
                extract_verified(archive, root / "inputs", dict(spec, bytes=1))
            target = root / "inputs" / self.member
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"existing user input")
            with self.assertRaises(FileExistsError):
                extract_verified(archive, root / "inputs", spec)
            self.assertEqual(target.read_bytes(), b"existing user input")

    def test_expected_path_rejects_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                expected_path(Path(temporary) / "inputs", "../outside.csv")


if __name__ == "__main__":
    unittest.main()
