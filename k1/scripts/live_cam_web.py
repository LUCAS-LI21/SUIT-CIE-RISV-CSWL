#!/usr/bin/env python3
"""摄像头实时画面 + 检测框可视化（PC 本地推理模式配套）。

为什么需要它
    pc_cam_vision.py 只吐 JSON 和一张不带框的原图（/v1/vision/frame.jpg）。
    想「把手机对着摄像头看它认不认得出狗在跑」，必须同时看到
      · 画面本身（构图对不对、宠物占多大）
      · 检测框（有没有框到、框偏没偏）
      · label / action（判定结果，这是要看的东西）
    于是这里做一个只读的转发页：不碰摄像头，只从推理服务取帧和结果。

用法
    .venv-train/Scripts/python.exe scripts/live_cam_web.py
    .venv-train/Scripts/python.exe scripts/live_cam_web.py --src http://127.0.0.1:11441 --port 8898

    浏览器打开 http://127.0.0.1:8898

    同一局域网的手机/3568 用 http://<PC的IP>:8898 也能看（--host 0.0.0.0）。

路由
    /            页面
    /stream      MJPEG，循环拉上游 frame.jpg
    /api/state   上游 /v1/vision/latest + /health 合并，前端画框用
    /health      本服务自检

设计约束（都是踩过的）
    1. 本机 shell 带 http_proxy=127.0.0.1:51621，urllib 默认会走代理 → 502。
       所有上游请求固定 ProxyHandler({})。
    2. MJPEG 每帧现拉，不缓存：上游掉帧时这里跟着停，页面上的「停更」告警才有意义。
    3. 上游 11441 ≠ 前端默认的 11436（11436 被 hdc 转发占着），端点必须可配。
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>K1 摄像头实时 · 检测可视化</title>
<style>
  :root{
    --bg:#0f1115; --panel:#171a21; --line:#252a35; --txt:#e6e9ef; --dim:#8b93a4;
    --ok:#3ecf8e; --warn:#f0a02c; --bad:#e5484d; --accent:#4c8dff;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);
    font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  .wrap{max-width:1180px;margin:0 auto;padding:18px 18px 40px}
  header{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:14px}
  h1{font-size:17px;font-weight:600;margin:0;letter-spacing:.02em}
  .sub{font-size:12px;color:var(--dim)}
  .dot{width:8px;height:8px;border-radius:50%;display:inline-block;background:var(--dim)}
  .dot.live{background:var(--ok);box-shadow:0 0 0 4px rgba(62,207,142,.15)}
  .dot.stale{background:var(--bad)}
  .stage{position:relative;background:#000;border:1px solid var(--line);border-radius:10px;
    overflow:hidden;aspect-ratio:4/3;max-height:66vh;margin:0 auto}
  .stage img{width:100%;height:100%;object-fit:contain;display:block}
  .stage canvas{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
  .badge{position:absolute;left:10px;top:10px;padding:5px 10px;border-radius:6px;
    background:rgba(0,0,0,.62);font-size:13px;font-weight:600;letter-spacing:.02em;
    backdrop-filter:blur(6px)}
  .hint{position:absolute;right:10px;top:10px;padding:5px 9px;border-radius:6px;
    background:rgba(0,0,0,.55);font-size:11px;color:var(--dim)}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-top:14px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:9px;padding:10px 12px}
  .card .k{font-size:11px;color:var(--dim);margin-bottom:5px;letter-spacing:.03em}
  .card .v{font-size:19px;font-weight:600;font-variant-numeric:tabular-nums}
  .card .v.sm{font-size:15px}
  .card .v.ok{color:var(--ok)} .card .v.warn{color:var(--warn)} .card .v.bad{color:var(--bad)}
  .bar{height:5px;background:#232833;border-radius:3px;margin-top:8px;overflow:hidden}
  .bar > i{display:block;height:100%;background:var(--accent);width:0;transition:width .15s}
  .row{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;font-size:12px;color:var(--dim)}
  .row b{color:var(--txt);font-weight:600}
  .tip{margin-top:16px;padding:11px 13px;border-left:2px solid var(--accent);
    background:rgba(76,141,255,.07);border-radius:0 6px 6px 0;font-size:12.5px;line-height:1.75;color:#c3cad8}
  .tip b{color:#fff}
  @media (max-width:640px){ .card .v{font-size:16px} h1{font-size:15px} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>摄像头实时画面</h1>
    <span class="sub"><span id="live" class="dot"></span> <span id="liveTxt">连接中…</span></span>
    <span class="sub" id="srcTxt"></span>
  </header>

  <div class="stage">
    <img id="img" src="/stream" alt="实时画面">
    <canvas id="cv"></canvas>
    <div class="badge" id="badge">等待画面…</div>
    <div class="hint" id="hint">–</div>
  </div>

  <div class="cards">
    <div class="card"><div class="k">识别结果 label</div><div class="v sm" id="c_label">–</div></div>
    <div class="card"><div class="k">动作 action</div><div class="v sm" id="c_action">–</div></div>
    <div class="card"><div class="k">动作置信度</div>
      <div class="v" id="c_aconf">–</div><div class="bar"><i id="b_aconf"></i></div></div>
    <div class="card"><div class="k">检测帧率</div><div class="v" id="c_fps">–</div></div>
    <div class="card"><div class="k">推理耗时</div><div class="v" id="c_infer">–</div></div>
    <div class="card"><div class="k">端到端延迟</div><div class="v" id="c_lat">–</div></div>
  </div>

  <div class="row">
    <span>画面里：<b id="c_count">–</b> 只宠物</span>
    <span>人：<b id="c_human">–</b></span>
    <span>行为判定频率：<b id="c_bhz">–</b></span>
    <span>标签新鲜度：<b id="c_age">–</b></span>
    <span>依据：<b id="c_basis">–</b></span>
  </div>

  <div class="tip">
    <b>怎么验动作识别：</b>手机横屏放在摄像头前 1~1.5 米，播「狗奔跑 / 狗跳跃」的视频，
    让狗占到画面高度的 1/3 以上。<br>
    <b>看哪里：</b>上方 <b>action</b> 是否从 <code>absent/standing</code> 变成 <code>walking</code> /
    <code>running</code> / <code>playing</code>；框是否贴住狗。<br>
    <b>判据：</b>帧率 15+ 才有意义 —— 低于 8 fps 时 0.3~0.6 秒的奔跑会整段落在两帧之间，采不到。
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
const cv = $('cv'), img = $('img');
let lastState = null, lastStateAt = 0, lastFrameMark = 0, frameMoved = false;

function fmt(x, d, u){ return (x===null||x===undefined||isNaN(x)) ? '–' : (Number(x).toFixed(d)+u); }

function draw(){
  const dpr = window.devicePixelRatio || 1;
  const rect = cv.getBoundingClientRect();
  if (cv.width !== Math.round(rect.width*dpr) || cv.height !== Math.round(rect.height*dpr)){
    cv.width = Math.round(rect.width*dpr); cv.height = Math.round(rect.height*dpr);
  }
  const g = cv.getContext('2d');
  g.setTransform(1,0,0,1,0,0);
  g.clearRect(0,0,cv.width,cv.height);
  if(!lastState) return;

  // img 用 object-fit:contain，先算出内容区（letterbox 后的实际显示区域）
  const nw = img.naturalWidth || 4, nh = img.naturalHeight || 3;
  const boxW = rect.width, boxH = rect.height;
  const s = Math.min(boxW/nw, boxH/nh);
  const dw = nw*s, dh = nh*s;
  const ox = (boxW-dw)/2, oy = (boxH-dh)/2;
  g.scale(dpr,dpr);
  const k = dw/nw;   // 原图坐标 → 屏幕坐标

  const palette = { cat:'#4c8dff', dog:'#3ecf8e', bird:'#c792ea', person:'#f0a02c' };
  const items = [];
  (lastState.all || []).forEach(o => items.push({box:o.bbox||o.box, name:(o.pet||'pet'), conf:o.conf, tag:o.label||o.action, color:'#3ecf8e'}));
  if (lastState.human && lastState.human.present && lastState.human.bbox)
    items.push({box:lastState.human.bbox, name:'person', conf:lastState.human.conf,
                tag:'human', color:'#f0a02c', dashed:true});

  items.forEach(it => {
    const b = it.box; if(!b || b.length<4) return;
    const x = ox + b[0]*k, y = oy + b[1]*k, w = (b[2]-b[0])*k, h = (b[3]-b[1])*k;
    g.lineWidth = 2; g.strokeStyle = it.color; g.setLineDash(it.dashed?[6,5]:[]);
    g.strokeRect(x,y,w,h); g.setLineDash([]);
    const txt = it.name + ' ' + (it.conf*100).toFixed(0) + '%' + (it.tag && it.tag!==it.name ? ' · '+it.tag : '');
    g.font = '600 12px -apple-system,"Segoe UI",sans-serif';
    const tw = g.measureText(txt).width + 12;
    g.fillStyle = it.color; g.fillRect(x, Math.max(oy+2, y-19), tw, 19);
    g.fillStyle = '#0f1115'; g.fillText(txt, x+6, Math.max(oy+2+14, y-5));
  });
}

async function tick(){
  try{
    const r = await fetch('/api/state', {cache:'no-store'});
    const d = await r.json();
    lastState = d.latest || null;
    const h = d.health || {};

    const fresh = d.age_sec !== null && d.age_sec < 1.5;
    $('live').className = 'dot ' + (fresh ? 'live' : 'stale');
    $('liveTxt').textContent = fresh ? ('实时 · 上游 ' + (d.src||'')) : ('停更 ' + (d.age_sec==null?'?':d.age_sec.toFixed(1)+'s'));
    $('srcTxt').textContent = h.mode ? ('模式 ' + h.mode) : '';

    if(lastState){
      const pet = lastState.pet, lbl = lastState.label, act = lastState.action;
      $('c_label').textContent = pet ? (pet + ' / ' + lbl) : '画面无宠物';
      $('c_label').className = 'v sm ' + (pet ? 'ok' : 'warn');
      const aSet = ['walking','running','playing','jumping'];
      $('c_action').textContent = act || '–';
      $('c_action').className = 'v sm ' + (aSet.includes(act) ? 'ok' : '');
      $('c_aconf').textContent = fmt(lastState.action_conf,2,'');
      $('c_aconf').className = 'v ' + (lastState.action_conf>=0.6?'ok':'warn');
      $('b_aconf').style.width = Math.round((lastState.action_conf||0)*100) + '%';
      $('c_fps').textContent = fmt(h.det_fps ?? lastState.det_fps,1,' fps');
      $('c_fps').className = 'v ' + (((h.det_fps ?? lastState.det_fps)||0) >= 12 ? 'ok' : 'bad');
      $('c_infer').textContent = fmt(h.infer_ms ?? lastState.infer_ms,0,' ms');
      $('c_lat').textContent = fmt(lastState.latency_ms,0,' ms');
      $('c_count').textContent = (lastState.count ?? 0);
      $('c_human').textContent = lastState.human && lastState.human.present
          ? ('有 ' + (lastState.human.count||1) + ' 人 ' + Math.round((lastState.human.conf||0)*100) + '%') : '无';
      $('c_bhz').textContent = fmt(lastState.behavior_hz ?? h.behavior_hz,0,' Hz');
      $('c_age').textContent = fmt(lastState.label_age_ms,0,' ms');
      $('c_basis').textContent = (lastState.basis || '–').slice(0,60);
      $('badge').textContent = (pet ? pet.toUpperCase() : '无宠物') + '  ·  ' + (act||'–');
      $('badge').style.color = pet ? '#3ecf8e' : '#f0a02c';
    }
    lastStateAt = Date.now();
    draw();
  }catch(e){
    $('live').className = 'dot stale';
    $('liveTxt').textContent = '页面服务断了';
  }
}
setInterval(tick, 150);
tick();
img.addEventListener('load', draw);
window.addEventListener('resize', draw);
</script>
</body>
</html>
"""


