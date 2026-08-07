from typer.testing import CliRunner

from shortcake.cli import app

runner = CliRunner()


def test_skill_prints_stacked_prs() -> None:
    """Test the stacked-PRs workflow skill is bundled and printable."""
    result = runner.invoke(app, ["skill", "--print", "shortcake-stacked-prs"])

    assert result.exit_code == 0
    assert "Shortcake-Parent" in result.output
    assert "sc ls --json" in result.output
    assert "sc submit --dry-run" in result.output
    assert "sc adopt X -p <parent>" in result.output
    assert "sc continue" in result.output
    assert "needs restack" in result.output
    assert "native GitHub stack" in result.output
    assert "sc co <pr-number>" in result.output
    assert "run `sc pull`" in result.output
    assert "sc pull --stack" not in result.output


def test_skill_list_includes_all_bundled_skills() -> None:
    """Test the no-arg listing names every bundled skill."""
    result = runner.invoke(app, ["skill"])

    assert result.exit_code == 0
    assert "shortcake-stacked-prs" in result.output
    assert "shortcake-visual-recap" in result.output
