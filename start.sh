#!/bin/sh
# MCChecker 启动入口（类 Unix；规范 §14.2、§14.3；卡片 M4-06 / INH-1908）。
#
# 与 start.bat 成对，同一套流程：找到解释器 → 卡版本 → 打印数据根与访问地址 → 前台运行。
# 不下载任何东西（H2 零第三方运行依赖），也不碰发布目录之外的数据根：`data/` 仍由
# run.py 按 §3.6 决定（默认发布目录下的 data/，可用 --data-dir 指到别处）。打印的那条
# 走 mcc_resolve_data_dir，与 run.py 同一个优先级——运维照屏幕上的目录备份，不能备份错的。
#
# 逻辑写在函数里，测试用 MCHECKER_START_LIB=0 source 本文件后直接调用这些函数
# （tests/test_release.py 里有），所以刻意不用 `set -e/-u`，错误一律显式 return/exit。

MCHECKER_DEFAULT_PORT=50002
#: 与规范 §14.1、§16.6 一致的下限。
MCHECKER_PYTHON_CHECK='import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'

# 发布包根目录 = 本文件所在目录（只依赖 §14.3 名单里确实存在的东西；不用 dirname，
# 这样 PATH 里只有解释器时也能跑起来）。用 `pwd -P` 取物理路径：run.py 那边的默认数据根
# 是 `os.path.abspath(__file__)` 算的，而 abspath 走 getcwd（会解掉 /tmp → /private/tmp 这类
# 符号链接），用逻辑路径打印就会跟日志里的 `data=` 差一层链接。
mcc_self=$0
case "$mcc_self" in
    */*) MCHECKER_SCRIPT_DIR=$(CDPATH= cd -- "${mcc_self%/*}" && pwd -P) || MCHECKER_SCRIPT_DIR=. ;;
    *) MCHECKER_SCRIPT_DIR=$(pwd) ;;
esac

mcc_die() {
    printf '%s\n' "$1" >&2
    return 1
}

mcc_python_ok() {
    # $1 = 候选解释器；存在且版本 >= 3.9 才算可用。
    [ -n "$1" ] || return 1
    command -v "$1" >/dev/null 2>&1 || return 1
    "$1" -c "$MCHECKER_PYTHON_CHECK" >/dev/null 2>&1
}

mcc_pick_python() {
    # 优先 MCHECKER_PYTHON，其次随包分发的内嵌运行时，最后系统解释器。
    # 内嵌运行时的目录钉在 §14.3 写的 runtime/python/（start.bat 用的同一个目录），
    # 目录里面的形状按平台分两种：Windows embeddable 是 python.exe（start.bat 负责），
    # 类 Unix 的可重定位包一般是 bin/python3，少数直接放一个 python3。
    mcc_seen_old=""
    for mcc_candidate in "$MCHECKER_PYTHON" "$MCHECKER_SCRIPT_DIR/runtime/python/bin/python3" \
                        "$MCHECKER_SCRIPT_DIR/runtime/python/python3" python3.9 python3 python; do
        [ -n "$mcc_candidate" ] || continue
        command -v "$mcc_candidate" >/dev/null 2>&1 || continue
        if mcc_python_ok "$mcc_candidate"; then
            printf '%s\n' "$mcc_candidate"
            return 0
        fi
        mcc_seen_old="$mcc_seen_old $mcc_candidate"
    done
    if [ -n "$mcc_seen_old" ]; then
        mcc_die "启动失败：本机找到的 Python$mcc_seen_old 都低于 3.9（规范要求 3.9.11，见 §14.1 与 §16.6）。请安装 Python 3.9 及以上，或按 §14.3 把内嵌运行时放到 runtime/python/ 后重试。"
    else
        mcc_die "启动失败：本机没有 Python 解释器（已尝试 MCHECKER_PYTHON、runtime/python/、python3.9、python3、python）。请安装 Python 3.9 及以上（§14.1），或用 MCHECKER_PYTHON 指定解释器路径。"
    fi
    return 1
}

mcc_resolve_port() {
    # 打印出来的地址要跟实际监听端口一致：命令行 --port > 环境变量 > 默认（§3.6 的优先级）。
    mcc_port="${MCHECKER_PORT:-$MCHECKER_DEFAULT_PORT}"
    mcc_prev=""
    for mcc_arg in "$@"; do
        if [ "$mcc_prev" = "--port" ]; then
            mcc_port="$mcc_arg"
        fi
        case "$mcc_arg" in
            --port=*) mcc_port="${mcc_arg#--port=}" ;;
        esac
        mcc_prev="$mcc_arg"
    done
    printf '%s\n' "$mcc_port"
}

mcc_resolve_data_dir() {
    # 打印出来的数据根必须是**实际生效**的那一个：优先级与 app/config.py::load_settings 同序
    # （命令行 --data-dir > 环境变量 MCHECKER_DATA_DIR > 发布目录下的 data/，见 §3.6）。
    # §14.3 的升级说明是「把整个 data/ 复制走」，报错目录会把运维引去备份错的根。
    mcc_data="${MCHECKER_DATA_DIR:-$MCHECKER_SCRIPT_DIR/data}"
    mcc_prev=""
    for mcc_arg in "$@"; do
        if [ "$mcc_prev" = "--data-dir" ]; then
            mcc_data="$mcc_arg"
        fi
        case "$mcc_arg" in
            --data-dir=*) mcc_data="${mcc_arg#--data-dir=}" ;;
        esac
        mcc_prev="$mcc_arg"
    done
    printf '%s\n' "$mcc_data"
}

mcc_main() {
    if [ ! -f "$MCHECKER_SCRIPT_DIR/run.py" ]; then
        mcc_die "启动失败：$MCHECKER_SCRIPT_DIR 下没有 run.py，发布包不完整（应含 run.py、main.py、app/、start.sh，见 §14.3）。"
        return 1
    fi
    mcc_python=$(mcc_pick_python) || return 1
    mcc_port=$(mcc_resolve_port "$@")
    mcc_data=$(mcc_resolve_data_dir "$@")
    printf 'MCChecker 启动中：解释器 %s，数据根 %s\n' "$mcc_python" "$mcc_data"
    printf '  （§3.6 优先级：--data-dir > 环境变量 MCHECKER_DATA_DIR > 默认 %s/data；升级要整份备份的是这个目录）\n' \
        "$MCHECKER_SCRIPT_DIR"
    printf '访问地址：http://127.0.0.1:%s/（默认监听 0.0.0.0，内网其他机器用 http://<本机IP>:%s/）\n' \
        "$mcc_port" "$mcc_port"
    printf '停止：Ctrl+C（有序停止见 §14.2）\n'
    cd "$MCHECKER_SCRIPT_DIR" || return 1
    exec "$mcc_python" run.py "$@"
}

if [ "${MCHECKER_START_LIB:-1}" = "1" ]; then
    mcc_main "$@"
    exit $?
fi
