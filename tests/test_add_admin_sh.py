"""手动添加管理员脚本。不需要 root：用 MCHECKER_ADD_ADMIN_SKIP_ROOT=1。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def env_for(tmp_path: Path, **extra: str) -> dict:
    path = os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"])
    base = {
        "PATH": path,
        "HOME": str(tmp_path / "home"),
        "MCHECKER_PYTHON": sys.executable,
        "MCHECKER_ADD_ADMIN_SKIP_ROOT": "1",
    }
    base.update(extra)
    return base


def install(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name in ("add-admin.sh", "start.sh"):
        target = root / name
        target.write_text((REPO_ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
        target.chmod(0o755)
    app = root / "app"
    # 校验和写入走仓库里的 app，发布目录只需能找到脚本；PYTHONPATH 由脚本设成脚本目录。
    # 测试把脚本放到 tmp，所以把 app 指到仓库会失败。复制不现实。改为在仓库根运行，
    # 数据根指到 tmp。这里的 root 只在「从别的目录调用」时使用。
    del app


def run(args: list, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["/bin/sh", str(REPO_ROOT / "add-admin.sh")] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        env=env,
        cwd=str(cwd),
        timeout=30,
    )


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_refuses_without_root(tmp_path: Path) -> None:
    proc = run(
        ["--name", "张三", "--data-dir", str(tmp_path / "data")],
        env_for(tmp_path, MCHECKER_ADD_ADMIN_SKIP_ROOT="0"),
        tmp_path,
    )
    assert proc.returncode == 3
    assert "sudo" in proc.stderr
    assert not (tmp_path / "data" / "admin_users.json").exists()


def test_adds_admin_and_ip(tmp_path: Path) -> None:
    data = tmp_path / "data"
    proc = run(
        ["--name", "张三", "--ip", "10.1.2.3", "--data-dir", str(data)],
        env_for(tmp_path),
        tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "已添加管理员：张三（10.1.2.3）" in proc.stdout
    admins = read_json(data / "admin_users.json")
    mapping = read_json(data / "ip_mapping.json")
    assert [row["name"] for row in admins] == ["张三"]
    assert mapping[0]["ip"] == "10.1.2.3"
    assert mapping[0]["name"] == "张三"


def test_existing_admin_still_binds_ip(tmp_path: Path) -> None:
    data = tmp_path / "data"
    env = env_for(tmp_path)
    first = run(["--name", "张三", "--data-dir", str(data)], env, tmp_path)
    assert first.returncode == 0, first.stderr
    assert "未绑定 IP" in first.stdout
    second = run(
        ["--name", "张三", "--ip", "10.1.2.3", "--data-dir", str(data)],
        env,
        tmp_path,
    )
    assert second.returncode == 0, second.stderr
    assert "已在名单中" in second.stdout
    assert len(read_json(data / "admin_users.json")) == 1
    assert read_json(data / "ip_mapping.json")[0]["ip"] == "10.1.2.3"


def test_rejects_name_that_cannot_be_deleted(tmp_path: Path) -> None:
    data = tmp_path / "data"
    proc = run(
        ["--name", "张/三", "--ip", "10.1.2.3", "--data-dir", str(data)],
        env_for(tmp_path),
        tmp_path,
    )
    assert proc.returncode == 1
    assert "管理员姓名" in proc.stderr
    assert not (data / "admin_users.json").exists()


def test_rejects_bad_ip_without_touching_admin_list(tmp_path: Path) -> None:
    data = tmp_path / "data"
    env = env_for(tmp_path)
    ok = run(["--name", "张三", "--data-dir", str(data)], env, tmp_path)
    assert ok.returncode == 0, ok.stderr
    before = (data / "admin_users.json").read_bytes()
    proc = run(
        ["--name", "李四", "--ip", "999.1.1.1", "--data-dir", str(data)],
        env,
        tmp_path,
    )
    assert proc.returncode == 1
    assert "IP" in proc.stderr or "地址" in proc.stderr
    assert (data / "admin_users.json").read_bytes() == before
    assert not (data / "ip_mapping.json").exists()


def test_uses_service_state_data_dir(tmp_path: Path) -> None:
    data = tmp_path / "svc data"
    (REPO_ROOT / ".mcchecker.service").write_text("data_dir=%s\n" % data, encoding="utf-8")
    try:
        proc = run(["--name", "张三", "--ip", "10.9.9.9"], env_for(tmp_path), Path("/"))
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert (data / "admin_users.json").is_file()
    finally:
        state = REPO_ROOT / ".mcchecker.service"
        if state.exists():
            state.unlink()
