# 使用与运维手册

## 1. 概述

MCChecker 是一个基于 Python + NiceGUI 的内网配置文件解析检查工具，支持 XML/JSON 配置的在线解析、搜索、对比与版本归档。

## 2. 环境准备

- Python: 3.9/3.11/3.12
- 依赖：见 [requirements.txt](file:///d:/Project/MCChecker/requirements.txt)
- 测试依赖：需要额外安装 `pytest`（仓库未在 `requirements.txt` 内声明）

## 3. 安装

```bash
pip install -r requirements.txt
```

## 4. 启动与停止

启动：

```bash
python main.py
```

默认监听：`0.0.0.0:50001`

停止：

- 前台运行：在终端按 `Ctrl+C`

## 5. 多机型适配

### 5.1 切换入口

- 顶部导航栏的“机型切换”入口支持：
  - 快速切换机型
  - 新增机型（可选从当前机型复制配置）
  - 删除机型（可选同时删除该机型的全部配置数据）

### 5.2 隔离范围

每个机型拥有独立的数据空间，互不影响，包含但不限于：

- 全量配置映射（文件名 ↔ 下载链接）
- 收藏夹（收藏变量与备注）
- 变量绑定配置
- 工具菜单
- 定时更新配置
- DL 快捷计算参数
- 上传/下载保存的配置文件、历史归档版本与解析缓存

## 6. 配置项

### 6.1 NICEGUI_STORAGE_SECRET（可选）

用于启用 NiceGUI 的用户级持久化能力（如记住当前机型选择）。可通过环境变量注入：

- `NICEGUI_STORAGE_SECRET`: 任意随机字符串

未设置时，应用会自动生成并写入到 `data/nicegui_storage_secret.txt`，以保证刷新页面或重启服务后仍能正确读取此前的机型选择。

## 7. 数据目录说明

默认数据目录为 `data/`，其中：

- 默认机型使用 `data/profiles/default/` 作为数据空间
- 非默认机型使用 `data/profiles/<model_id>/` 作为数据空间

该目录内包含配置文件、历史归档、缓存与若干 JSON 持久化文件。
