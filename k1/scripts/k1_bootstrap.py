# -*- coding: utf-8 -*-
"""三端自愈引导：只要 PC 开着，3568 和 K1 无论怎么重启/掉线都能自己接回来。

设计原则（2026-11-05 重写）
  旧版只在「子进程退出了」时重启，于是踩了两个真实事故：
    · 两个 bootstrap 同时跑，各拉一套推流去抢同一个摄像头，两个都读不到帧，
      而进程都活着 —— 旧判据认为「一切正常」，K1 六小时零帧。
    · camingest 挂了但隧道还在，旧版完全不管 K1 侧。
  所以现在的判据是 **数据在不在流**，不是进程在不在：
    /health 拿得到 + frames 在涨  ⇒ 整条链路活着
    /health 拿不到               ⇒ 隧道或 K1 服务的问题（不动 pusher）
    /health 正常但 frames 不涨   ⇒ 推流段的问题（重启 pusher）

自愈顺序（每轮 8 秒）
  1. 单实例：Windows 命名互斥体，比 PID 文件可靠（崩溃时内核自动释放）
  2. hdc 找不到 3568 → hdc kill/start 重连，等它回来
  3. 3568:2222 没监听 → 补 busybox nc 中继
  4. 本地 11436 没监听 → 起 k1_portfwd（内含 fport 补建 + SSH keeper）
  5. 11436 连不上但端口在 → SSH 到 K1 重启 k1-camingest
  6. frames 停滞超时 → 杀 pusher 重起（摄像头被占用/曝光卡死都靠这条救）
  7. 3568 上前台不是 com.ljp.myapplication → aa start
  8. 每 60 秒确认 k1-vision 是 disabled（它和 camingest 抢 AI 核 TCM，
     起来必崩，还会把 camingest 拖死）

用法
  python scripts/k1_bootstrap.py            # 常驻（开机自启调这个）
  python scripts/k1_bootstrap.py --once     # 只体检一遍，排障用
  python scripts/k1_bootstrap.py --status   # 只打印当前状态，不修
"""
import os as _os_sys
import sys as _sys_sys
_sys_sys.path.insert(0, _os_sys.path.dirname(_os_sys.path.abspath(__file__)))
import _nowin  # noqa: F401  —— 统一禁止子进程弹控制台

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 两个子进程要不同的解释器：
#   · 推流用 opencv  —— 只有 .venv-train 装了
#   · 隧道用 paramiko —— 同上，统一用一个
PY_PUSHER = str(ROOT / ".venv-train" / "Scripts" / "pythonw.exe")
if not Path(PY_PUSHER).exists():
    PY_PUSHER = sys.executable
PY_TUNNEL = PY_PUSHER

# hdc 有好几份，按可用性试。套件自带那份路径带中文和空格，但历史上一直能用。
HDC_CANDIDATES = [
    r"E:\CIE-RISC-V\05-SC-3568HA开发套件（ISOH-SC-3568HA）-基础版\05 工具\OpenHarmony\HDC 工具\hdc.exe",
    r"E:\HarmonyOS\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe",
]
DEVICE = "df011853434133324710076f03096b00"
APP_BUNDLE = "com.ljp.myapplication"  # 主线前端（五页签，含 AI 对话），源在 D:\p
# 曾经写成 com.ljp.petworld（E:\p 的四页签版，已从 3568 卸载）。
# 判定包名归属别靠记忆，扫盘比对：grep bundleName */AppScope/app.json5
RELAY_PORT = 2222
K1_VISION_PORT = 11436
LOCK_PORT = 11445                    # 单实例锁（独占监听，见 acquire_single_instance）
FRAME_STALL_SEC = 25.0               # frames 多久不涨算推流卡死
K1_SVC_CHECK_SEC = 60.0              # k1-vision 是否被重新装回来的检查周期
K1_CLOCK_CHECK_SEC = 600.0           # K1 时间偏差检查周期

# 本机装了 HTTP_PROXY 时 urllib 会把 127.0.0.1 也发给代理，代理回 502，
# 表现成「板端挂了」。这里彻底绕过代理。
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

procs = {}
_lock_sock = None
_last_frames = [-1, time.time()]
_last_k1_svc_check = 0.0
_last_k1_clock_check = 0.0
_reuse_logged = False


