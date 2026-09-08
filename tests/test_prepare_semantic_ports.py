import hashlib
import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prepare", ROOT / "scripts/prepare-desktop-embedder.py"
)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class AppendedSemanticPorts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="prepare-port-test-", dir=ROOT / "artifacts"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.temp_override = patch.object(tempfile, "tempdir", str(self.root))
        self.temp_override.start()
        self.addCleanup(self.temp_override.stop)
        self.repo = self.root / "gecko"
        self.repo.mkdir()
        self.git("init", "--quiet")
        self.source = self.repo / "source.txt"
        self.source.write_text("one\n")
        self.git("add", "source.txt")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--quiet", "-m", "Fixture baseline")
        self.ports = []
        for index, content in enumerate([
            "@@ -1 +1,2 @@\n one\n+two\n",
            "@@ -1,2 +1,3 @@\n one\n two\n+three\n",
        ], 1):
            path = self.root / f"{index:04d}.patch"
            path.write_text("diff --git a/source.txt b/source.txt\n"
                            "--- a/source.txt\n+++ b/source.txt\n" + content)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.ports.append((path, f"{digest} {path.name}"))
        self.git("apply", str(self.ports[0][0]))
        self.state = self.repo / ".desktop-embedder-patches-applied"
        self.state.write_text(self.ports[0][1] + "\n")

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.repo), *arguments],
                              check=True, capture_output=True, text=True)

    def test_append_already_generated_is_recorded_only_after_exact_verification(self):
        self.git("apply", str(self.ports[1][0]))
        prepare.apply_ports(self.repo, self.ports)
        self.assertEqual(self.source.read_text(), "one\ntwo\nthree\n")
        self.assertEqual(self.state.read_text().splitlines(), [entry for _, entry in self.ports])
        prepare.apply_ports(self.repo, self.ports)

    def test_append_not_yet_generated_is_applied_normally(self):
        prepare.apply_ports(self.repo, self.ports)
        self.assertEqual(self.source.read_text(), "one\ntwo\nthree\n")
        self.assertEqual(self.state.read_text().splitlines(), [entry for _, entry in self.ports])

    def test_already_generated_with_unowned_changes_never_updates_state(self):
        self.git("apply", str(self.ports[1][0]))
        self.source.write_text(self.source.read_text() + "unowned change\n")
        before = self.state.read_text()
        with self.assertRaises(prepare.PrepareError):
            prepare.apply_ports(self.repo, self.ports)
        self.assertEqual(self.state.read_text(), before)
        self.assertTrue(self.source.read_text().endswith("unowned change\n"))


if __name__ == "__main__":
    unittest.main()
