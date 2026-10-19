"""只读路径不许在数据根里用 `_ensure_dirs()` 长骨架目录；派生缓存写另计（B10.2 / chore 卡）。

来历是实测，不是推测：`storage.list_config_files()` 原先每次调用都先跑一遍
`_ensure_dirs()`，于是 `GET /api/v1/p/{pid}/files` 这条**只读**请求链会在真实
`data/` 里长出 `archive/`、`records/`、`cache/`、`cache/parse_tree/` 四个目录。
权威文件的哈希逐条未变，所以旧版 H3 守卫（只哈希常规文件）对此完全沉默——
「只读接口没改数据目录」这句话当时在机制上无法验证。

本文件把两半都钉住：

1. 走**真实 HTTP 面**（与 `tests/test_api_documents.py` 同一套路：线程里以随机端口
   起 `MCServer`，`http.client` 发请求）读一遍，数据根的**条目集合与字节**必须一模
   一样，目录也算条目。判据直接复用守卫的 `snapshot_data_tree` + `compare_snapshots`
   （+ `format_diff_report` 当失败文案），不再另写一份会跟守卫漂移的遍历。
2. 存储层读函数在目录缺失时返回**空结果**，而不是把树长出来。
3. 创建确实还在发生，只是明确落在**写路径与启动流程**上：全新数据根第一次
   `save_config_file` / `save_record_file` 之后目录一定在——这是防「读不再建目录，
   顺手把写也弄坏了」的那一半。

断言范围之外的豁免（2026-10-18 按端点实测：`run.py --data-dir` 起真服务打一次性 `/tmp`
根，17 条 GET 路由一条一棵 pristine 树、`cache/` 不预建，前后差交给守卫自己的
`snapshot_data_tree`）——**4 条 GET 确实会在数据根里创建条目**，机制都不是 `_ensure_dirs()`，
而是派生缓存写走的 `fsutil.atomic_write_bytes()`（`app/core/fsutil.py` 里它给自己的目标目录
`makedirs`）：

* `GET /files/{name}/document`、`GET /search?q=…` 经 `app/services/documents.py::_disk_store`
  建 `cache/` + `cache/doc_v2/` 并写缓存文件；
* `GET /files/{name}/archives`、`GET /files/{name}/records` 经 `app/services/history.py::_cache_write`
  建 `cache/` + `cache/diff_v2/` 并写缓存文件。

这 4 条同一轮里权威字节逐条哈希不变，「读一次就把派生缓存建起来」是设计如此（规范 §5.1
把这两个缓存目录标成可丢弃的派生数据，§6.5 第 3 步与 §6.6 就写着未命中要计算并落盘），
收不收是另一张卡的裁定，不归 B10.2 这张卡管。所以本文件断的是**它自己真正打到的那些读
接口**（`GET /files`、`GET /raw`）不改变数据根的目录集合与既有文件的字节；别把断言范围
顺手扩到上面 4 条上——那会立刻红在设计内行为上。守卫一侧不跟着豁免：H3 的目录集合快照对
派生与权威一视同仁，这 4 条在真实 `data/` 上跑一轮就会以「新建目录 / 新增文件」报红，
看得见它们正是本卡要的执法效果。

已知的残留（**不在本卡范围**，别把本文件误读成「全仓库读路径都不建目录」）：
`storage.get_record_dir()` 与 `get_archive_dir()` 仍带 `makedirs`，而读它们的
`list_record_versions` / `list_archived_versions` / `load_archived_file` 只挂在旧
NiceGUI 页面链路（`app/pages/**`，M4-07 要删的那批）上。`app/server/**` 的读接口
一律绕开了这两个创建型 helper——`app/services/history.py` 与
`app/services/documents.py` 里各带一句「读列表不创建目录」的注释加 `isdir` 判据
（两处原话措辞略不同：`history.py:83` 与 `documents.py::_count_files`），那是本仓库早就
认下的口径，本文件只是把它第一次变成可判红的证据。
同一条创建在**写**请求上仍然会发生（实测、非本文件断言对象）：`DELETE /api/v1/p/{pid}/files/{name}`
会经 `recovery._invalidate()` → `parse_cache` → `storage._ensure_dirs()`（`app/core/parse_cache.py:15`）
把缺失的骨架目录重新长出来——一棵只剩 `configs/` 的根上补出了 `archive/`、`records/`、`cache/`、
`cache/parse_tree/`。
"""

