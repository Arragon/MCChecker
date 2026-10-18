@echo off
rem MCChecker 启动入口（Windows；规范 §14.2、§14.3；卡片 M4-06 / INH-1908）。
rem
rem 与 start.sh 成对：找到解释器 -> 卡版本 -> 打印访问地址 -> 前台运行。
rem 不下载任何东西（H2 零第三方运行依赖）；数据根仍由 run.py 按 §3.6 决定
rem （默认发布目录下的 data\，可用 --data-dir 指到别处）。
rem
rem 优先级：MCHECKER_PYTHON（必须是单个 exe 路径，不能带参数）
rem          -> runtime\python\python.exe（§14.3 的内嵌 embeddable 包，可选）
rem          -> python -> py
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "ROOT=%~dp0"
set "CHECK=import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"

if not exist "%ROOT%run.py" goto :missing_entry

set "PYEXE="
if defined MCHECKER_PYTHON call :probe "%MCHECKER_PYTHON%"
if not defined PYEXE call :probe "%ROOT%runtime\python\python.exe"
if not defined PYEXE call :probe "python"
if not defined PYEXE call :probe "py"
if not defined PYEXE goto :no_python

set "PORT=50002"
if defined MCHECKER_PORT set "PORT=%MCHECKER_PORT%"
set "PREV="
for %%A in (%*) do (
    set "ARG=%%~A"
    if "!PREV!"=="--port" set "PORT=!ARG!"
    set "EQ=!ARG:~0,7!"
    if "!EQ!"=="--port=" set "PORT=!ARG:~7!"
    set "PREV=!ARG!"
)

echo MCChecker 启动中：解释器 %PYEXE%，数据根默认为 %ROOT%data（--data-dir 可改，§3.6）
echo 访问地址：http://127.0.0.1:%PORT%/（默认监听 0.0.0.0，内网其他机器用 http://本机IP:%PORT%/）
echo 停止：Ctrl+C，或直接关闭本窗口（有序停止见 §14.2）

cd /d "%ROOT%"
call "%PYEXE%" run.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo 启动失败：MCChecker 退出码 %RC%，原因见上方日志（§14.2）。
    pause
)
exit /b %RC%

:probe
rem %~1 = 候选解释器；存在且版本不低于 3.9 才记进 PYEXE。找不到就静默换下一个。
if defined PYEXE exit /b 0
"%~1" -c "%CHECK%" >nul 2>nul
if errorlevel 1 exit /b 0
set "PYEXE=%~1"
exit /b 0

:missing_entry
echo 启动失败：%ROOT% 下没有 run.py，发布包不完整（应含 run.py、main.py、app\、start.bat，见规范 §14.3）。
pause
exit /b 1

:no_python
echo 启动失败：本机没有 Python 3.9 或更高版本（规范要求 3.9.11，见 §14.1 与 §16.6）。
echo 已尝试：MCHECKER_PYTHON、runtime\python\python.exe、python、py。
echo 请安装 Python 3.9.11，或按 §14.3 把官方 embeddable 包解压到 runtime\python\ 后重试。
pause
exit /b 1
