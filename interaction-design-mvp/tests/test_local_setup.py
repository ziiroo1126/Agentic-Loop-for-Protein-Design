"""Local installation preserves existing skills and works from another project."""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "plugins/molclaw/tools/local.py"
spec = importlib.util.spec_from_file_location("molclaw_local_setup", SCRIPT)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.mark.parametrize(
    "host,folder", [("codex", ".agents"), ("claude", ".claude"), ("deepseek", ".dsh")]
)
def test_install_links_complete_skill_and_is_idempotent(tmp_path, host, folder):
    result = setup.install(ROOT, tmp_path, host)
    path = tmp_path / folder / "skills/molclaw"
    assert result["status"] == "installed"
    assert path.is_symlink()
    assert (path / "references/adaptive-evaluation.md").is_file()
    assert (path / "scripts/molclaw.py").is_file()
    assert setup.install(ROOT, tmp_path, host)["status"] == "already_installed"


@pytest.mark.parametrize("kind", ["directory", "file", "foreign_link", "broken_link"])
def test_install_never_replaces_existing_skill(tmp_path, kind):
    path = tmp_path / ".agents/skills/molclaw"
    path.parent.mkdir(parents=True)
    if kind == "directory":
        path.mkdir()
        (path / "SKILL.md").write_text("existing")
    elif kind == "file":
        path.write_text("existing")
    else:
        other = tmp_path / "another-skill"
        if kind == "foreign_link":
            other.mkdir()
        path.symlink_to(other)
    with pytest.raises(ValueError, match="refusing to replace"):
        setup.install(ROOT, tmp_path, "codex")
    assert setup.link_status(setup.source_skill(ROOT), path) == "conflict"


def test_doctor_uses_actual_launcher_from_different_working_directory(tmp_path):
    cli = ROOT / "interaction-design-mvp/.venv/bin/interaction-design"
    if not cli.is_file():
        pytest.skip("existing development CLI required for local integration")
    setup.install(ROOT, tmp_path, "codex")
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    result = setup.doctor(ROOT, tmp_path, "codex", cli=cli)
    assert result["status"] == "passed"
    assert result["replay_cli_available"]
    assert result["checks"]["resources"]["status"] == "not_checked"
    assert result["checks"]["host_discovery"]["status"] == "not_verified_in_host"
    assert not result["live_inference_verified"]
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before
    missing = setup.doctor(ROOT, tmp_path, "codex", cli=tmp_path / "missing")
    assert missing["status"] == "incomplete"
    assert not missing["replay_cli_available"]


def test_missing_skill_and_resources_are_distinct(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "_probe", lambda *args: {"status": "passed"})
    result = setup.doctor(ROOT, tmp_path, "codex")
    assert result["replay_cli_available"]
    assert result["checks"]["skill_path"]["status"] == "missing"
    assert result["status"] == "incomplete"
    with pytest.raises(ValueError, match="both"):
        setup.doctor(ROOT, tmp_path, "codex", task=tmp_path / "task.json")


def test_doctor_resource_failure_does_not_disable_replay(tmp_path, monkeypatch):
    setup.install(ROOT, tmp_path, "codex")

    def probe(command, cwd, timeout):
        return {"status": "failed" if "preflight" in command else "passed"}

    monkeypatch.setattr(setup, "_probe", probe)
    result = setup.doctor(
        ROOT, tmp_path, "codex", task=tmp_path / "task", runtime=tmp_path / "runtime"
    )
    assert result["status"] == "incomplete"
    assert result["replay_cli_available"]
    assert result["checks"]["resources"]["status"] == "failed"
    json.dumps(result)