from __future__ import annotations

import http.client
import json
import os
import shutil
import threading

import pytest

import data_guard
from app.config import Settings
from app.services import documents

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "web")

XML_A = b'<?xml version="1.0"?><R a="1"><item>x</item></R>'
XML_B = b'<?xml version="1.0"?><R a="2"><item>y</item></R>'


def _write(path: str, content: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def _snapshot(root: str) -> dict:
    return data_guard.snapshot_data_tree(root)


def _assert_unchanged(root: str, before: dict) -> dict:
    """读请求跑完之后：数据根的**条目集合 + 每个条目的字节**必须与读之前一致。

    失败消息直接用守卫的报告——同一份文案在 H3 执法时也印在 pytest 末尾，
    两处读到的是同一个形状，不必再翻译一遍。
    """
    after = _snapshot(root)
    diffs = data_guard.compare_snapshots(before, after)
    assert not data_guard.has_differences(diffs), data_guard.format_diff_report(diffs)
    return after


@pytest.fixture()
def pristine_root(tmp_path):
    """一个「放了一个配置文件、但业务子目录一个都没有」的数据根。

    刻意**不像** `tests/conftest.py::isolated_data_root` 那样先把 `archive/`、
    `records/`、`cache/` 建好：那些目录只要预先存在，读路径再把它们 `makedirs`
    一遍就是零差异，B10.2 那条缺陷恰好抓不到（这是本文件最容易写假的地方）。
    """
    root = str(tmp_path / "pristine" / "data")
    _write(os.path.join(root, "profiles", "default", "configs", "a.xml"), XML_A)
    return root


@pytest.fixture()
def at_pristine_root(pristine_root):
    """把存储层数据根切到 `pristine_root`——走生产的 `--data-dir` 同一条路。

    `configure_data_dir` 与 `tests/conftest.py` 的隔离夹具用的是同一套「按前缀整体
    改写模块常量」的做法，所以 `device_models._models_file()` 这类运行期拼出来的
    路径也会跟着走。`isolated_data_root` 的 monkeypatch 在本用例结束时照常把
    原值装回去，这里再显式切一次只是不让「恢复」依赖夹具拆序。
    """
    from app.core import storage

    previous = storage.DATA_DIR
    storage.configure_data_dir(pristine_root)
    try:
        yield pristine_root
    finally:
        storage.configure_data_dir(previous)


@pytest.fixture()
def server(at_pristine_root):
    """线程里以随机端口起真实 `MCServer`（照 `tests/test_api_documents.py` 的套路）。"""
    from app.server.http import MCServer
    from app.server.router import build_router

    documents.reset_cache()
    settings = Settings(
        host="127.0.0.1", port=0, data_dir=at_pristine_root, web_dir=WEB_DIR
    )
    srv = MCServer(settings, build_router(settings))
    thread = threading.Thread(
        target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def get(server, path):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        conn.request("GET", path, headers={})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1) 真实 HTTP 面：只读请求序列之后，数据根逐条目、逐字节一模一样
# ---------------------------------------------------------------------------


def test_get_files_sequence_leaves_data_root_entry_and_byte_identical(
    server, at_pristine_root
) -> None:
    """`GET /files` 连读两次 + `GET /raw` 一次：条目集合与字节都不许变。

    这条就是 B10.2 的正身：修复前它在 `data/` 里长出四个目录，而**一个文件都没多**，
    所以旧的「只哈希常规文件」的快照看不出任何差异。
    """
    root = at_pristine_root
    before = _snapshot(root)
    assert before, "种子树必须有可比对的条目，否则这条证明是空的"

    for _round in range(2):
        status, _headers, body = get(server, "/api/v1/p/default/files")
        assert status == 200, body
        names = [item["name"] for item in json.loads(body)["items"]]
        assert names == ["a.xml"], body

    status, _headers, raw = get(server, "/api/v1/p/default/files/a.xml/raw")
    assert status == 200, raw
    assert raw == XML_A

    after = _assert_unchanged(root, before)
    # 明确说清点到了哪些条目：目录必须在集合里，且不比读之前多。
    assert set(after) == {
        "profiles/",
        "profiles/default/",
        "profiles/default/configs/",
        "profiles/default/configs/a.xml",
    }, sorted(after)


def test_get_files_when_the_profile_has_no_directories_creates_nothing(
    server, at_pristine_root
) -> None:
    """机型目录一个都没有：`GET /files` 必须回空列表，而不是先给它把树建出来。

    这就是「刚注册的机型还没写过」的现网形状（`device_models.add_model()` 不建目录，
    只有 `copy_profile_data` 才建）。用 `default` 走同一条形状是为了不掺进机型注册表
    ——`tests/conftest.py::isolated_data_root` 把 `device_models._models_file` 单独钉在
    它自己的临时根上，与本用例的 pristine 根不是一个地方，读起来容易误判成「守卫没生效」。
    """
    from app.core import storage

    root = at_pristine_root
    shutil.rmtree(os.path.join(root, "profiles", "default"))
    assert not os.path.exists(storage.get_configs_dir())
    before = _snapshot(root)

    status, _headers, body = get(server, "/api/v1/p/default/files")
    assert status == 200, body
    assert json.loads(body)["items"] == [], body

    after = _assert_unchanged(root, before)
    assert not os.path.exists(os.path.join(root, "profiles", "default")), "读一次就长出机型目录"
    assert after == before


# ---------------------------------------------------------------------------
# 2) 存储层读函数：目录缺失 = 空结果，且不留痕迹
# ---------------------------------------------------------------------------


def test_list_config_files_on_missing_dir_returns_empty_and_creates_nothing(at_pristine_root) -> None:
    from app.core import storage

    configs_dir = storage.get_configs_dir()
    shutil.rmtree(configs_dir)
    before = _snapshot(at_pristine_root)

    assert storage.list_config_files() == []

    _assert_unchanged(at_pristine_root, before)
    assert not os.path.exists(configs_dir), "读一次就把 configs/ 长回来了"


def test_list_config_files_lists_exactly_what_it_listed_before(at_pristine_root) -> None:
    """目录齐活的既有机型：读结果与修复前逐字一致（只有常规文件、按名排序）。

    这条钉的是「改成只读不许顺带改变语义」：期望值**硬写**，不从 `os.listdir` 现算，
    否则实现漂移时期望跟着漂。子目录 `nested/` 不进列表（`isfile` 过滤器的既有行为），
    空文件进列表，排序是名字的字典序。
    """
    from app.core import storage

    configs_dir = storage.get_configs_dir()
    _write(os.path.join(configs_dir, "b.xml"), XML_B)
    _write(os.path.join(configs_dir, "a.json"), b"{}")
    _write(os.path.join(configs_dir, "empty.txt"), b"")
    _write(os.path.join(configs_dir, "nested", "c.xml"), XML_A)
    before = _snapshot(at_pristine_root)

    # 种子自带的 a.xml 也在里面：期望值是**手写的**全集，不是「新加的那几个」。
    expected = ["a.json", "a.xml", "b.xml", "empty.txt"]
    assert storage.list_config_files() == expected
    # 连读两次结果一样：第二次没有「把上一次没建的目录补上」这种副作用。
    assert storage.list_config_files() == expected

    _assert_unchanged(at_pristine_root, before)


def test_other_config_reads_do_not_create_the_configs_dir(at_pristine_root) -> None:
    """同一台机型上的其它读接口也不许把 `configs/` 长回来（防「换个入口照样建」）。

    这几个读函数本来就不调 `_ensure_dirs()`，这里钉的是**它们一直没建**这件事：
    一旦有人图省事在 `_load_json` / `get_config_path` 那种公共入口上补一次
    「ensure」，本条立刻红，而不必等下一次基准测量才发现。
    """
    from app.core import storage

    configs_dir = storage.get_configs_dir()
    shutil.rmtree(configs_dir)
    before = _snapshot(at_pristine_root)

    assert storage.load_config_file("a.xml") is None
    assert storage.get_file_update_date("a.xml") is None
    assert storage.load_config_mapping() == []
    assert storage.get_configs_dir() == configs_dir

    _assert_unchanged(at_pristine_root, before)
    assert not os.path.exists(configs_dir)


def test_legacy_flat_layout_profile_still_lists_and_creates_nothing(at_pristine_root) -> None:
    """旧「平铺」布局（只有 `data/configs/`，没有 `profiles/default/`）读出来一个字节不差。

    `get_configs_dir()` 对 default 机型有 legacy 回退：目标目录不存在而 `data/configs`
    存在时用后者。真实部署里就有这种根（`ensure_profile_layout(migrate_legacy=True)`
    才把它搬走），所以这条同时钉两件事——列表内容不变，且不因为读一次就长出
    `profiles/` 那一套新树。
    """
    from app.core import storage

    root = at_pristine_root
    shutil.rmtree(os.path.join(root, "profiles"))
    _write(os.path.join(root, "configs", "a.xml"), XML_A)
    _write(os.path.join(root, "configs", "b.xml"), XML_B)
    assert storage.get_configs_dir() == os.path.join(root, "configs")

    before = _snapshot(root)
    assert storage.list_config_files() == ["a.xml", "b.xml"]
    after = _assert_unchanged(root, before)
    assert not os.path.exists(os.path.join(root, "profiles")), "读一次 legacy 根就长出机型树"
    assert after == before


# ---------------------------------------------------------------------------
# 3) 创建没有消失，只是挪到了「启动流程 / 第一次需要它的写」
# ---------------------------------------------------------------------------


def test_first_write_still_materializes_the_business_directories(at_pristine_root) -> None:
    """全新数据根上第一次写必须把目录建出来——读路径不建，不等于没人建。"""
    from app.core import storage

    root = at_pristine_root
    shutil.rmtree(storage.get_configs_dir())
    shutil.rmtree(os.path.join(root, "profiles", "default"))
    assert not os.path.exists(os.path.join(root, "profiles", "default", "configs"))

    path = storage.save_config_file("a.xml", XML_A)
    assert os.path.isfile(path)
    assert storage.list_config_files() == ["a.xml"]

    record_path = storage.save_record_file("a.xml", b"record")
    assert os.path.isfile(record_path)

    for directory in ("configs", "archive", "records", "cache"):
        assert os.path.isdir(
            os.path.join(root, "profiles", "default", directory)
        ), directory
    assert os.path.isdir(os.path.join(root, "profiles", "default", "cache", "parse_tree"))


def test_startup_layout_creates_the_profile_root(at_pristine_root) -> None:
    """`run.py` 启动时调的 `ensure_profile_layout()` 依旧建机型根（口径没被动过）。

    它只建 `profiles/` 与 `profiles/default/`，业务子目录留给第一次写——这正是本卡
    选的落点：启动流程建「机型存在」，写路径建「我要往这里写」。
    """
    from app.core import storage

    root = at_pristine_root
    shutil.rmtree(os.path.join(root, "profiles", "default"))

    storage.ensure_profile_layout(migrate_legacy=False)

    assert os.path.isdir(os.path.join(root, "profiles"))
    assert os.path.isdir(os.path.join(root, "profiles", "default"))
