# 环境与依赖说明

## 1. 运行环境

- 操作系统：Windows / Linux 均可（内网部署常见为 Windows）
- Python：3.9 / 3.11 / 3.12

## 2. 运行依赖

安装命令：

```bash
pip install -r requirements.txt
```

依赖列表见 [requirements.txt](file:///d:/Project/MCChecker/requirements.txt)。

## 3. 测试依赖

仓库使用 `pytest` 作为测试框架，但未在 `requirements.txt` 中固定声明；运行测试前需要自行安装：

```bash
pip install pytest
```

## 4. 可选配置

- `NICEGUI_STORAGE_SECRET`：用于启用 NiceGUI 的用户级持久化（例如记住当前机型选择）。未设置时会自动生成并存储到 `data/nicegui_storage_secret.txt`。

