"""Exercise the host job override without preparing Gecko or compiling."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MachBuildJobsTest(unittest.TestCase):
    def run_mach(self, jobs, *arguments):
        with tempfile.TemporaryDirectory(prefix="navis-mach-jobs-") as directory:
            gecko = Path(directory)
            for name in (".git", "navis", "desktop-embedder"):
                (gecko / name).mkdir()
            mach = gecko / "mach"
            mach.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
            mach.chmod(0o755)
            env = os.environ.copy()
            env.update(NAVIS_GECKO_DIR=str(gecko), NAVIS_PYTHON=sys.executable,
                       NAVIS_MOZCONFIG=str(ROOT.parent / "runtime/mozconfig.runtime"),
                       NAVIS_BUILD_JOBS=jobs, NAVIS_MOZBUILD_STATE_PATH="", MOZBUILD_STATE_PATH="")
            return subprocess.run(["bash", str(ROOT / "scripts/mach.sh"), *arguments],
                                  env=env, capture_output=True, text=True, timeout=10)

    def test_override_preserves_build_targets(self):
        result = self.run_mach("4", "build", "buildid.h")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["build", "--jobs=4", "buildid.h"])

    def test_unset_keeps_profile_default(self):
        result = self.run_mach("", "build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["build"])

    def test_configure_is_unchanged(self):
        result = self.run_mach("4", "configure")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["configure"])

    def test_invalid_override_is_rejected_before_mach(self):
        for jobs in ("0", "-1", "04", "4 --other", "4;true"):
            with self.subTest(jobs=jobs):
                result = self.run_mach(jobs, "build")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("positive integer", result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
