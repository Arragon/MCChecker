#!/bin/sh
# 手动添加管理员（全机型共用）。用 sudo 运行，这样数据目录属主不是当前用户时也能写。
#
#   sudo ./add-admin.sh --data-dir /var/lib/mcchecker --name 张三 --ip 10.1.2.3
#
# --ip 可省略。省略时只写入管理员名单；此人的访问 IP 还没进对应表，页面上仍不会变成管理员。
# 姓名、IP 的校验与页面保存相同。已在名单中时不报错，只补上或改写这一条 IP。
#
# 测试用 MCHECKER_ADD_ADMIN_LIB=0 source 本文件。MCHECKER_ADD_ADMIN_SKIP_ROOT=1 跳过 root 检查。

mcc_admin_self=$0
case "$mcc_admin_self" in
    */add-admin.sh|add-admin.sh)
        case "$mcc_admin_self" in
            */*) MCHECKER_SCRIPT_DIR=$(CDPATH= cd -- "${mcc_admin_self%/*}" && pwd -P) || MCHECKER_SCRIPT_DIR=. ;;
            *) MCHECKER_SCRIPT_DIR=$(pwd -P) ;;
        esac
        ;;
    *)
        MCHECKER_SCRIPT_DIR=$(pwd -P)
        ;;
esac

mcc_admin_root=$MCHECKER_SCRIPT_DIR
MCHECKER_START_LIB=0
# shellcheck disable=SC1091
. "$mcc_admin_root/start.sh"
MCHECKER_SCRIPT_DIR=$mcc_admin_root

mcc_admin_die() {
    printf '%s\n' "$1" >&2
    return 1
}

mcc_admin_usage() {
    printf '%s\n' "用法：sudo ./add-admin.sh --name <姓名> [--ip <地址>] [--data-dir <数据根>]"
    printf '%s\n' "  姓名写入数据根的 admin_users.json，所有机型共用。"
    printf '%s\n' "  给了 --ip 时同时写入 ip_mapping.json，该地址的访问者才会被认成这名管理员。"
    printf '%s\n' "  数据根：--data-dir > MCHECKER_DATA_DIR > 上次 service.sh 记下的目录 > <发布目录>/data"
    printf '%s\n' "  sudo 默认不带用户环境变量，数据根不在默认位置时请写 --data-dir。"
    return 2
}

mcc_admin_require_root() {
    if [ "${MCHECKER_ADD_ADMIN_SKIP_ROOT:-0}" = "1" ]; then
        return 0
    fi
    if [ "$(id -u)" -eq 0 ]; then
        return 0
    fi
    mcc_admin_die "请用 sudo 运行：sudo ./add-admin.sh --name <姓名> --ip <地址>"
    return 3
}

mcc_admin_parse() {
    mcc_admin_name=""
    mcc_admin_ip=""
    mcc_admin_data_args=""
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --name)
                shift
                [ "$#" -gt 0 ] || { mcc_admin_die "缺少 --name 的值"; return 2; }
                mcc_admin_name=$1
                ;;
            --name=*)
                mcc_admin_name=${1#--name=}
                ;;
            --ip)
                shift
                [ "$#" -gt 0 ] || { mcc_admin_die "缺少 --ip 的值"; return 2; }
                mcc_admin_ip=$1
                ;;
            --ip=*)
                mcc_admin_ip=${1#--ip=}
                ;;
            --data-dir)
                shift
                [ "$#" -gt 0 ] || { mcc_admin_die "缺少 --data-dir 的值"; return 2; }
                mcc_admin_data_args=$1
                ;;
            --data-dir=*)
                mcc_admin_data_args=${1#--data-dir=}
                ;;
            -h|--help|help)
                mcc_admin_usage
                return $?
                ;;
            *)
                mcc_admin_die "无法识别的参数：$1"
                return 2
                ;;
        esac
        shift
    done
    if [ -z "$mcc_admin_name" ]; then
        mcc_admin_usage
        return 2
    fi
    return 0
}

mcc_admin_abs_data() {
    if [ -n "$mcc_admin_data_args" ]; then
        mcc_raw=$mcc_admin_data_args
    elif [ -n "${MCHECKER_DATA_DIR:-}" ]; then
        mcc_raw=$(mcc_resolve_data_dir) || return 1
    elif [ -f "$MCHECKER_SCRIPT_DIR/.mcchecker.service" ]; then
        IFS= read -r mcc_line < "$MCHECKER_SCRIPT_DIR/.mcchecker.service" || mcc_line=""
        case "$mcc_line" in
            data_dir=?*) mcc_raw=${mcc_line#data_dir=} ;;
            *) mcc_raw=$(mcc_resolve_data_dir) || return 1 ;;
        esac
    else
        mcc_raw=$(mcc_resolve_data_dir) || return 1
    fi
    case "$mcc_raw" in
        /*) printf '%s\n' "$mcc_raw"; return 0 ;;
    esac
    mcc_py=$(mcc_pick_python) || return 1
    (
        cd "$MCHECKER_SCRIPT_DIR" || exit 1
        "$mcc_py" -c 'import os, sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$mcc_raw"
    )
}

mcc_admin_owner() {
    # 写之前记下数据根的属主。目录还不存在时，交给 sudo 之前的那个用户。
    if [ -d "$1" ]; then
        stat -c '%u:%g' "$1" 2>/dev/null || echo "0:0"
        return 0
    fi
    if [ -n "${SUDO_UID:-}" ] && [ -n "${SUDO_GID:-}" ]; then
        printf '%s:%s\n' "$SUDO_UID" "$SUDO_GID"
        return 0
    fi
    echo "0:0"
}

mcc_admin_restore_owner() {
    [ "$(id -u)" -eq 0 ] || return 0
    mcc_owner=$1
    mcc_data=$2
    [ -n "$mcc_owner" ] || return 0
    if [ -d "$mcc_data" ]; then
        chown "$mcc_owner" "$mcc_data" || return 1
    fi
    for mcc_file in "$mcc_data/admin_users.json" "$mcc_data/ip_mapping.json"; do
        if [ -f "$mcc_file" ]; then
            chown "$mcc_owner" "$mcc_file" || return 1
        fi
    done
}

mcc_admin_apply() {
    mcc_data=$1
    mcc_name=$2
    mcc_ip=$3
    mcc_py=$(mcc_pick_python) || return 1
    PYTHONPATH="$MCHECKER_SCRIPT_DIR" "$mcc_py" - "$MCHECKER_SCRIPT_DIR" "$mcc_data" "$mcc_name" "$mcc_ip" <<'PY'
import sys

root, data_dir, name, ip = sys.argv[1:5]
sys.path.insert(0, root)

from app.core import storage
from app.core.errors import FILE_EXISTS, AppError

storage.configure_data_dir(data_dir)
from app.services import people

try:
    people._validated_admin_name(name)
    if ip:
        people._validated_ip_key(people._validated_ip(ip))
except AppError as exc:
    print(exc.message, file=sys.stderr)
    raise SystemExit(1)

try:
    people.add_admin("default", name)
    admin_state = "added"
except AppError as exc:
    if exc.code != FILE_EXISTS:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1)
    admin_state = "exists"

ip_state = "skipped"
if ip:
    try:
        people.put_ip_mapping("default", ip, {"ip": ip, "name": name})
    except AppError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1)
    ip_state = "mapped"

print("%s %s" % (admin_state, ip_state))
PY
}

mcc_admin_main() {
    mcc_admin_require_root || return $?
    mcc_admin_parse "$@" || return $?
    mcc_data=$(mcc_admin_abs_data) || return 1
    mcc_owner=$(mcc_admin_owner "$mcc_data")
    mcc_out=$(mcc_admin_apply "$mcc_data" "$mcc_admin_name" "$mcc_admin_ip") || return 1
    mcc_admin_restore_owner "$mcc_owner" "$mcc_data" || {
        mcc_admin_die "管理员已写入，但无法把文件属主改回 $mcc_owner"
        return 1
    }
    case "$mcc_out" in
        added\ skipped)
            printf '已添加管理员：%s\n' "$mcc_admin_name"
            printf '未绑定 IP。此人打开页面时仍不是管理员，补一次 --ip。\n'
            ;;
        exists\ skipped)
            printf '管理员已在名单中：%s\n' "$mcc_admin_name"
            printf '未绑定 IP。\n'
            ;;
        added\ mapped)
            printf '已添加管理员：%s（%s）\n' "$mcc_admin_name" "$mcc_admin_ip"
            ;;
        exists\ mapped)
            printf '管理员已在名单中：%s，已绑定 %s\n' "$mcc_admin_name" "$mcc_admin_ip"
            ;;
        *)
            mcc_admin_die "写入结果无法识别：$mcc_out"
            return 1
            ;;
    esac
    printf '数据根：%s\n' "$mcc_data"
    printf '该地址刷新页面后生效。\n'
    return 0
}

if [ "${MCHECKER_ADD_ADMIN_LIB:-1}" = "1" ]; then
    mcc_admin_main "$@"
    exit $?
fi
