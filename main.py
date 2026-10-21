"""MCChecker 启动入口（兼容旧路径；规范 §3.2）。

真正的实现在 `run.py`；`python main.py` 与 `python run.py` 等价。
"""

from __future__ import annotations

from run import main

if __name__ == "__main__":
    raise SystemExit(main())
