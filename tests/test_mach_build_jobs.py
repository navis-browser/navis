# SPDX-License-Identifier: MPL-2.0

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
    def run_mach(self, jobs, *arguments, recursive=False):
        with tempfile.TemporaryDirectory(prefix="navis-mach-jobs-") as directory:
            gecko = Path(directory)
            for name in (".git", "navis", "desktop-embedder"):
                (gecko / name).mkdir()
            mach = gecko / "mach"
            if recursive:
                (gecko / "child.mk").write_text(
                    'all:\n\t@echo flags=$(MAKEFLAGS) override=$(MOZ_MAKE_FLAGS)\n')
                (gecko / "parent.mk").write_text(
                    'MOZ_MAKE_FLAGS=-j16\nall:\n\t+@$(MAKE) --no-print-directory '
                    '-f child.mk $(MOZ_MAKE_FLAGS)\n')
                mach.write_text('import subprocess\nsubprocess.run('
                    '["make", "--no-print-directory", "-f", "parent.mk", "-j4"], check=True)\n')
            else:
                mach.write_text("import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
            mach.chmod(0o755)
            env = os.environ.copy()
            env.update(NAVIS_GECKO_DIR=str(gecko), NAVIS_PYTHON=sys.executable,
                       NAVIS_MOZCONFIG=str(ROOT.parent / "runtime/mozconfig.runtime"),
                       NAVIS_BUILD_JOBS=jobs, NAVIS_MOZBUILD_STATE_PATH="", MOZBUILD_STATE_PATH="")
            env.pop("MAKEFLAGS", None)
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

    def test_recursive_make_respects_override_not_profile_default(self):
        result = self.run_mach("4", "build", recursive=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("override=-j4", result.stdout)
        self.assertNotIn("-j16", result.stdout)

    def test_recursive_make_keeps_default_without_override(self):
        result = self.run_mach("", "build", recursive=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-j16", result.stdout)
        self.assertNotIn("override=-j4", result.stdout)

    def test_invalid_override_is_rejected_before_mach(self):
        for jobs in ("0", "-1", "04", "4 --other", "4;true"):
            with self.subTest(jobs=jobs):
                result = self.run_mach(jobs, "build")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("positive integer", result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
