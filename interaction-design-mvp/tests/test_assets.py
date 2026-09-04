from __future__ import annotations

from interaction_design.assets import (
    hf_download_command,
    hf_verify_command,
    load_asset_pins,
    verify_assets,
    write_asset_manifest,
)


def test_asset_lock_uses_full_revisions_and_hf_cli(tmp_path):
    pins = load_asset_pins("config/assets.lock.toml")
    assert len(pins) == 2
    for pin in pins:
        command = hf_download_command(pin, tmp_path)
        assert command[:2] == ["hf", "download"]
        assert command[command.index("--revision") + 1] == pin.revision
        assert len(pin.revision) == 40
        assert pin.revision not in {"main", "latest"}
        verify = hf_verify_command(pin, tmp_path)
        assert verify[:3] == ["hf", "cache", "verify"]
        assert verify[verify.index("--revision") + 1] == pin.revision


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
