#!/usr/bin/env python3
"""E2E test runner - discovers tests, runs them, manages snapshots."""

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Snapshot:
    """Captures CLI output and git state."""

    stdout: str
    stderr: str
    exit_code: int
    current_branch: str
    branches: list[str]
    commits: dict[str, dict]  # {branch: {message, has_trailer}}

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, data: str) -> "Snapshot":
        return cls(**json.loads(data))


class TestRunner:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.e2e_dir = project_root / "e2e"
        self.helpers = self.e2e_dir / "helpers.sh"
        self.tool_dir: Path | None = None
        self.update_snapshots = "--update" in sys.argv

    def setup(self) -> bool:
        """Install tool via uv tool install. Returns True if successful."""
        print("Installing shortcake...")
        self.tool_dir = Path(tempfile.mkdtemp())
        env = {**os.environ, "UV_TOOL_DIR": str(self.tool_dir)}
        result = subprocess.run(
            ["uv", "tool", "install", str(self.project_root), "--force"],
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Failed to install shortcake: {result.stderr}")
            return False
        os.environ["PATH"] = f"{self.tool_dir}/bin:{os.environ['PATH']}"
        print(f"Installed to {self.tool_dir}/bin/sc\n")
        return True

    def discover_tests(self) -> list[tuple[Path, Path | None]]:
        """Find all test scripts and their snapshots."""
        tests = []
        for test_dir in sorted(self.e2e_dir.glob("test_*")):
            if not test_dir.is_dir():
                continue
            for test_script in sorted(test_dir.glob("*.sh")):
                snapshot_file = test_script.with_suffix(".json")
                tests.append(
                    (test_script, snapshot_file if snapshot_file.exists() else None)
                )
        return tests

    def run_test(self, test_script: Path) -> tuple[bool, str, str, int]:
        """Run single test script, return (success, stdout, stderr, exit_code)."""
        script = f"""
            set -euo pipefail
            source "{self.helpers}"
            source "{test_script}"
        """
        result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        return (
            result.returncode == 0,
            result.stdout,
            result.stderr,
            result.returncode,
        )

    def compare_snapshot(
        self, snapshot_file: Path, actual_output: str
    ) -> tuple[bool, str]:
        """Compare test output against snapshot. Returns (match, diff_info)."""
        if self.update_snapshots or not snapshot_file.exists():
            snapshot_file.write_text(actual_output)
            return True, "updated" if snapshot_file.exists() else "created"

        expected = snapshot_file.read_text()
        if actual_output.strip() == expected.strip():
            return True, ""

        return False, f"Expected:\n{expected[:200]}\n\nGot:\n{actual_output[:200]}"

    def run_all(self) -> int:
        """Run all tests."""
        if not self.setup():
            return 1

        tests = self.discover_tests()
        if not tests:
            print("No tests found!")
            return 1

        passed = 0
        failed = 0
        current_group = None

        for test_script, _snapshot_file in tests:
            group = test_script.parent.name
            if group != current_group:
                print(f"\n--- {group} ---")
                current_group = group

            test_name = test_script.stem
            success, stdout, stderr, exit_code = self.run_test(test_script)

            if success:
                passed += 1
                print(f"  [ok] {test_name}")
            else:
                failed += 1
                print(f"  [FAIL] {test_name}")
                if stdout:
                    print(f"    stdout: {stdout[:200]}")
                if stderr:
                    print(f"    stderr: {stderr[:200]}")

        print("\n--- Results ---")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")
        return 1 if failed else 0


if __name__ == "__main__":
    runner = TestRunner(Path(__file__).parent.parent)
    sys.exit(runner.run_all())