def http_get(url, timeout=3.0):
    req = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with _OPENER.open(req, timeout=timeout) as r:
        return r.read()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    src = "http://127.0.0.1:11441"
    verbose = True

    def log_message(self, fmt, *a):
        if self.verbose:
            sys.stderr.write("[web] %s\n" % (fmt % a))

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")

        if path == "/health":
            return self._send(200, {"ok": True, "src": self.src, "ts": time.time()})

        if path == "/api/state":
            latest, health, err = None, {}, None
            try:
                latest = json.loads(http_get(self.src + "/v1/vision/latest"))
            except Exception as e:
                err = "latest: %s" % e
            try:
                health = json.loads(http_get(self.src + "/health"))
            except Exception as e:
                err = (err + " | " if err else "") + "health: %s" % e
            age = None
            if latest and latest.get("ts"):
                age = max(0.0, time.time() - float(latest["ts"]))
            return self._send(200 if latest else 502,
                              {"latest": latest, "health": health,
                               "age_sec": age, "src": self.src, "error": err})

        if path == "/stream":
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            last = b""
            try:
                while True:
                    try:
                        jpeg = http_get(self.src + "/v1/vision/frame.jpg", timeout=2.0)
                    except Exception:
                        time.sleep(0.05)
                        continue
                    # 上游一帧没变时不要重复推：省带宽，也让浏览器的“动”等于“真的有新帧”
                    if jpeg and jpeg == last:
                        time.sleep(0.01)
                        continue
                    last = jpeg
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                     b"Content-Length: " + str(len(jpeg)).encode()
                                     + b"\r\n\r\n" + jpeg + b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        return self._send(404, {"ok": False, "reason": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="http://127.0.0.1:11441",
                    help="推理服务地址（PC 本地模式默认 11441；K1 直连写 http://192.168.88.10:11436）")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8898)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    Handler.src = args.src.rstrip("/")
    Handler.verbose = not args.quiet

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print("=" * 62)
    print(f"  摄像头实时画面  http://127.0.0.1:{args.port}")
    print(f"  上游推理服务    {Handler.src}")
    print("=" * 62, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[stop] bye", flush=True)


if __name__ == "__main__":
    main()
