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
rem 数据根与端口的打印口径跟 start.sh 一致：--data-dir / --port 覆盖环境变量，环境变量覆盖默认。
rem 注意：本文件只能在 Windows 的 cmd.exe 下验证；开发机（macOS）上它只做过静态核对。
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
set "DATA=%ROOT%data"
if defined MCHECKER_DATA_DIR set "DATA=%MCHECKER_DATA_DIR%"
rem 只为「打印出来的端口/数据根 = 实际生效的那一个」而扫命令行（§3.6），真正的参数原样交给 run.py。
call :scan_args %*

echo MCChecker 启动中：解释器 !PYEXE!，数据根 !DATA!
echo   （§3.6 优先级：--data-dir ^> 环境变量 MCHECKER_DATA_DIR ^> 默认 %ROOT%data；升级要整份备份的是这个目录）
echo 访问地址：http://127.0.0.1:!PORT!/（默认监听 0.0.0.0；内网其他机器把 127.0.0.1 换成本机 IP，端口不变）
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

:scan_args
rem 只为显示而扫命令行：用 call + shift 逐个取，不用 `for %%A in (%*)`——后者把用户原文
rem 重新解析一遍（未加引号的带空格路径被切、`%%~A` 剥掉引号、`&` 走到解析器、`!` 被延迟展开
rem 吃掉）。这里按位置取值，绝不把用户文本再拼回任何命令行；算出来的 PORT/DATA 只喂上面
rem 两行 echo，传给 run.py 的仍然是原始 %*。
:scan_next
if "%~1"=="" exit /b 0
if /i "%~1"=="--port" goto :scan_port
if /i "%~1"=="--data-dir" goto :scan_data
for /f "tokens=1,* delims==" %%A in ("%~1") do (
    if /i "%%A"=="--port" if not "%%B"=="" set "PORT=%%B"
    if /i "%%A"=="--data-dir" if not "%%B"=="" set "DATA=%%B"
)
shift
goto :scan_next
:scan_port
if not "%~2"=="" set "PORT=%~2"
shift
shift
goto :scan_next
:scan_data
if not "%~2"=="" set "DATA=%~2"
shift
shift
goto :scan_next

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
