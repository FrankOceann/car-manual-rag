"""Safety checks for backup/restore, without requiring Docker or private data."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import backup_common as backup


class BackupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        files = ["postgres.dump", *(key + ".tar.gz" for key in backup.DATA_VOLUMES)]
        for name in files:
            (self.folder / name).write_bytes(b"private-test-data")
        self.manifest = {
            "format": 1, "project": "source", "archive_image": "sha256:test",
            "images": {name: name + ":fixed" for name in ("postgres", "redis", "chroma")},
            "sha256": {name: backup.digest(self.folder / name) for name in files},
        }
        self.save_manifest()

    def save_manifest(self):
        (self.folder / "manifest.json").write_text(json.dumps(self.manifest))

    def test_same_project_refused_before_docker(self):
        with patch.object(backup, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "new project"):
                backup.restore("source", self.folder)
            run.assert_not_called()

    def test_corrupt_archive_refused_before_docker(self):
        (self.folder / "manuals.tar.gz").write_bytes(b"corrupt")
        with patch.object(backup, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                backup.restore("target", self.folder)
            run.assert_not_called()

    def test_manifest_path_traversal_refused_before_reading(self):
        self.manifest["sha256"]["../outside"] = "invalid"
        self.save_manifest()
        with patch.object(backup, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "unexpected or missing"):
                backup.restore("target", self.folder)
            run.assert_not_called()

    def test_existing_volume_is_never_overwritten(self):
        config = {"volumes": {"postgres": {"name": "target_postgres"}}}
        with patch.object(backup, "config", return_value=config), \
             patch.object(backup, "run", return_value="target_postgres") as run:
            with self.assertRaisesRegex(RuntimeError, "already exist"):
                backup.restore("target", self.folder)
            self.assertEqual(run.call_args.args[:3], ("docker", "volume", "ls"))
            self.assertEqual(run.call_count, 1)

    def test_backup_failure_resumes_previously_running_services(self):
        config = {
            "services": {"postgres": {"environment": {"POSTGRES_USER": "rag", "POSTGRES_DB": "rag"}}}
        }
        def compose(project, *args, **kwargs):
            if args[0] == "ps":
                return "postgres\nredis\nchroma\napi\nworker"
            if args[0] == "cp":
                raise OSError("backup disk unavailable")
            return ""
        with patch.object(backup, "config", return_value=config), \
             patch.object(backup, "compose", side_effect=compose) as runner:
            with self.assertRaisesRegex(OSError, "disk unavailable"):
                backup.backup("source", self.folder / "new-backup")
            self.assertEqual(runner.call_args.args, ("source", "start", "worker", "api"))
            self.assertFalse((self.folder / "new-backup" / "manifest.json").exists())

    def test_restore_archive_command_rejects_links_and_path_traversal_on_python311(self):
        with patch.object(backup, "run") as run:
            backup.archive_volume("archive-image", "target_volume", self.folder, "manuals.tar.gz", True)
        code = run.call_args.args[-2]
        self.assertIn("m.isdir() or m.isreg()", code)
        self.assertIn("p in q.parents", code)
        self.assertNotIn("data_filter", code)

    def test_docker_output_is_decoded_as_utf8(self):
        with patch.object(backup.subprocess, "run", return_value=SimpleNamespace(stdout="{}")) as run:
            self.assertEqual(backup.run("docker", "version", capture=True), "{}")
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")

    def test_backup_archiving_materializes_only_in_volume_regular_files(self):
        with patch.object(backup, "run") as run:
            backup.archive_volume("archive-image", "source_volume", self.folder, "models.tar.gz", False)
        code = run.call_args.args[-2]
        self.assertIn("r.resolve()", code)
        self.assertIn("Unsafe archive source", code)
        self.assertIn("recursive=False", code)


if __name__ == "__main__":
    unittest.main()