# ---------------------------------------------------------------- 日志
def _ensure_std_streams(force_file=True):
    """把 stdout/stderr 无条件接到日志文件。

    这里踩过两个坑，都跟「以为 pythonw 下 stdout 是 None」有关：
      1. 实测 pythonw 的 sys.stdout 不是 None，而是一个**能写、不报错、
         但什么都不落盘**的句柄 —— 于是判断 `is None` 的兜底完全不触发，
         开机自启后日志凭空消失，出问题只能靠猜。
      2. venv 里的 pythonw.exe 是个转发 stub，会再 CreateProcess 一个真解释器，
         父子进程命令行完全相同。两个进程各自接管同一个日志文件会互相覆盖，
         所以文件用 append + 行缓冲，且锁只认端口（见 acquire_single_instance）。
    """
    if not force_file:
        return
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    f = open(logs / "bootstrap.log", "a", encoding="utf-8",
             errors="replace", buffering=1)
    sys.stdout = f
    sys.stderr = f


def log(msg):
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] [boot] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        try:
            with open(ROOT / "logs" / "bootstrap_fallback.log", "a",
                      encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def rotate_log(path, limit_mb=4):
    """日志无限长会拖慢启动（也是磁盘垃圾）。超过 4MB 就换名字重开一份。"""
    try:
        if path.exists() and path.stat().st_size > limit_mb * 1024 * 1024:
            bak = path.with_suffix(path.suffix + ".1")
            if bak.exists():
                bak.unlink()
            path.rename(bak)
    except Exception:
        pass


# ---------------------------------------------------------------- 单实例
def acquire_single_instance():
    """用「独占监听一个本地端口」当锁。

    先后试过两种都不行：
      · PID 文件 + os.kill(pid, 0)：挡不住重复实例（PID 会被复用，对已死的
        pid 也不一定抛异常），实测起出两套推流去抢同一个摄像头，两边都读不到
        帧却都「活着」——K1 因此六小时零帧。
      · Windows 命名互斥体：venv 的 pythonw.exe 是转发 stub，会再起一个真
        解释器且命令行相同；互斥体在哪个进程里创建、谁先退出，都不受控。
    端口锁由内核保证唯一：bind 成功后别人 bind 必失败，进程一死立刻释放，
    也没有僵尸文件要清。
    """
    global _lock_sock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 绝对不能开 SO_REUSEADDR：Windows 上开了之后第二个进程也能 bind 同一
    # 端口，锁就形同虚设。
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
    except OSError:
        try:
            s.close()
        except Exception:
            pass
        return False
    _lock_sock = s
    return True


# ---------------------------------------------------------------- hdc / 3568
def hdc_path():
    for p in HDC_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def sh(cmd, timeout=25):
    """执行 hdc shell。**必须 DEVNULL**：hdc 会 fork 常驻 server 继承 stdout，
    用 PIPE 捕获输出会永久等不到 EOF（实测挂死 >90s）。要看输出用 sh_out。"""
    hdc = hdc_path()
    if not hdc:
        return False
    try:
        r = subprocess.run([hdc, "-t", DEVICE, "shell", cmd],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=timeout)
        return r.returncode == 0
    except Exception:
        return False


def sh_out(cmd, timeout=25):
    """需要看输出的场合：写临时文件再读，避开 hdc 的 EOF 陷阱。"""
    hdc = hdc_path()
    if not hdc:
        return ""
    tmp = ROOT / "_hdc_out.txt"
    try:
        with open(tmp, "wb") as f:
            subprocess.run([hdc, "-t", DEVICE, "shell", cmd],
                           stdout=f, stderr=subprocess.DEVNULL, timeout=timeout)
        return tmp.read_text(errors="replace")
    except Exception:
        return ""


def hdc_alive():
    hdc = hdc_path()
    if not hdc:
        return False
    tmp = ROOT / "_hdc_targets.txt"
    try:
        with open(tmp, "wb") as f:
            subprocess.run([hdc, "list", "targets"], stdout=f,
                           stderr=subprocess.DEVNULL, timeout=15)
        return DEVICE in tmp.read_text(errors="replace")
    except Exception:
        return False


def hdc_restart_server():
    """hdc server 会卡死：fport 报 OK 但本地没人监听，或 shell 直接 SIGTERM。"""
    hdc = hdc_path()
    if not hdc:
        return
    for a in ("kill", "start"):
        try:
            subprocess.run([hdc, a], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=20)
        except Exception:
            pass
    time.sleep(2)


def relay_alive():
    """3568 上 2222 是否真的在 **LISTEN**。

    坑（2026-11-05 实测）：OpenHarmony 的 netstat 不认 -l —— 加了 -l 照样把
    ESTABLISHED / TIME_WAIT 列出来。所以不能 `netstat -tln | grep -c 2222`：
    中继被杀掉之后，残留的 TIME_WAIT 还会命中 8 行，判据一直返回「活着」，
    于是永远不补建，链路就卡死在那儿了。必须显式找 LISTEN 那一行。
    """
    out = sh_out(f"netstat -tln | grep ':{RELAY_PORT} ' | grep LISTEN")
    return "LISTEN" in out


def ensure_relay():
    if relay_alive():
        return True
    log("3568 中继掉线，重新拉起 2222")
    here = Path(__file__).resolve().parent / "hop.sh"
    if not (ROOT / "_relay_uploaded").exists() and here.exists():
        # hop.sh 在 /data/local/tmp 是持久保存的，只有 3568 刷机才会丢。
        # 第一次上传后记个标记，之后不再无谓地传。
        try:
            hdc = hdc_path()
            subprocess.run([hdc, "-t", DEVICE, "file", "send",
                            str(here), "/data/local/tmp/hop.sh"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=60)
            sh("chmod 755 /data/local/tmp/hop.sh")
            (ROOT / "_relay_uploaded").write_text("1")
        except Exception as e:
            log(f"上传 hop.sh 失败: {e}")
    sh(f"sh -c 'nohup busybox nc -lk -p {RELAY_PORT} -e /data/local/tmp/hop.sh >/dev/null 2>&1 &'")
    time.sleep(1.5)
    ok = relay_alive()
    log(f"中继{'已恢复' if ok else '仍未起来'}")
    return ok


def app_running():
    out = sh_out(f"pidof {APP_BUNDLE}")
    return any(ch.isdigit() for ch in out)


def ensure_app():
    """3568 重启后应用不会自己起来。已经在跑就不要重复 start。"""
    if app_running():
        return True
    sh(f"aa start -a EntryAbility -b {APP_BUNDLE}", timeout=30)
    log(f"已请求启动 3568 应用 {APP_BUNDLE}")
    return True


# ---------------------------------------------------------------- K1
def port_busy(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) == 0


def vision_health(timeout=5):
    """拿 K1 的 /health。返回 dict 或 None（None = 拿不到，不等于服务死了）。"""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{K1_VISION_PORT}/health", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def k1_ssh(cmd, timeout=20):
    """直连 127.0.0.1:2222（→ 3568 nc → K1:22）。

    刻意**不**复用 scripts/k1_ssh.py：那个脚本每次调用都会重建 fport，
    而 k1_portfwd 的常驻 SSH 正挂在同一条转发上，重建会把它的连接掐断。
    """
    try:
        import paramiko
    except Exception:
        return None
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        pw = __import__("os").environ.get("K1_PASSWORD", "")
        if not pw:/n            print("[FATAL] 请先导出板端口令：export K1_PASSWORD=***")
            return 1
        c.connect("127.0.0.1", port=RELAY_PORT, username="root", password=pw,
                  timeout=8, banner_timeout=10, auth_timeout=10,
                  look_for_keys=False, allow_agent=False)
        _, out, _ = c.exec_command(cmd, timeout=timeout)
        return out.read().decode("utf-8", "replace")
    except Exception:
        return None
    finally:
        try:
            c.close()
        except Exception:
            pass


def ensure_k1_service():
    """camingest 挂了就重启。只在「端口在听但 health 拿不到」时调用。"""
    st = k1_ssh("systemctl is-active k1-camingest")
    if st is None:
        log("SSH 到 K1 失败，跳过服务检查")
        return
    st = st.strip()
    if st == "active":
        log("K1 camingest 是 active 但 health 拿不到 —— 可能刚在重启，先等")
        return
    log(f"K1 camingest = {st}，重启它")
    k1_ssh("systemctl restart k1-camingest")
    time.sleep(3)


def ensure_k1_conflict_free():
    """k1-vision 和 k1-camingest 都监听 11436、都要 SpaceMIT AI 核 TCM。

    **`disable` 是不够的**（2026-11-05 实测）：disable 只挡开机自启，挡不住
    「已经在跑 → 崩溃 → auto-restart」这个循环。当时 k1-vision 处于
    activating/auto-restart、NRestarts=18，每 6 秒爬起来抢一次 TCM，把
    camingest 的 infer 从 150ms 拖到 1400ms、fps 掉到 0.6。
    **`mask` 也不行**：service 文件本身就在 /etc/systemd/system 下，mask 要
    在同目录建同名符号链接，直接报 "File ... already exists"。
    → 唯一干净的办法是把 unit 文件移走再 daemon-reload，让 systemd 彻底不认它。
    """
    global _last_k1_svc_check
    if time.time() - _last_k1_svc_check < K1_SVC_CHECK_SEC:
        return
    _last_k1_svc_check = time.time()
    raw = k1_ssh("systemctl list-unit-files 2>/dev/null | grep -c '^k1-vision.service'")
    if raw is None:
        return
    try:
        n = int((raw.strip().splitlines() or ["0"])[-1])
    except ValueError:
        n = 0
    if n == 0:
        return                                  # 已彻底移除，正常
    log("k1-vision unit 又出现了（会和 camingest 抢 AI 核 TCM），移走它")
    k1_ssh("systemctl stop k1-vision 2>&1; "
           "mv -f /etc/systemd/system/k1-vision.service "
           "/etc/systemd/system/k1-vision.service.disabled 2>/dev/null; "
           "systemctl daemon-reload")
    


def ensure_k1_clock():
    """K1 的 RTC 每次重启必漂（实测一次差 8 小时整，看着像时区其实是时间本身错）。
    时间错会让 journalctl 的时间戳和 PC 对不上，排障时误判「几小时前就挂了」。
    每 10 分钟对一次，偏差超过 30s 才改，避免频繁写 RTC。"""
    global _last_k1_clock_check
    if time.time() - _last_k1_clock_check < K1_CLOCK_CHECK_SEC:
        return
    _last_k1_clock_check = time.time()
    raw = k1_ssh("date +%s 2>/dev/null")
    if raw is None:
        return
    try:
        k1_ts = float((raw.strip().splitlines() or ["0"])[-1])
    except ValueError:
        return
    drift = time.time() - k1_ts
    if abs(drift) < 30:
        return
    log(f"K1 时间偏差 {drift:+.0f}s，对齐到 PC 时间")
    cmd = time.strftime("%Y-%m-%d %H:%M:%S")
    k1_ssh(f"date -s '{cmd}' >/dev/null 2>&1; hwclock -w 2>/dev/null")


# ---------------------------------------------------------------- 子进程
def start_child(name, args):
    # 子进程 stdout 不能接 DEVNULL：Python 按 locale 编码写日志，中文一写就可能
    # UnicodeEncodeError 直接退出（实测隧道进程秒退）。统一写文件并强制 UTF-8。
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    rotate_log(logs / f"{name}.log")
    out = open(logs / f"{name}.log", "ab")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(args, cwd=str(ROOT), stdout=out, stderr=out,
                         env=env, bufsize=0, creationflags=flags)
    procs[name] = p
    log(f"{name} 已启动 pid={p.pid}")
    return p


def child_dead(name):
    p = procs.get(name)
    return p is None or p.poll() is not None


def kill_child(name):
    p = procs.get(name)
    if p is None:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(p.pid), "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        pass
    try:
        p.wait(timeout=5)
    except Exception:
        pass
    procs.pop(name, None)


PUSHER_ARGS = [PY_PUSHER, "-u", str(ROOT / "scripts" / "pc_cam_vision.py"),
               "--push", f"http://127.0.0.1:{K1_VISION_PORT}",
               "--port", "11441", "--width", "640", "--height", "480"]


def ensure_tunnel():
    global _reuse_logged
    if child_dead("tunnel"):
        if port_busy(K1_VISION_PORT):
            # 端口在听说明外部已有一份在跑（比如手动起的），直接复用，
            # 别再加一个抢同一个端口 —— 那会让两边都 bind 失败。
            # 每轮都打会刷屏，只在状态变化时记一次。
            if not _reuse_logged:
                log("隧道端口已在监听，复用现有实例")
                _reuse_logged = True
            return
        _reuse_logged = False
        start_child("tunnel", [PY_TUNNEL, str(ROOT / "scripts" / "k1_portfwd.py")])


def ensure_pusher():
    if child_dead("pusher"):
        start_child("pusher", PUSHER_ARGS)


def kill_pusher_by_port():
    """按端口找占用者并结束。处理「进程不是我起的」这种情况
    （上一会话残留 / 手动起的），否则两个推流会一直抢摄像头。

    netstat 在中文 Windows 上输出 GBK，用 text=True 会抛 UnicodeDecodeError
    （异常被吞掉后 stdout 是 None，再 .splitlines() 就崩），所以自己解码。
    """
    try:
        r = subprocess.run(["netstat", "-ano"], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        return
    if not r.stdout:
        return
    out = r.stdout.decode("gbk", "replace")
    pids = set()
    for line in out.splitlines():
        if f":11441" in line and "LISTENING" in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(parts[-1])
    for pid in pids:
        if str(pid) == str(os.getpid()):
            continue
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=15)
            log(f"清掉占用 11441 的进程 pid={pid}")
        except Exception:
            pass


# ---------------------------------------------------------------- 一轮体检
def tick():
    """返回 True 表示这一轮三端都在线。"""
    if not hdc_alive():
        log("找不到 3568，尝试重启 hdc server")
        hdc_restart_server()
        if not hdc_alive():
            log("3568 仍不在线，等下一轮")
            return False
        log("3568 已上线")

    ensure_relay()
    ensure_tunnel()
    ensure_pusher()          # 不存在就起；卡死的情况下面按 frames 停滞再处理

    h = vision_health()
    if h is None:
        if port_busy(K1_VISION_PORT):
            # 端口在听却拿不到 health → K1 那头的服务问题
            ensure_k1_service()
        else:
            log("11436 端口没在听，等隧道下轮重建")
        return False

    # 到这里说明 vision 通道是通的。判帧流在不在动。
    frames = int(h.get("frames") or 0)
    if frames != _last_frames[0]:
        _last_frames[0] = frames
        _last_frames[1] = time.time()
    stalled = time.time() - _last_frames[1]
    if stalled > FRAME_STALL_SEC:
        log(f"frames 已 {stalled:.0f}s 不涨（{frames} 帧），重启推流")
        kill_child("pusher")
        # 顺手清掉占着 11441 的其它进程：上一会话遗留的推流会和新的抢摄像头
        kill_pusher_by_port()
        _last_frames[1] = time.time()
        start_child("pusher", PUSHER_ARGS)
        return False

    ensure_app()
    ensure_k1_conflict_free()
    ensure_k1_clock()
    return True


def vision_latest(timeout=5):
    """/health 只有计数器，识别结果在 /v1/vision/latest。"""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{K1_VISION_PORT}/v1/vision/latest", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def status():
    print("=== 三端状态 ===")
    print(f"hdc 路径      : {hdc_path()}")
    print(f"3568 在线     : {hdc_alive()}")
    print(f"3568 中继 2222: {relay_alive()}")
    print(f"本地隧道 11436: {port_busy(K1_VISION_PORT)}")
    h = vision_health()
    if h:
        print(f"K1 vision     : ok  frames={h.get('frames')} fps={h.get('fps')} "
              f"infer={h.get('infer_ms')}ms ep={h.get('providers')}")
    else:
        print("K1 vision     : 拿不到 /health")
    v = vision_latest()
    if v:
        print(f"K1 当前识别   : pet={v.get('pet') or '-'} label={v.get('label')} "
              f"conf={v.get('conf')} action={v.get('action')} "
              f"behavior_hz={v.get('behavior_hz')}")
    else:
        print("K1 当前识别   : 拿不到 /v1/vision/latest")
    print(f"3568 应用     : {APP_BUNDLE} 进程{'在' if app_running() else '不在'}")
    print(f"引导子进程    : {[(k, v.pid, v.poll() is None) for k, v in procs.items()]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="只体检一次")
    ap.add_argument("--status", action="store_true", help="只打印状态，不做修复")
    a = ap.parse_args()

    if a.status:
        status()          # 手动排障用，保持打到控制台
        return

    # 先轮转再接管 stdout：反过来会让句柄指着刚被改名的旧文件，
    # 之后所有日志都写进 bootstrap.log.1，看着像「日志停了」。
    rotate_log(ROOT / "logs" / "bootstrap.log")
    _ensure_std_streams()
    if not acquire_single_instance():
        log(f"已有引导实例在跑（锁端口 {LOCK_PORT}），本实例退出")
        return
    log(f"引导启动 pid={os.getpid()}")
    kill_pusher_by_port()
    # 上一会话残留的推流可能还占着摄像头（进程活着但读不到帧），先清干净。
    # 这是「两个推流抢同一个摄像头」的另一个入口：互斥体只挡得住引导进程，
    # 挡不住上次崩溃前 fork 出去、父进程没了还在跑的子进程。
    kill_pusher_by_port()

    # 开机时 hdc 可能还没枚举到设备，先给一次重连机会
    if not hdc_alive():
        log("等 3568 上线（每 5 秒重试，最多 2 分钟）")
        for _ in range(24):
            if hdc_alive():
                break
            hdc_restart_server()
            time.sleep(5)
        else:
            log("2 分钟内没等到 3568，转入慢速轮询")

    if a.once:
        tick()
        log("单次体检完成")
        return

    slow = False
    while True:
        try:
            ok = tick()
            if ok and slow:
                log("三端已全部恢复")
                slow = False
            elif not ok:
                slow = True
        except Exception as e:
            log(f"体检异常: {e}")
        time.sleep(8)


if __name__ == "__main__":
    main()
