"""模型文件完整性：清单一致才加载；被改动时报错；没有清单时照常加载。"""

import pytest

from redactx.integrity import ModelTampered, verify_dir, write_sums


def test_verify_detects_changed_and_missing_files(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"weights")
    (tmp_path / "config.json").write_text("{}")
    write_sums(tmp_path, ["model.onnx", "config.json"])
    verify_dir(tmp_path)  # 一致
    (tmp_path / "model.onnx").write_bytes(b"tampered")
    with pytest.raises(ModelTampered):
        verify_dir(tmp_path)
    (tmp_path / "model.onnx").unlink()
    with pytest.raises(ModelTampered):
        verify_dir(tmp_path)


def test_rejects_paths_outside_dir(tmp_path):
    (tmp_path / "SHA256SUMS").write_text("0" * 64 + "  ../etc/passwd\n")
    with pytest.raises(ModelTampered):
        verify_dir(tmp_path)


def test_without_sums_still_loads(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"weights")
    verify_dir(tmp_path)  # 只记警告
