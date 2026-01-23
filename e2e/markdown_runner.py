#!/usr/bin/env python3
"""
Markdown-based E2E test runner for Shortcake.

Parses markdown files, extracts console code blocks, runs commands,
and verifies output matches. Can update markdown files with actual output.

Usage:
    python e2e/markdown_runner.py                    # Run all tests
    python e2e/markdown_runner.py --update           # Update snapshots
    python e2e/markdown_runner.py docs/create.md     # Run specific file
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CodeBlock:
    """A console code block from markdown."""

    start_line: int
    end_line: int
    commands: list[tuple[str, str]]  # (command, expected_output)
    raw_content: str


@dataclass
class TestEnv:
    """Test environment with repo and optional remote."""

    repo_dir: Path
    remote_dir: Path | None = None


@dataclass
class TestResult:
    """Result of running a code block."""

    passed: bool
    command: str
    expected: str
    actual: str
    line: int


def parse_markdown(content: str) -> list[CodeBlock]:
    """Extract console code blocks from markdown."""
    blocks = []
    lines = content.split("\n")
    i = 0

    while i < len(lines):
        # Look for ```console or ```bash or ```shell
        if re.match(r"^```(console|bash|shell|sh)\s*$", lines[i]):
            start_line = i
            block_lines = []
            i += 1

            # Collect until closing ```
            while i < len(lines) and lines[i] != "```":
                block_lines.append(lines[i])
                i += 1

            end_line = i
            raw_content = "\n".join(block_lines)

            # Parse commands and expected output
            commands = parse_console_block(block_lines)
            if commands:
                blocks.append(
                    CodeBlock(
                        start_line=start_line,
                        end_line=end_line,
                        commands=commands,
                        raw_content=raw_content,
                    )
                )
        i += 1

    return blocks


def parse_console_block(lines: list[str]) -> list[tuple[str, str]]:
    """Parse a console block into (command, expected_output) pairs."""
    commands = []
    current_cmd = None
    current_output_lines = []

    for line in lines:
        if line.startswith("$ "):
            # Save previous command if exists
            if current_cmd is not None:
                commands.append((current_cmd, "\n".join(current_output_lines)))

            current_cmd = line[2:]  # Remove "$ "
            current_output_lines = []
        elif current_cmd is not None:
            # This is output from the current command
            current_output_lines.append(line)

    # Don't forget the last command
    if current_cmd is not None:
        commands.append((current_cmd, "\n".join(current_output_lines)))

    return commands


def setup_test_repo() -> TestEnv:
    """Create a fresh git repo for testing."""
    repo_dir = Path(tempfile.mkdtemp(prefix="shortcake_test_"))

    # Initialize repo
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )

    # Create initial commit
    readme = repo_dir / "README.md"
    readme.write_text("# Test Project\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )

    return TestEnv(repo_dir=repo_dir)


def setup_remote(env: TestEnv) -> None:
    """Create a bare remote and push main to it."""
    remote_dir = Path(tempfile.mkdtemp(prefix="shortcake_remote_"))

    # Create bare repo
    subprocess.run(
        ["git", "init", "--bare"],
        cwd=remote_dir,
        capture_output=True,
        check=True,
    )

    # Add remote to local repo
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote_dir)],
        cwd=env.repo_dir,
        capture_output=True,
        check=True,
    )

    # Push main
    subprocess.run(
        ["git", "push", "-u", "origin", "main"],
        cwd=env.repo_dir,
        capture_output=True,
        check=True,
    )

    env.remote_dir = remote_dir


def update_remote_main(env: TestEnv) -> None:
    """Simulate remote main advancing by committing directly to remote.

    Creates a temp clone of the remote, commits, and pushes.
    """
    if env.remote_dir is None:
        return

    temp_clone = Path(tempfile.mkdtemp(prefix="shortcake_clone_"))
    try:
        subprocess.run(
            ["git", "clone", str(env.remote_dir), "."],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "remote@example.com"],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Remote User"],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
        # Create a commit on main
        (temp_clone / "remote_change.txt").write_text("Remote change\n")
        subprocess.run(
            ["git", "add", "remote_change.txt"],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "Remote commit on main"],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
    finally:
        shutil.rmtree(temp_clone, ignore_errors=True)


def force_push_to_remote(env: TestEnv, branch: str) -> None:
    """Simulate someone force-pushing a branch with different commits.

    Creates a temp clone of the remote, makes a different commit on the branch,
    and force pushes.
    """
    if env.remote_dir is None:
        return

    temp_clone = Path(tempfile.mkdtemp(prefix="shortcake_clone_"))
    try:
        subprocess.run(
            ["git", "clone", str(env.remote_dir), "."],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "remote@example.com"],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Remote User"],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
        # Checkout the branch
        subprocess.run(
            ["git", "checkout", branch],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
        # Amend the last commit to create divergence
        subprocess.run(
            ["git", "commit", "--amend", "-m", "Force-pushed commit"],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
        # Force push
        subprocess.run(
            ["git", "push", "--force", "origin", branch],
            cwd=temp_clone,
            capture_output=True,
            check=True,
        )
    finally:
        shutil.rmtree(temp_clone, ignore_errors=True)


def run_command(cmd: str, env: TestEnv) -> str:
    """Run a shell command and return output.

    Handles special meta-commands:
    - # reset: No-op placeholder
    - # setup: with-remote: Create bare remote and push main
    - # remote: update-main: Simulate remote main advancing
    - # remote: force-push <branch>: Simulate force-push to branch
    """
    cmd_stripped = cmd.strip()

    # Handle special meta-commands
    if cmd_stripped == "# reset":
        return ""

    if cmd_stripped == "# setup: with-remote":
        setup_remote(env)
        return ""

    if cmd_stripped == "# remote: update-main":
        update_remote_main(env)
        return ""

    if cmd_stripped.startswith("# remote: force-push "):
        branch = cmd_stripped.replace("# remote: force-push ", "").strip()
        force_push_to_remote(env, branch)
        return ""

    result = subprocess.run(
        cmd,
        shell=True,
        cwd=env.repo_dir,
        capture_output=True,
        text=True,
        env={**os.environ, "NO_COLOR": "1", "TERM": "dumb"},
    )

    output = result.stdout
    if result.stderr and result.returncode != 0:
        output = result.stderr if not output else output + result.stderr

    return output.rstrip("\n")


def normalize_output(output: str) -> str:
    """Normalize output for comparison.

    - Strip trailing whitespace per line
    - Replace commit hashes (7+ hex chars) with <HASH>
    - Replace timestamps and other variable content
    """
    lines = output.split("\n")
    normalized = []
    for line in lines:
        line = line.rstrip()
        # Replace commit hashes in common formats:
        # [branch abc1234] message
        # abc1234 message (git log --oneline)
        line = re.sub(r"\[(\S+)\s+[a-f0-9]{7,}\]", r"[\1 <HASH>]", line)
        line = re.sub(r"^[a-f0-9]{7,}\s+", "<HASH> ", line)
        normalized.append(line)
    return "\n".join(normalized)


def run_code_block(block: CodeBlock, env: TestEnv) -> list[TestResult]:
    """Run all commands in a code block, return results."""
    results = []

    for cmd, expected in block.commands:
        actual = run_command(cmd, env)

        # Normalize both for comparison
        expected_norm = normalize_output(expected)
        actual_norm = normalize_output(actual)

        passed = expected_norm == actual_norm

        results.append(
            TestResult(
                passed=passed,
                command=cmd,
                expected=expected,
                actual=actual,
                line=block.start_line,
            )
        )

    return results


def update_markdown(
    content: str, blocks: list[CodeBlock], all_results: list[list[TestResult]]
) -> str:
    """Update markdown content with actual command outputs."""
    lines = content.split("\n")

    # Process blocks in reverse order so line numbers stay valid
    for block, results in reversed(list(zip(blocks, all_results, strict=False))):
        # Rebuild the code block with actual output
        new_block_lines = []
        for result in results:
            new_block_lines.append(f"$ {result.command}")
            if result.actual:
                new_block_lines.extend(result.actual.split("\n"))

        # Replace the old block content
        lines[block.start_line + 1 : block.end_line] = new_block_lines

    return "\n".join(lines)


def run_markdown_file(
    filepath: Path, update: bool = False, verbose: bool = False
) -> tuple[int, int]:
    """Run tests in a markdown file. Returns (passed, failed) counts."""
    content = filepath.read_text()
    blocks = parse_markdown(content)

    if not blocks:
        if verbose:
            print(f"  No console blocks found in {filepath}")
        return 0, 0

    # Each file gets a fresh repo
    env = setup_test_repo()

    try:
        passed = 0
        failed = 0
        all_results = []

        for block in blocks:
            results = run_code_block(block, env)
            all_results.append(results)

            for result in results:
                if result.passed:
                    passed += 1
                    if verbose:
                        print(f"  \033[32m✓\033[0m {result.command}")
                else:
                    failed += 1
                    if not update:
                        print(f"  \033[31m✗\033[0m {result.command}")
                        print(f"    Expected:\n{indent(result.expected, 6)}")
                        print(f"    Actual:\n{indent(result.actual, 6)}")

        if update and failed > 0:
            new_content = update_markdown(content, blocks, all_results)
            filepath.write_text(new_content)
            print(f"  \033[33mUpdated\033[0m {filepath}")

        return passed, failed

    finally:
        shutil.rmtree(env.repo_dir, ignore_errors=True)
        if env.remote_dir is not None:
            shutil.rmtree(env.remote_dir, ignore_errors=True)


def indent(text: str, spaces: int) -> str:
    """Indent text by given spaces."""
    prefix = " " * spaces
    return "\n".join(prefix + line for line in text.split("\n"))


def find_markdown_files(directory: Path) -> list[Path]:
    """Find all markdown files in directory."""
    return sorted(directory.glob("**/*.md"))


def main():
    parser = argparse.ArgumentParser(description="Run markdown-based E2E tests")
    parser.add_argument("files", nargs="*", help="Specific files to test")
    parser.add_argument("--update", "-u", action="store_true", help="Update snapshots")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    args = parser.parse_args()

    # Find test files
    e2e_dir = Path(__file__).parent
    docs_dir = e2e_dir / "docs"

    if args.files:
        files = [Path(f) for f in args.files]
    elif docs_dir.exists():
        files = find_markdown_files(docs_dir)
    else:
        print(f"No docs directory found at {docs_dir}")
        sys.exit(1)

    if not files:
        print("No markdown files found")
        sys.exit(1)

    # Run tests
    total_passed = 0
    total_failed = 0

    for filepath in files:
        print(f"\n\033[1m{filepath}\033[0m")
        passed, failed = run_markdown_file(
            filepath, update=args.update, verbose=args.verbose
        )
        total_passed += passed
        total_failed += failed

    # Summary
    print(f"\n{'=' * 40}")
    if total_failed == 0:
        print(f"\033[32m{total_passed} passed\033[0m")
        sys.exit(0)
    else:
        print(
            f"\033[32m{total_passed} passed\033[0m, "
            f"\033[31m{total_failed} failed\033[0m"
        )
        if not args.update:
            print("\nRun with --update to update snapshots")
        sys.exit(1)


if __name__ == "__main__":
    main()
