# Project Architecture

## 1. Overview
MCChecker 是一个基于 Python + NiceGUI 的内网配置文件解析检查工具，支持 XML/JSON 格式配置的在线解析、查看、搜索、对比和版本管理。

## 2. Tech Stack
- **Runtime**: Python 3.9 / 3.11 / 3.12
- **Web Framework**: NiceGUI (基于 FastAPI + Vue)
- **Parsing**: xml.etree.ElementTree (标准库), json (标准库)
- **Scheduling**: APScheduler (定时任务)
- **HTTP Client**: urllib (标准库, 用于下载文件)
- **Data Storage**: JSON files (本地持久化)
- **Diff Engine**: difflib (标准库)

## 3. Directory Structure
```
MCChecker/
├── main.py                  # 应用入口
├── requirements.txt         # 依赖声明
├── docs/
│   ├── architecture.md      # 架构文档
│   └── know-how.md          # 经验文档
├── app/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── device_models.py  # 机型配置空间管理
│   │   ├── parser.py        # XML/JSON 解析引擎
│   │   ├── storage.py       # 数据持久化层
│   │   ├── scheduler.py     # 定时任务管理
│   │   ├── differ.py        # 版本对比引擎
│   │   ├── downloader.py    # 文件下载器
│   │   ├── dltool.py        # DL 快捷计算引擎与持久化
│   │   ├── favorites_live.py# 收藏速览数据解析
│   │   └── parse_cache.py   # 解析树缓存
│   ├── pages/
│   │   ├── __init__.py
│   │   ├── home.py          # 主页(速览面板、搜索)
│   │   ├── viewer.py        # 文件解析查看页
│   │   ├── management.py    # 更新设置页
│   │   ├── comparison.py    # 版本对比页
│   │   ├── bindings.py      # 变量绑定配置页
│   │   └── tools.py         # 工具菜单配置页
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── auth.py          # 权限隔离
│   │   └── helpers.py       # 通用工具函数
│   └── static/
│       └── css/
│           └── style.css    # 全局自定义样式
├── data/
│   ├── profiles/            # 各机型的数据空间根目录（按 model_id 分目录）
│   └── device_models.json   # 机型列表（用于顶部导航栏切换）
└── tests/
    ├── test_parser.py
    ├── test_storage.py
    ├── test_differ.py
    └── test_integration.py
```

## 4. Core Modules
- **parser.py**: 解析 XML/JSON 为统一的树形结构，支持折叠展开
- **storage.py**: 管理所有持久化数据，并支持机型维度的隔离存储
- **device_models.py**: 机型列表管理（新增/删除/切换）
- **scheduler.py**: APScheduler 定时任务，支持按机型读取各自的定时配置
- **differ.py**: 基于 difflib 的版本对比，支持变量绑定标注
- **downloader.py**: 从 URL 下载文件，支持重试

## 5. Frontend Architecture
- NiceGUI 声明式 UI 构建
- 左侧固定导航菜单 (文件列表)
- 标签页管理多文件解析结果，支持返回上一页面（历史栈）
- 使用 ui.expansion 构建树形视图，叶子节点旁显示收藏星标
- 收藏星标：未收藏显示空心星(star_outline)，已收藏显示实心星(star)
- 全局搜索与局部搜索
- 版本时间轴组件
- 顶部导航栏显示当前机型、访问身份标签、人员名称与客户端 IP
- 文件树节点支持 hover 出现“修改备注”按钮，并展示按人员归属后的备注列表
- 管理员拥有独立的“审阅修改”入口，可聚合全量待审备注并批量生成新文件

## 6. Backend Architecture
- NiceGUI 路由系统
- 文件上传接口 (NiceGUI upload 组件)
- URL 下载接口 (服务端拉取)
- 受控文件下载接口 (`/api/file-download`，支持当前文件与历史归档文件)
- 数据持久化通过 JSON 文件，包含配置映射、IP 对应表、管理员列表与审阅备注
- 权限隔离基于客户端 IP，并通过 `IP -> 人员 -> 管理员名单` 完成身份识别
- 审阅通过后由服务端回写 XML/JSON 树结构并生成带时间戳的新文件

## 7. Data Flow
1. 用户上传文件/输入URL → 服务端解析 → 树形结构展示
2. 全量更新: 遍历映射表 → 下载文件 → 归档旧版 → 存储新版
3. 搜索: 遍历解析结果树 → 匹配关键词 → 高亮显示
4. 对比: 选择两个版本 → differ 计算 → 展示差异
5. 收藏: 点击变量旁星标 → 切换收藏状态 → 更新 favorites.json
6. 修改备注: 用户提交建议值 → 记录 IP / 归属人员 / 节点信息 → 进入待审列表
7. 审阅修改: 管理员逐项选择通过或驳回 → 生成新文件 → 保存到 `configs` 目录并同步进入左侧文件列表

## 8. Testing Strategy
- 单元测试: pytest
- 测试目录: tests/
- 运行命令: pytest tests/
- 覆盖: 解析引擎、存储层、对比引擎、集成测试

## 9. Development Conventions
- Python 类型注解
- 函数职责单一
- 异步操作使用 asyncio
- 日志使用 logging 标准库
- 所有持久化数据使用 JSON 格式

## 10. Known Constraints
- NiceGUI 需要网络访问以加载前端资源（首次启动）
- 内网部署需确保所有客户端可访问服务端口
- 大文件解析可能需要优化性能
- APScheduler 定时任务在服务重启后需重新注册
