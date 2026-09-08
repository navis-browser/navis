#!/usr/bin/env python3

"""Integration tests for the deterministic Gecko ESR review dossier."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parent.parent
TOOL = WORKSPACE.parent / "runtime/scripts/review-gecko-esr-update.py"


class GeckoEsrReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="desktop-embedder-esr-review-test."
        )
        self.root = Path(self.temporary.name)
        self.repo = self.root / "upstream"
        self.pin = self.root / "base-pin.json"
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "GIT_AUTHOR_NAME": "Navis Test",
                "GIT_AUTHOR_EMAIL": "navis-test.invalid",
                "GIT_COMMITTER_NAME": "Navis Test",
                "GIT_COMMITTER_EMAIL": "navis-test.invalid",
                "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
                "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
                "LC_ALL": "C",
                "TZ": "UTC",
            }
        )
        self._create_diverged_history()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *arguments: str, repo: Path | None = None) -> str:
        result = subprocess.run(
            ["git", "-C", os.fspath(repo or self.repo), *arguments],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.environment,
        )
        return result.stdout.strip()

    def commit(self, subject: str) -> None:
        self.git("add", "--all")
        self.git("commit", "--quiet", "--message", subject)

    def _create_diverged_history(self) -> None:
        self.repo.mkdir()
        subprocess.run(
            ["git", "init", "--quiet", "--initial-branch=main", self.repo],
            check=True,
            env=self.environment,
        )
        (self.repo / "toolkit").mkdir()
        (self.repo / "toolkit/moz.configure").write_text(
            "base\n", encoding="utf-8"
        )
        (self.repo / "dom/base").mkdir(parents=True)
        (self.repo / "dom/base/Engine.cpp").write_text("base\n", encoding="utf-8")
        self.commit("common")
        self.git("tag", "COMMON")

        self.git("switch", "--quiet", "--create", "base-esr")
        (self.repo / "dom/base/Engine.cpp").write_text(
            "base-esr-change\n", encoding="utf-8"
        )
        self.commit("base-esr-fix")
        self.git("tag", "BASE_ESR")
        base_commit = self.git("rev-parse", "BASE_ESR")

        self.git("switch", "--quiet", "--create", "target-esr", "COMMON")
        (self.repo / "toolkit/moz.configure").write_text(
            "target-esr-build-hook\n", encoding="utf-8"
        )
        self.commit("target-build-change")
        (self.repo / "README.test").write_text(
            "target general change\n", encoding="utf-8"
        )
        self.commit("target-general-change")
        self.git("tag", "TARGET_ESR")

        self.pin.write_text(
            json.dumps(
                {
                    "remote": "local-test",
                    "tag": "BASE_ESR",
                    "build_tag": "BASE_ESR_BUILD1",
                    "commit": base_commit,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def invoke(
        self, repo: Path, output: Path, *extra: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                os.fspath(TOOL),
                "--repo",
                os.fspath(repo),
                "--from-pin",
                os.fspath(self.pin),
                "--to-ref",
                "TARGET_ESR",
                "--to-label",
                "target-test",
                "--output",
                os.fspath(output),
                *extra,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.environment,
        )

    def test_complete_diverged_history_is_deterministic(self) -> None:
        first = self.root / "review-1.json"
        second = self.root / "review-2.json"
        for output in (first, second):
            result = self.invoke(self.repo, output, "--include-commit-paths")
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(first.read_bytes(), second.read_bytes())

        report = json.loads(first.read_text(encoding="utf-8"))
        self.assertEqual(report["coverage"]["commit_history"], "complete")
        self.assertEqual(
            report["history"]["topology"], "diverged-from-common-ancestor"
        )
        self.assertEqual(report["history"]["base_side"]["commit_count"], 1)
        self.assertEqual(report["history"]["target_side"]["commit_count"], 2)
        self.assertEqual(report["tree_delta"]["path_count"], 3)
        self.assertEqual(len(report["tree_delta"]["paths"]), 3)
        port_paths = report["tree_delta"]["semantic_port_changed_paths"]
        self.assertEqual([entry["path"] for entry in port_paths], ["toolkit/moz.configure"])

    def test_shallow_endpoints_fail_closed(self) -> None:
        shallow = self.root / "shallow"
        subprocess.run(
            [
                "git",
                "clone",
                "--quiet",
                "--depth=1",
                "--branch=base-esr",
                f"file://{self.repo}",
                os.fspath(shallow),
            ],
            check=True,
            env=self.environment,
        )
        self.git(
            "fetch", "--quiet", "--depth=1", "origin", "tag", "TARGET_ESR", repo=shallow
        )

        rejected = self.root / "rejected.json"
        result = self.invoke(shallow, rejected, "--summary-only")
        self.assertEqual(result.returncode, 2)
        self.assertIn("complete commit coverage is unavailable", result.stderr)
        self.assertFalse(rejected.exists())

        accepted = self.root / "explicitly-incomplete.json"
        result = self.invoke(
            shallow,
            accepted,
            "--summary-only",
            "--allow-incomplete-history",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(accepted.read_text(encoding="utf-8"))
        self.assertEqual(report["coverage"]["tree_delta"], "complete")
        self.assertEqual(report["coverage"]["commit_history"], "incomplete")
        self.assertNotIn("paths", report["tree_delta"])


if __name__ == "__main__":
    unittest.main()
