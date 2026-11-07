# -*- coding: utf-8 -*-
"""动作标签时序复现与跨帧率一致性探针（离线，不连板子、不开摄像头）。

三件事：
  A. 复现并量化「宠物已经开始走动/奔跑，标签还停在 sitting/sleeping」的盲区时长；
  B. 验证像素抖动不会被 STALE_DISP_BODY 兜底判据误判成移动；
  C. 验证**同一段轨迹在 2 / 5 / 10 Hz 下给出同样的判定**——
     这是提高 --fps 的前提：特征窗口已全部按秒定义，帧率只改采样密度。

用法：.venv-train/Scripts/python.exe scripts/behavior_lag_probe.py
退出码：任一判据不满足即 1。
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "k1-vision"))
import vision_service as V  # noqa: E402

BW, BH = 220, 200   # 猫侧身框：A = 200/220 = 0.909
IMG_W, IMG_H = 1280, 720
SEED = 20261105
FAIL = []

MOVING_LABELS = ("walking", "running", "playing", "jumping")


def _jitter_at(now, amp):
    """抖动必须是**物理时间**的函数，否则不同帧率的采样点取到的抖动值不同，
    跨帧率对比就没有意义了。按 0.1 s 量化后取同一颗随机种子。"""
    if amp <= 0:
        return 0.0, 0.0
    r = random.Random(SEED * 100003 + int(round(now * 10)))
    return r.uniform(-amp, amp), r.uniform(-amp, amp)


def simulate(hz, still_sec, move_sec, px_per_s, bh=BH, jitter=0.0):
    """按 hz 采样喂一条轨迹，返回 [(t, raw, stable, vb, S, conf)]。

    位置用**时间的闭式函数**给出，不是逐帧累加 —— 累加会让不同帧率的轨迹
    本身不同（2 Hz 在起步那一帧一次性跳 200 px，10 Hz 只跳 40 px），
    跨帧率对比就变成拿两条不同轨迹在比。
    """
    dt = 1.0 / hz
    t = V.PetTracker(1, 0.0)
    cy = 400.0
    out = []
    steps = int(round((still_sec + move_sec) * hz))
    for k in range(steps):
        now = k * dt
        moved = max(0.0, now - still_sec) if px_per_s else 0.0
        cx = 400.0 + px_per_s * moved
        jx, jy = _jitter_at(now, jitter)
        bx, by = cx + jx, cy + jy
        bbox = (bx - BW / 2, by - bh / 2, bx + BW / 2, by + bh / 2)
        t.update(now, bx, by, "cat", bbox)
        raw, conf, _ = t.analyze_behavior(BW, bh, IMG_W, IMG_H, now)
        stable = t.get_stable_behavior(raw, now)
        out.append((now, raw, stable, t._robust_speed_px_s() / bh,
                    t._position_stability(), conf))
    return out


def simulate_jump(hz, hop_h=120.0, hop_dur=0.4, still_sec=6.0, total=10.0):
    """一次原地跳：静止 → 抛物线滞空 hop_dur → 落地静止。

    专门用来验证「跳」这种**短事件**能不能穿过整条链路发布出去。
    跳跃的净位移≈0（升上去又落回来），按速度算会得到 vb≈0，
    这是它与走/跑最本质的区别，也是它必须单独成用例的原因。
    """
    dt = 1.0 / hz
    t = V.PetTracker(1, 0.0)
    cx, cy0 = 400.0, 400.0
    out = []
    for k in range(int(round(total * hz))):
        now = k * dt
        u = (now - still_sec) / hop_dur
        cy = cy0 + (-hop_h * 4 * u * (1 - u) if 0.0 <= u <= 1.0 else 0.0)
        bbox = (cx - BW / 2, cy - BH / 2, cx + BW / 2, cy + BH / 2)
        t.update(now, cx, cy, "cat", bbox)
        raw, conf, _ = t.analyze_behavior(BW, BH, IMG_W, IMG_H, now)
        stable = t.get_stable_behavior(raw, now)
        out.append((now, raw, stable, t._robust_speed_px_s() / BH,
                    t._position_stability(), conf))
    return out


def check_event_published(name, trace, expect, need=2, min_hz=5.0):
    """短事件必须被**发布**（stable），且至少连续出现 need 次。

    need=2 不是随手取的：App 端按 1 Hz 轮询板端 2 Hz 快照，
    只发布 1 次的话有一半概率落在两次轮询之间被完全错过。
    """
    hits = [r[0] for r in trace if r[2] == expect]
    if not hits:
        FAIL.append(f"{name}: 事件 {expect} 未被发布（App 端永远收不到）")
        print(f"  [x] {name}：{expect} 一次都没发布")
        return
    if len(hits) < need:
        FAIL.append(f"{name}: {expect} 只发布 {len(hits)} 次，不足以被 1 Hz 轮询捕获")
        print(f"  [x] {name}：{expect} 只发布 {len(hits)} 次")
        return
    print(f"  [ok] {name}：{expect} 发布于 {hits[0]:.2f}~{hits[-1]:.2f}s，{len(hits)} 次")


def label_at(trace, query_t):
    """取「不晚于 query_t 的最后一帧」的稳定标签。"""
    lab = trace[0][2]
    for now, _raw, stable, *_ in trace:
        if now <= query_t + 1e-9:
            lab = stable
        else:
            break
    return lab


def show(name, trace, still_sec, step_sec):
    """打印 2 Hz 粒度的时间线。"""
    print(f"\n=== {name} ===")
    print("  时刻   vb      S      raw        stable     conf")
    tq = 0.0
    keys = {round(now, 3): (raw, stable, vb, S, conf)
            for now, raw, stable, vb, S, conf in trace}
    while tq <= trace[-1][0] + 1e-9:
        near = min(keys, key=lambda k: abs(k - tq))
        if abs(near - tq) <= step_sec / 2:
            raw, stable, vb, S, conf = keys[near]
            mark = " <- 开始移动" if abs(near - still_sec) < 1e-6 and still_sec > 0 else ""
            S = "inf" if S == float("inf") else f"{S:5.1f}"
            print(f"{near:5.1f}s  {vb:4.2f}  {S}  {raw:<10} {stable:<10} {conf:.2f}{mark}")
        tq += step_sec


def check_onset(name, trace, still_sec, expect):
    """静止结束后 expect 必须在 1.5 s 内被稳定确认。"""
    hit = next((now for now, _r, stable, *_ in trace
                if now >= still_sec - 1e-9 and stable == expect), None)
    if hit is None:
        print(f"  [x] 静止结束后整段内从未出现 {expect}")
        FAIL.append(f"{name}: 未出现 {expect}")
    elif hit - still_sec > 1.5:
        print(f"  [x] 开始移动后 {hit - still_sec:.1f}s 才显示 {expect}（>1.5s）")
        FAIL.append(f"{name}: {expect} 延迟 {hit - still_sec:.1f}s")
    else:
        print(f"  [ok] 开始移动后 {hit - still_sec:.1f}s 显示 {expect}")


def check_no_motion(name, trace):
    bad = sorted({stable for _n, _r, stable, *_ in trace if stable in MOVING_LABELS})
    if bad:
        print(f"  [x] 静止+抖动被判成运动标签：{bad}")
        FAIL.append(f"{name}: 误报运动 {bad}")
    else:
        print("  [ok] 静止+抖动未产生运动标签")


def check_cross_rate(name, still_sec, move_sec, px_per_s, bh=BH, expect=None,
                     hz_list=(2.0, 5.0, 10.0), settle=3.0):
    """同一段物理轨迹在不同帧率下必须给出同样的判断。

    两条判据，分开报告，不合并：
      1) 稳态一致：起步后 settle 秒起（窗口已重新填满），逐 0.5 s 比对稳定标签。
         这是核心判据 —— 帧率只应改采样密度，不应改结论。
      2) 起步延迟极差 ≤ 延迟上限。允许差异存在，因为高帧率会解析出
         2 Hz 混叠掉的加速过程（速度 0→2 体高/秒在 0.5 s 内爬升），
         路径上会多一次 walking→running 的状态切换，确认时间因此拉长。
    """
    traces = {hz: simulate(hz, still_sec, move_sec, px_per_s, bh) for hz in hz_list}
    t_end = min(tr[-1][0] for tr in traces.values())

    t_from = still_sec + settle
    diffs = []
    tq = t_from
    while tq <= t_end + 1e-9:
        labs = {hz: label_at(tr, tq) for hz, tr in traces.items()}
        if len(set(labs.values())) > 1:
            diffs.append((round(tq, 2), labs))
        tq += 0.5
    if diffs:
        print(f"  [x] {name}：稳态标签仍随帧率变化 {diffs[:3]}")
        FAIL.append(f"{name}: 稳态跨帧率不一致 {diffs[:2]}")
    else:
        print(f"  [ok] {name}：稳态 {hz_list} Hz 判定一致")

    if expect:
        latency = {}
        for hz, tr in traces.items():
            hit = next((now for now, _r, stable, *_ in tr
                        if now >= still_sec - 1e-9 and stable == expect), None)
            latency[hz] = None if hit is None else round(hit - still_sec, 2)
        vals = [v for v in latency.values() if v is not None]
        if len(vals) < len(latency):
            print(f"  [x] {name}：部分帧率从未出现 {expect} {latency}")
            FAIL.append(f"{name}: 缺 {expect} {latency}")
        elif max(vals) - min(vals) > 1.0:
            print(f"  [x] {name}：起步延迟跨帧率差 {max(vals)-min(vals):.1f}s > 1.0s {latency}")
            FAIL.append(f"{name}: 延迟极差过大 {latency}")
        else:
            print(f"  [ok] {name}：{expect} 起步延迟 2/5/10 Hz = "
                  + " / ".join(f"{latency[h]}s" for h in hz_list))


if __name__ == "__main__":
    # ── A. 起步响应（2 Hz 基准）────────────────────────────────────
    t1 = simulate(2.0, 6.0, 3.0, 120)
    show("侧身慢走 0.6 体高/秒，经过 3 秒（2 Hz）", t1, 6.0, 0.5)
    check_onset("侧身慢走", t1, 6.0, "walking")

    t2 = simulate(2.0, 6.0, 3.0, 400)
    show("奔跑 2.0 体高/秒，经过 3 秒（2 Hz）", t2, 6.0, 0.5)
    check_onset("奔跑", t2, 6.0, "running")

    t3 = simulate(2.0, 6.0, 3.0, 120, bh=230)
    show("正面慢走 A=1.05，经过 3 秒（2 Hz）", t3, 6.0, 0.5)
    check_onset("正面慢走", t3, 6.0, "walking")

    t4 = simulate(2.0, 0.0, 4.0, 120)
    show("新 tracker 冷启动即移动（2 Hz）", t4, 0.0, 0.5)
    check_onset("冷启动", t4, 0.0, "walking")

    # ── B. 抖动误报面 ──────────────────────────────────────────────
    print("\n=== 静止 + 检测框 ±15px 抖动（2 Hz，10 秒）===")
    t5 = simulate(2.0, 10.0, 0.0, 0, jitter=15.0)
    check_no_motion("抖动", t5)

    # ── C. 跨帧率一致性（提高 --fps 的前提）────────────────────────
    # 判据：同一动作在 2/5/10 Hz 下稳态结论相同、起步延迟极差 ≤1.0 s，
    #       即阈值不需要随帧率重标。
    print("\n=== 跨帧率一致性 ===")
    check_cross_rate("侧身慢走", 6.0, 8.0, 120, expect="walking")
    check_cross_rate("奔跑", 6.0, 8.0, 400, expect="running")
    check_cross_rate("正面慢走", 6.0, 8.0, 120, bh=230, expect="walking")
    check_cross_rate("长时静息", 8.0, 4.0, 0, expect="sleeping")

    # ── D. 短事件必须能穿过链路（jumping 是 3568 虚拟形象的动作来源）──
    # 判据：0.4 s / 0.6 s 的跳在 5 Hz、10 Hz 下都必须被发布 ≥2 次。
    # 2 Hz 下不设判据：0.4 s 只有不到一个采样间隔，物理上采不到，
    # 这是采样定理的硬限制，不是代码问题 —— 也正是要提 fps 的原因。
    print("\n=== 短事件发布（虚拟形象能否收到「跳」）===")
    check_event_published("跳跃 0.4s @5Hz", simulate_jump(5.0), "jumping")
    check_event_published("跳跃 0.4s @10Hz", simulate_jump(10.0), "jumping")
    check_event_published("跳跃 0.6s @5Hz", simulate_jump(5.0, hop_dur=0.6), "jumping")
    # 锁存不能赖着不走：跳完 3 s 必须回到静息标签
    tj = simulate_jump(5.0)
    tail = [r[2] for r in tj if r[0] >= 6.0 + 3.0]
    if tail and any(x == "jumping" for x in tail):
        FAIL.append("跳跃锁存未自愈：3 s 后仍发布 jumping")
        print("  [x] 锁存自愈：跳完 3 s 仍在 jumping")
    else:
        print(f"  [ok] 锁存自愈：跳完 3 s 回到 {tail[-1] if tail else '—'}")

    print()
    if FAIL:
        print(f"FAIL：{len(FAIL)} 项不达标")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("PASS：全部判据达标")
