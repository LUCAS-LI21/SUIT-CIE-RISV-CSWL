#!/system/bin/sh
# touchctl - 3568 触摸桥安全开关
#
# 背景：幽灵触摸的桥接进程 /system/bin/uinput_inject 是全机唯一的
#       HDF -> /dev/uinput 通路。SIGSTOP 它能让幽灵消失，但真手指
#       也一起失效。本脚本保证「暂停」一定能自动恢复。
#
# 用法：
#   touchctl st            查看当前 pid 与状态（T=已暂停 / S=运行中）
#   touchctl off [秒数]     暂停触摸，默认 120 秒后自动恢复
#   touchctl on            立即恢复

LOG=/data/local/tmp/touchctl.log

find_pid() {
  for d in /proc/[0-9]*; do
    case "$d" in
      */self|*/thread-self) continue ;;
    esac
    if grep -qa uinput_inject "$d/cmdline" 2>/dev/null; then
      echo "${d#/proc/}"
      return 0
    fi
  done
  return 1
}

state_of() {
  read -r _tp _tc _ts _trest < /proc/"$1"/stat 2>/dev/null
  echo "$_ts"
}

P=$(find_pid)
if [ -z "$P" ]; then
  echo "ERR: 找不到 uinput_inject 进程"
  exit 1
fi

case "$1" in
  st|status)
    echo "pid=$P state=$(state_of "$P")"
    ;;
  off)
    S=$(state_of "$P")
    if [ "$S" = "T" ]; then
      echo "已在暂停中 pid=$P state=T（未重复操作）"
      exit 0
    fi
    kill -STOP "$P" 2>/dev/null || { echo "ERR: SIGSTOP 失败"; exit 1; }
    T=${2:-120}
    echo "已暂停 pid=$P state=$(state_of "$P")，${T} 秒后自动恢复"
    echo "$(date '+%F %T') STOP pid=$P auto=${T}s" >> "$LOG"
    setsid /bin/sh -c "sleep $T; kill -CONT $P 2>/dev/null; echo \"\$(date '+%F %T') AUTO-CONT pid=$P\" >> $LOG" &
    ;;
  on)
    kill -CONT "$P" 2>/dev/null
    sleep 1
    echo "已恢复 pid=$P state=$(state_of "$P")"
    echo "$(date '+%F %T') CONT pid=$P" >> "$LOG"
    ;;
  *)
    echo "用法: touchctl st | off [秒数] | on"
    ;;
esac
