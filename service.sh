#!/bin/sh
# MCChecker Linux 后台启停（与 start.sh 成对）。
#
# start.sh 仍是前台入口。本脚本负责脱离终端、记下进程，并用 SIGTERM 走有序停止。
# 只发 SIGTERM。强杀会在后台任务还在写 data/ 时拆掉单实例锁。
#
# 逻辑写在函数里。测试用 MCHECKER_SERVICE_LIB=0 source 本文件后直接调用这些函数，
# 所以不用 set -e/-u，错误一律显式 return。
#
# 用法：
#   ./service.sh start   [run.py 的参数...]
#   ./service.sh stop
#   ./service.sh restart [run.py 的参数...]
#   ./service.sh status
#   ./service.sh logs
#   ./service.sh unit

# 停服务时最多等这么久。排空任务约 30 秒；写盘线程还在时，退出前还会再等约 300 秒才放锁。
MCHECKER_SERVICE_STOP_TIMEOUT="${MCHECKER_SERVICE_STOP_TIMEOUT:-330}"
# 拉起后等多久看见单实例锁。到点进程还在，也算已经启动。
MCHECKER_SERVICE_START_TIMEOUT="${MCHECKER_SERVICE_START_TIMEOUT:-15}"

mcc_svc_self=$0
case "$mcc_svc_self" in
    */service.sh|service.sh)
        case "$mcc_svc_self" in
            */*) MCHECKER_SCRIPT_DIR=$(CDPATH= cd -- "${mcc_svc_self%/*}" && pwd -P) || MCHECKER_SCRIPT_DIR=. ;;
            *) MCHECKER_SCRIPT_DIR=$(pwd -P) ;;
        esac
        ;;
    *)
        MCHECKER_SCRIPT_DIR=$(pwd -P)
        ;;
esac

# 复用 start.sh 的解释器、端口和数据根。source 时 $0 不是 start.sh，它会把目录改成当前目录，所以先记住。
mcc_svc_root=$MCHECKER_SCRIPT_DIR
MCHECKER_START_LIB=0
# shellcheck disable=SC1091
. "$mcc_svc_root/start.sh"
MCHECKER_SCRIPT_DIR=$mcc_svc_root

mcc_svc_state="$MCHECKER_SCRIPT_DIR/.mcchecker.service"

mcc_svc_die() {
    printf '%s\n' "$1" >&2
    return 1
}

mcc_svc_args_have_data_dir() {
    mcc_prev=""
    for mcc_arg in "$@"; do
        if [ "$mcc_prev" = "--data-dir" ]; then
            return 0
        fi
        case "$mcc_arg" in
            --data-dir=*) return 0 ;;
        esac
        mcc_prev="$mcc_arg"
    done
    return 1
}

mcc_svc_abs_data() {
    # 与 run.py 一致：相对路径按发布目录解析（start.sh 会先 cd 到那里）。
    case "$1" in
        /*) printf '%s\n' "$1"; return 0 ;;
    esac
    mcc_py=$(mcc_pick_python) || {
        printf '%s\n' "$1"
        return 0
    }
    (
        cd "$MCHECKER_SCRIPT_DIR" || exit 1
        "$mcc_py" -c 'import os, sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$1"
    )
}

mcc_svc_pick_data() {
    if mcc_svc_args_have_data_dir "$@"; then
        mcc_raw=$(mcc_resolve_data_dir "$@") || return 1
        mcc_svc_abs_data "$mcc_raw"
        return $?
    fi
    if [ -f "$mcc_svc_state" ]; then
        IFS= read -r mcc_line < "$mcc_svc_state" || mcc_line=""
        case "$mcc_line" in
            data_dir=?*)
                printf '%s\n' "${mcc_line#data_dir=}"
                return 0
                ;;
        esac
    fi
    mcc_raw=$(mcc_resolve_data_dir "$@") || return 1
    mcc_svc_abs_data "$mcc_raw"
}

mcc_svc_pid_alive() {
    [ -n "$1" ] || return 1
    case "$1" in
        *[!0-9]*) return 1 ;;
    esac
    kill -0 "$1" 2>/dev/null
}

mcc_svc_pid_is_ours() {
    # 锁和 pid 文件里的号可能已被系统拿去复用。只认命令行里还有本程序的进程。
    mcc_svc_pid_alive "$1" || return 1
    if [ -r "/proc/$1/cmdline" ]; then
        mcc_cmd=$(tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null) || mcc_cmd=""
        case "$mcc_cmd" in
            *run.py*|*start.sh*|*main.py*|*os.setsid*) return 0 ;;
            *) return 1 ;;
        esac
    fi
    return 0
}

mcc_svc_read_pidfile() {
    [ -f "$1" ] || return 1
    IFS= read -r mcc_pid < "$1" || return 1
    case "$mcc_pid" in
        *[!0-9]*|"") return 1 ;;
    esac
    printf '%s\n' "$mcc_pid"
}

mcc_svc_json_int() {
    # $1 文本，$2 键。锁文件是 json.dump 的一行，形如 "pid": 12。
    mcc_rest=${1#*\"$2\"}
    [ "$mcc_rest" != "$1" ] || return 1
    mcc_rest=${mcc_rest#*:}
    mcc_rest=${mcc_rest#"${mcc_rest%%[![:space:]]*}"}
    mcc_num=${mcc_rest%%[!0-9]*}
    [ -n "$mcc_num" ] || return 1
    printf '%s\n' "$mcc_num"
}

mcc_svc_json_str() {
    mcc_rest=${1#*\"$2\"}
    [ "$mcc_rest" != "$1" ] || return 1
    mcc_rest=${mcc_rest#*\"}
    printf '%s\n' "${mcc_rest%%\"*}"
}

mcc_svc_lock_text() {
    [ -f "$1" ] || return 1
    tr -d '\n' < "$1"
}

mcc_svc_remember() {
    mcc_data=$1
    shift
    printf 'data_dir=%s\n' "$mcc_data" > "$mcc_svc_state" || return 1
    : > "$mcc_data/.mcchecker.service.args" || return 1
    for mcc_arg in "$@"; do
        printf '%s\n' "$mcc_arg" >> "$mcc_data/.mcchecker.service.args" || return 1
    done
}

mcc_svc_running_pids() {
    # 仍属于本程序的 pid，一行一个：先托管进程，再锁里的服务进程（相同则只打一次）。
    mcc_data=$1
    mcc_seen=" "
    mcc_host=$(mcc_svc_read_pidfile "$mcc_data/.mcchecker.service.pid" 2>/dev/null) || mcc_host=""
    if mcc_svc_pid_is_ours "$mcc_host"; then
        printf '%s\n' "$mcc_host"
        mcc_seen=" $mcc_host "
    fi
    mcc_text=$(mcc_svc_lock_text "$mcc_data/.mcchecker.lock" 2>/dev/null) || mcc_text=""
    if [ -n "$mcc_text" ]; then
        mcc_lock_pid=$(mcc_svc_json_int "$mcc_text" pid) || mcc_lock_pid=""
        if mcc_svc_pid_is_ours "$mcc_lock_pid"; then
            case "$mcc_seen" in
                *" $mcc_lock_pid "*) ;;
                *) printf '%s\n' "$mcc_lock_pid" ;;
            esac
        fi
    fi
}

mcc_svc_any_running() {
    mcc_hit=$(mcc_svc_running_pids "$1")
    [ -n "$mcc_hit" ]
}

mcc_svc_describe() {
    mcc_data=$1
    shift
    mcc_port=$(mcc_resolve_port "$@") || mcc_port="?"
    printf '数据根：%s\n' "$mcc_data"
    mcc_host=$(mcc_svc_read_pidfile "$mcc_data/.mcchecker.service.pid" 2>/dev/null) || mcc_host=""
    if mcc_svc_pid_is_ours "$mcc_host"; then
        printf '托管进程：%s\n' "$mcc_host"
    elif [ -n "$mcc_host" ]; then
        printf '托管进程：%s（已退出）\n' "$mcc_host"
    else
        printf '托管进程：无\n'
    fi
    mcc_text=$(mcc_svc_lock_text "$mcc_data/.mcchecker.lock" 2>/dev/null) || mcc_text=""
    if [ -n "$mcc_text" ]; then
        mcc_lock_pid=$(mcc_svc_json_int "$mcc_text" pid) || mcc_lock_pid="?"
        mcc_lock_port=$(mcc_svc_json_int "$mcc_text" port) || mcc_lock_port=""
        mcc_lock_at=$(mcc_svc_json_str "$mcc_text" started_at) || mcc_lock_at=""
        if mcc_svc_pid_is_ours "$mcc_lock_pid"; then
            printf '实例锁：pid=%s 端口=%s 启动于 %s\n' "$mcc_lock_pid" "$mcc_lock_port" "$mcc_lock_at"
            if [ -n "$mcc_lock_port" ]; then
                mcc_port=$mcc_lock_port
            fi
        else
            printf '实例锁：残留（pid=%s 已不在）\n' "$mcc_lock_pid"
        fi
    else
        printf '实例锁：无\n'
    fi
    printf '访问地址：http://127.0.0.1:%s/\n' "$mcc_port"
    printf '业务日志：%s\n' "$mcc_data/logs/mcchecker.log"
    printf '启动输出：%s\n' "$mcc_data/logs/service.out"
}

mcc_svc_status() {
    mcc_data=$(mcc_svc_pick_data "$@") || return 1
    if mcc_svc_any_running "$mcc_data"; then
        printf '状态：运行中\n'
        mcc_svc_describe "$mcc_data" "$@"
        return 0
    fi
    printf '状态：未运行\n'
    mcc_svc_describe "$mcc_data" "$@"
    return 1
}

mcc_svc_start() {
    if [ ! -f "$MCHECKER_SCRIPT_DIR/start.sh" ] || [ ! -f "$MCHECKER_SCRIPT_DIR/run.py" ]; then
        mcc_svc_die "启动失败：$MCHECKER_SCRIPT_DIR 下缺少 start.sh 或 run.py。"
        return 1
    fi
    mcc_data=$(mcc_svc_pick_data "$@") || return 1
    if mcc_svc_any_running "$mcc_data"; then
        printf '已经在运行，没有再次启动。\n' >&2
        mcc_svc_describe "$mcc_data" "$@" >&2
        return 1
    fi
    mcc_py=$(mcc_pick_python) || return 1
    mkdir -p "$mcc_data/logs" || {
        mcc_svc_die "启动失败：无法创建 $mcc_data/logs。"
        return 1
    }
    mcc_svc_remember "$mcc_data" "$@" || {
        mcc_svc_die "启动失败：无法写下 $mcc_data/.mcchecker.service.args。"
        return 1
    }
    mcc_out="$mcc_data/logs/service.out"
    printf '\n---- %s start ----\n' "$(date '+%Y-%m-%d %H:%M:%S')" >> "$mcc_out"
    # 新会话再换进 start.sh。pid 从这一步保持到 run.py，关闭终端不会把服务一起带走。
    "$mcc_py" -c 'import os, sys; os.setsid(); os.execv(sys.argv[1], sys.argv[1:])' \
        "$MCHECKER_SCRIPT_DIR/start.sh" "$@" >> "$mcc_out" 2>&1 &
    mcc_pid=$!
    printf '%s\n' "$mcc_pid" > "$mcc_data/.mcchecker.service.pid" || return 1

    mcc_ticks=$((MCHECKER_SERVICE_START_TIMEOUT * 5))
    while [ "$mcc_ticks" -gt 0 ]; do
        if ! mcc_svc_pid_alive "$mcc_pid"; then
            printf '启动失败：进程 %s 已退出。启动输出的末尾：\n' "$mcc_pid" >&2
            if [ -f "$mcc_out" ]; then
                tail -n 20 "$mcc_out" >&2 2>/dev/null || cat "$mcc_out" >&2
            fi
            return 1
        fi
        mcc_text=$(mcc_svc_lock_text "$mcc_data/.mcchecker.lock" 2>/dev/null) || mcc_text=""
        if [ -n "$mcc_text" ]; then
            mcc_lock_pid=$(mcc_svc_json_int "$mcc_text" pid) || mcc_lock_pid=""
            if mcc_svc_pid_is_ours "$mcc_lock_pid"; then
                printf '已在后台启动。\n'
                mcc_svc_describe "$mcc_data" "$@"
                printf '停止：service.sh stop\n'
                return 0
            fi
        fi
        mcc_ticks=$((mcc_ticks - 1))
        sleep 0.2
    done
    printf '进程已拉起（pid %s），尚未写入单实例锁。\n' "$mcc_pid"
    mcc_svc_describe "$mcc_data" "$@"
    printf '停止：service.sh stop\n'
    return 0
}

mcc_svc_stop() {
    mcc_data=$(mcc_svc_pick_data "$@") || return 1
    mcc_pids=$(mcc_svc_running_pids "$mcc_data")
    if [ -z "$mcc_pids" ]; then
        printf '未在运行。\n'
        rm -f "$mcc_data/.mcchecker.service.pid"
        return 0
    fi
    printf '正在停止（SIGTERM，等待有序退出）…\n'
    # 不加引号：running_pids 一行一个 pid。
    for mcc_pid in $mcc_pids; do
        kill -TERM "$mcc_pid" 2>/dev/null || true
    done
    mcc_ticks=$((MCHECKER_SERVICE_STOP_TIMEOUT * 2))
    while [ "$mcc_ticks" -gt 0 ]; do
        if ! mcc_svc_any_running "$mcc_data"; then
            rm -f "$mcc_data/.mcchecker.service.pid"
            printf '已停止。\n'
            return 0
        fi
        mcc_ticks=$((mcc_ticks - 1))
        sleep 0.5
    done
    printf '仍未退出（已等待 %s 秒）。锁文件保留，不要再启动第二个实例。\n' \
        "$MCHECKER_SERVICE_STOP_TIMEOUT" >&2
    mcc_svc_describe "$mcc_data" "$@" >&2
    return 1
}

mcc_svc_with_args() {
    # $1 是要调用的函数。没有其余参数时，用上次 start 记下的参数。
    mcc_fn=$1
    shift
    if [ "$#" -eq 0 ]; then
        mcc_data=$(mcc_svc_pick_data) || return 1
        mcc_file="$mcc_data/.mcchecker.service.args"
        if [ -f "$mcc_file" ]; then
            while IFS= read -r mcc_arg || [ -n "$mcc_arg" ]; do
                set -- "$@" "$mcc_arg"
            done < "$mcc_file"
        fi
    fi
    "$mcc_fn" "$@"
}

mcc_svc_restart() {
    # 先停上次记下的那一个实例，再按本次参数（没有就用上次的参数）拉起。
    mcc_svc_stop || return
    mcc_svc_with_args mcc_svc_start "$@"
}

mcc_svc_logs() {
    mcc_data=$(mcc_svc_pick_data "$@") || return 1
    printf '业务日志：%s\n' "$mcc_data/logs/mcchecker.log"
    printf '启动输出：%s\n' "$mcc_data/logs/service.out"
    if [ -f "$mcc_data/logs/service.out" ]; then
        printf '\n'
        tail -n 40 "$mcc_data/logs/service.out" 2>/dev/null || cat "$mcc_data/logs/service.out"
    fi
}

mcc_svc_quote() {
    # systemd 按类似 shell 的规则分词。参数用单引号包起来，内部单引号写成 '\''。
    mcc_q=$1
    mcc_out="'"
    while :; do
        case "$mcc_q" in
            *\'*)
                mcc_out="$mcc_out${mcc_q%%\'*}'\\''"
                mcc_q=${mcc_q#*\'}
                ;;
            *)
                printf '%s' "$mcc_out$mcc_q'"
                return 0
                ;;
        esac
    done
}

mcc_svc_append_unit_arg() {
    case "$1" in
        --reload|--no-reload) return 0 ;;
    esac
    mcc_unit_exec="$mcc_unit_exec $(mcc_svc_quote "$1")"
}

mcc_svc_unit() {
    mcc_data=$(mcc_svc_pick_data "$@") || return 1
    mcc_user=$(id -un 2>/dev/null) || mcc_user=""
    mcc_group=$(id -gn 2>/dev/null) || mcc_group=""
    mcc_unit_exec="$(mcc_svc_quote "$MCHECKER_SCRIPT_DIR/start.sh") --no-reload"
    if [ "$#" -gt 0 ]; then
        for mcc_arg in "$@"; do
            mcc_svc_append_unit_arg "$mcc_arg"
        done
    else
        mcc_file="$mcc_data/.mcchecker.service.args"
        if [ -f "$mcc_file" ]; then
            while IFS= read -r mcc_arg || [ -n "$mcc_arg" ]; do
                mcc_svc_append_unit_arg "$mcc_arg"
            done < "$mcc_file"
        fi
    fi
    cat <<EOF
# 由 service.sh unit 生成。写入 systemd 后用 systemctl 管理，不要再同时 ./service.sh start。
# --no-reload：热更新父进程收到 SIGTERM 会马上退出，systemd 会把同组里还在有序停止的子进程杀掉。
[Unit]
Description=MCChecker
After=network.target

[Service]
Type=simple
EOF
    if [ -n "$mcc_user" ]; then
        printf 'User=%s\n' "$mcc_user"
    fi
    if [ -n "$mcc_group" ]; then
        printf 'Group=%s\n' "$mcc_group"
    fi
    printf 'WorkingDirectory=%s\n' "$(mcc_svc_quote "$MCHECKER_SCRIPT_DIR")"
    printf 'ExecStart=%s\n' "$mcc_unit_exec"
    cat <<EOF
Restart=on-failure
KillSignal=SIGTERM
TimeoutStopSec=$MCHECKER_SERVICE_STOP_TIMEOUT

[Install]
WantedBy=multi-user.target
EOF
}

mcc_svc_usage() {
    printf '%s\n' "用法：service.sh start|stop|restart|status|logs|unit [run.py 的参数...]"
    printf '%s\n' "  start    后台启动（脱离终端）。参数原样交给 start.sh / run.py。"
    printf '%s\n' "  stop     发送 SIGTERM，等待有序停止。这一步结束前不要再启动。"
    printf '%s\n' "  restart  先 stop，再用上次的参数（或本次参数）start。"
    printf '%s\n' "  status   查看托管进程、单实例锁和访问地址。运行中退出码 0，未运行退出码 1。"
    printf '%s\n' "  logs     打印日志路径，并显示启动输出的末尾。"
    printf '%s\n' "  unit     打印 systemd 单元。审查后再写入 /etc/systemd/system/mcchecker.service。"
    return 2
}

mcc_svc_main() {
    mcc_cmd=${1-}
    if [ -z "$mcc_cmd" ]; then
        mcc_svc_usage
        return 2
    fi
    shift
    case "$mcc_cmd" in
        start) mcc_svc_start "$@" ;;
        stop) mcc_svc_stop "$@" ;;
        restart) mcc_svc_restart "$@" ;;
        status) mcc_svc_status "$@" ;;
        logs) mcc_svc_logs "$@" ;;
        unit) mcc_svc_unit "$@" ;;
        -h|--help|help) mcc_svc_usage ;;
        *) mcc_svc_usage ;;
    esac
}

if [ "${MCHECKER_SERVICE_LIB:-1}" = "1" ]; then
    mcc_svc_main "$@"
    exit $?
fi
