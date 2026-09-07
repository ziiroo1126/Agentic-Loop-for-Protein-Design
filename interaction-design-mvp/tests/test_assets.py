from __future__ import annotations

import json
from types import SimpleNamespace

from interaction_design.assets import (
    download_assets,
    hf_download_command,
    hf_verify_command,
    load_asset_pins,
    select_asset_pins,
    task_asset_pins,
    verify_assets,
    write_asset_manifest,
)


def test_asset_lock_uses_full_revisions_and_hf_cli(tmp_path):
    pins = load_asset_pins("config/assets.lock.toml")
    assert len(pins) == 8
    for pin in pins:
        command = hf_download_command(pin, tmp_path)
        assert command[:2] == ["hf", "download"]
        assert command[command.index("--revision") + 1] == pin.revision
        assert len(pin.revision) == 40
        assert pin.revision not in {"main", "latest"}
        verify = hf_verify_command(pin, tmp_path)
        assert verify[:3] == ["hf", "cache", "verify"]
        assert verify[verify.index("--revision") + 1] == pin.revision


def test_task_assets_select_only_required_model_and_inverse_fold_weights():
    pins = load_asset_pins("config/assets.lock.toml")
    selected = task_asset_pins(pins, "odesign_base_prot_flex", "protein")
    assert [pin.name for pin in selected] == ["odesign-prot-flex", "oinvfold-protein"]
    assert [pin.include for pin in selected] == [
        ("ckpt/odesign_base_prot_flex.pt",),
        ("oinvfold_protein.ckpt",),
    ]


def test_asset_selection_rejects_unknown_names():
    pins = load_asset_pins("config/assets.lock.toml")
    try:
        select_asset_pins(pins, ["not-a-real-asset"])
    except ValueError as error:
        assert "unknown asset name" in str(error)
    else:
        raise AssertionError("unknown asset name should fail")


def test_download_only_runs_selected_hub_commands(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout='{"verified": true}', stderr="")

    monkeypatch.setattr("interaction_design.assets.subprocess.run", fake_run)
    manifest = download_assets(
        "config/assets.lock.toml",
        tmp_path,
        ["odesign-prot-flex", "oinvfold-protein"],
    )

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert [pin["name"] for pin in payload["pins"]] == [
        "odesign-prot-flex",
        "oinvfold-protein",
    ]
    assert len(commands) == 4
    assert all("--revision" in command for command in commands)


def test_local_asset_manifest_detects_corruption(tmp_path):
    pins = load_asset_pins("config/assets.lock.toml")
    weight = tmp_path / "ckpt" / "tiny.pt"
    weight.parent.mkdir()
    weight.write_bytes(b"known bytes")
    write_asset_manifest(tmp_path, pins)
    assert verify_assets(tmp_path)["ok"] is True

    weight.write_bytes(b"changed")
    verification = verify_assets(tmp_path)
    assert verification["ok"] is False
    assert "checksum mismatch" in verification["failures"][0]


def test_runtime_checkpoint_verification_rejects_wrong_bytes_or_pin(tmp_path):
    import pytest

    from interaction_design.assets import verify_task_checkpoints

    pins = task_asset_pins(
        load_asset_pins("config/assets.lock.toml"), "odesign_base_prot_flex", "protein"
    )
    for pin in pins:
        for filename in pin.include:
            path = tmp_path / pin.local_subdir / filename
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(pin.name.encode())
    manifest = write_asset_manifest(tmp_path, pins)
    result = verify_task_checkpoints(tmp_path / "ckpt", "odesign_base_prot_flex", "protein")
    assert len(result["files"]) == 2
    payload = json.loads(manifest.read_text())
    payload["pins"][0]["revision"] = "0" * 40
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="pin mismatch"):
        verify_task_checkpoints(tmp_path / "ckpt", "odesign_base_prot_flex", "protein")
    write_asset_manifest(tmp_path, pins)
    (tmp_path / "ckpt" / "oinvfold_protein.ckpt").write_bytes(b"wrong weights")
    with pytest.raises(ValueError, match="checksum/size mismatch"):
        verify_task_checkpoints(tmp_path / "ckpt", "odesign_base_prot_flex", "protein")
