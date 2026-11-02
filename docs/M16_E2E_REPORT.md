# M16 更新管理真实浏览器 E2E 记录（M16-40）

> 状态：2026-11-02。**桌面 admin 五 tab、成员只读、跨工作区、375/768 窄屏已实测；原 V3 F01–F28 只抽样，键盘焦点/未保存 409 等项未完整执行，因此 M16-40 仍为 In Progress，不是 PASS。**
> 环境：macOS + Google Chrome 156 headless（CDP `Emulation.setDeviceMetricsOverride`），临时数据根 `/tmp/mcc-e2e-40`，Python 3.9.11 主应用。未使用公司内网、未接触真实 SDA。

## 1. 测试数据与身份

- 临时工作区 W1 `w-c61b6bab`（源 `A.xml`、SDA 连接），W2 `w-e996b504`（同名 `A.xml`、不同 `src-b83c69d54cf05a18` 与 `/synthetic/W2-A.xml`）。
- 身份通过临时 `ip_mapping`/`admins` 切换：`127.0.0.1 → E2E管理员`（admin）与 `127.0.0.1 → 只读成员`（member）。全部只写 `/tmp/mcc-e2e-40`。

## 2. 桌面 admin（1280×900）

| 页面 | 结果 | 截图 |
|---|---|---|
| 更新源 | 1 条真实来源，搜索/状态/排序/刷新、编辑/更新按钮可见；无 console error | `docs/evidence/m16-40/updates-sources-1280-admin.png` |
| 更新计划 | 工作区 W1、默认 6h、profile override、全量预览、手动全量；无 `.form-error` | `docs/evidence/m16-40/updates-plan-1280-admin.png` |
| 连接设置 | SDA 连接安全摘要、保活/自动重登信息；凭据/运维入口按能力显示 | `docs/evidence/m16-40/updates-connections-1280-admin.png` |
| 运行记录 | 空态“还没有同步运行记录”，无报错 | `docs/evidence/m16-40/updates-runs-1280-admin.png` |
| 更新候选 | 空态“还没有更新候选”，修复空列表自循环后只发一次 GET | `docs/evidence/m16-40/updates-candidates-1280-admin.png` |

原 V3 导航 `速览/全局搜索/配置管理/设置` 标题可达；本次只做路由抽样，没有逐条执行 F01–F28 的完整交互。

## 3. 成员只读与跨工作区

- 同一 W1 切换为 member 后：更新源仍显示行与状态，但 **编辑/更新按钮数量为 0**，行尾显示“只读”，顶栏身份为“成员”，导航隐藏审阅修改。截图见 375/768 两张。
- localStorage 切到 W2 并刷新后，更新源只剩 W2 的 `A.xml`（`src-b83c69d54cf05a18` / `/synthetic/W2-A.xml`），W1 同名源不可见；证明前端按 W 重新取数、没有复用旧列表。

## 4. 窄屏 375/768

用 Chrome CDP 将布局视口设为 375×812 与 768×812，打开 W2 的更新源：

| 宽度 | scrollWidth | clientWidth | 页面级横向溢出 | 来源行 |
|---:|---:|---:|---|---:|
| 375 | 375 | 375 | 否 | 1 |
| 768 | 768 | 768 | 否 | 1 |

截图：`updates-sources-375-member.png`、`updates-sources-768-member.png`。

## 5. 复现命令（摘要）

```bash
# 临时数据根 + admin/member 映射
./.venv39/bin/python - <<'PY'
from app.core import storage
storage.configure_data_dir('/tmp/mcc-e2e-40')
storage.upsert_ip_mapping('127.0.0.1', 'E2E管理员')
storage.upsert_admin_user('E2E管理员')
PY
./.venv39/bin/python run.py --data-dir /tmp/mcc-e2e-40 --host 127.0.0.1 --port 8975 --no-reload
# Chrome headless CDP：打开 /#/p/default/updates/{sources,plan,connections,runs,candidates}
# 375/768 通过 Emulation.setDeviceMetricsOverride 后读取 scrollWidth/clientWidth 并截图
```

## 6. 键盘、409 与旧 V3 路由补充（同一 Chrome/CDP 会话）

- 打开更新源编辑抽屉后，初始焦点落在名称输入框（`document.activeElement` 为 input，值 `A.xml`）。
- 在抽屉内把名称改为 `E2E-rename` 后，用另一个客户端把该来源 revision 从 1 推到 2；点击保存返回 409，错误文案为“来源已在其他客户端更新；当前草稿已保留，请刷新后重试”，输入框仍保留 `E2E-rename`，抽屉未关闭。截图：`docs/evidence/m16-40/updates-sources-409-draft-retained.png`。
- 合成 Escape 键关闭抽屉成功；由于抽屉是程序化 click 打开、初始焦点不在编辑按钮，关闭后焦点回退的浏览器级证据不足，留作未完成项。
- 旧 V3 路由逐个打开，标题均正确可达：速览、全局搜索、配置管理、设置、外部工具、DL 计算、文件 `A.xml`、审阅修改。未逐项执行收藏/remarks/参数编辑等交互。

## 7. 未完成项（因此不标 Done）

1. 原 V3 F01–F28 的完整回归（文件/收藏/搜索/remarks/DL/参数界面）只抽样了路由，没有逐项截图与交互。
2. 未保存草稿、409 冲突、键盘 Tab 焦点、modal 关闭/恢复焦点尚未在本 E2E 中记录（前端单元页有部分覆盖）。
3. 运维凭据的真实 `X-Operator-Token` 交互、密码错误锁存 UI、跨站/伪造 Host 的浏览器级验证未执行。
4. 未做 Windows/Linux 实机与真实 SDA 内网；这些仍归 M16-41/42 的 `BLOCKED-EXTERNAL`。
