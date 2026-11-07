# -*- coding: utf-8 -*-
"""K1 视觉实时预览（只读转发，不占用摄像头）。

为什么需要它：
  原 yolo-server/templates/index.html 用 getUserMedia 抓 PC 摄像头，
  但摄像头已被 pc_cam_vision.py 独占推流到 K1，浏览器再抢必然失败。
  本服务改为从 K1 的 /v1/vision/frame.jpg 取「K1 正在分析的那一帧」，
  不动摄像头、不影响推流，看到的就是识别引擎真实输入。

用法：
    python scripts/k1_live_view.py            # 默认 http://127.0.0.1:8899
    python scripts/k1_live_view.py --port 9000
"""
from __future__ import annotations

import argparse
import io
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw, ImageFont

K1_DEFAULT = "http://127.0.0.1:11436"
FONT_BOLD = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_REG = r"C:\Windows\Fonts\msyh.ttc"

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_state = {
    "base": K1_DEFAULT,
    "snap": {},
    "snap_ts": 0.0,
    "frame_ts": 0.0,
}
_lock = threading.Lock()

_font_cache: dict[int, ImageFont.FreeTypeFont] = {}


def _font(size: int, bold: bool = True):
    key = size + (1000 if bold else 0)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)
    return _font_cache[key]


def _get(path: str, timeout: float = 5.0) -> bytes:
    with _opener.open(_state["base"] + path, timeout=timeout) as r:
        return r.read()


def _refresh_snapshot():
    """后台线程：保持 latest 新鲜（框坐标用它，避免每帧都请求）"""
    while True:
        try:
            snap = json.loads(_get("/v1/vision/latest", 4).decode("utf-8"))
            with _lock:
                _state["snap"] = snap
                _state["snap_ts"] = time.time()
        except Exception:
            pass
        time.sleep(0.4)


def _draw(jpeg: bytes) -> bytes:
    """在帧上画检测框 + 25% 参考线，让用户直接看到取景关系"""
    try:
        im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    except Exception:
        return jpeg
    W, H = im.size
    d = ImageDraw.Draw(im)
    with _lock:
        snap = dict(_state["snap"])

    # 25% 高度参考线（目标要越过这条线才检得出）
    y = int(H * (1 - 0.25))
    for x in range(0, W, 24):
        d.line([(x, y), (min(x + 12, W), y)], fill=(40, 230, 120), width=2)
    d.text((8, y - 20), f"pet >= 25% ({int(H * 0.25)}px) above this line",
           font=_font(13, False), fill=(40, 230, 120))

    for det in (snap.get("all") or []):
        b = det.get("bbox")
        if not b:
            continue
        d.rectangle([b[0], b[1], b[2], b[3]], outline=(255, 176, 32), width=3)
        d.text((b[0] + 4, max(0, b[1] - 18)),
               f"{det.get('type', 'pet')} {det.get('conf', 0):.2f}",
               font=_font(15), fill=(255, 176, 32))

    hum = snap.get("human") or {}
    if hum.get("present") and hum.get("bbox"):
        b = hum["bbox"]
        d.rectangle([b[0], b[1], b[2], b[3]], outline=(0, 210, 255), width=2)
        d.text((b[0] + 4, max(0, b[1] - 18)),
               f"person {hum.get('conf', 0):.2f}", font=_font(15), fill=(0, 210, 255))

    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return buf.getvalue()


PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>K1 视觉实时预览</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; background:#0f1216; color:#e8edf3;
         font-family: "Microsoft YaHei", system-ui, sans-serif; }
  .wrap { max-width:1180px; margin:0 auto; padding:18px 18px 40px; }
  h1 { font-size:19px; margin:0 0 4px; font-weight:600; }
  .sub { color:#8b96a5; font-size:13px; margin-bottom:16px; }
  .row { display:flex; gap:16px; flex-wrap:wrap; align-items:flex-start; }
  .stage { flex:1 1 620px; min-width:320px; }
  .frame { position:relative; background:#000; border-radius:10px; overflow:hidden;
           border:1px solid #242b34; aspect-ratio:4/3; }
  .frame img { width:100%; height:100%; object-fit:contain; display:block; }
  .badge { position:absolute; top:10px; left:10px; background:rgba(0,0,0,.62);
           padding:5px 10px; border-radius:6px; font-size:12px; color:#9fe8c0; }
  .panel { flex:0 0 330px; min-width:300px; }
  .card { background:#161b22; border:1px solid #242b34; border-radius:10px;
          padding:14px 16px; margin-bottom:12px; }
  .card h2 { font-size:13px; color:#8b96a5; margin:0 0 10px; font-weight:600;
             letter-spacing:.5px; }
  .big { font-size:26px; font-weight:700; margin:2px 0 6px; }
  .kv { display:flex; justify-content:space-between; font-size:13px;
        padding:5px 0; border-bottom:1px dashed #222a34; }
  .kv:last-child { border-bottom:none; }
  .kv span:first-child { color:#8b96a5; }
  .ok { color:#39d98a; } .no { color:#ff8a5c; } .dim { color:#6b7684; }
  .tip { font-size:12.5px; line-height:1.7; color:#9aa6b4; }
  .tip b { color:#ffd166; }
</style></head>
<body><div class="wrap">
  <h1>K1 视觉实时预览</h1>
  <div class="sub">这是 K1 推理引擎实际收到的那一帧（只读转发，不占用摄像头）</div>
  <div class="row">
    <div class="stage">
      <div class="frame">
        <img id="v" src="/stream" alt="live">
        <div class="badge" id="live">● LIVE</div>
      </div>
    </div>
    <div class="panel">
      <div class="card">
        <h2>宠物检测</h2>
        <div class="big" id="petBig">—</div>
        <div class="kv"><span>类型</span><span id="petType">—</span></div>
        <div class="kv"><span>动作</span><span id="petAct">—</span></div>
        <div class="kv"><span>置信度</span><span id="petConf">—</span></div>
      </div>
      <div class="card">
        <h2>画面中的人</h2>
        <div class="kv"><span>数量</span><span id="humN">—</span></div>
        <div class="kv"><span>置信度</span><span id="humC">—</span></div>
      </div>
      <div class="card">
        <h2>引擎</h2>
        <div class="kv"><span>fps</span><span id="fps">—</span></div>
        <div class="kv"><span>单帧推理</span><span id="infer">—</span></div>
        <div class="kv"><span>动作特征</span><span id="basis" style="font-size:11px">—</span></div>
      </div>
      <div class="card">
        <div class="tip">
          让宠物<b>整只高于绿线</b>（占画面高 25% 以上）才会被检出。<br>
          低于这条线时置信度会掉到 0，调什么参数都没用。<br>
          距离参考：柯基/小型犬约 <b>1 米内</b>，猫约 <b>0.6 米内</b>。
        </div>
      </div>
    </div>
  </div>
</div>
<script>
async function tick() {
  try {
    const r = await fetch('/api/status', {cache:'no-store'});
    const d = await r.json();
    const n = d.count ?? 0;
    const petType = d.pet || '';
    const big = document.getElementById('petBig');
    if (n > 0) {
      big.textContent = (petType === 'cat' ? '猫' : petType === 'dog' ? '狗' : '宠物') + ' ×' + n;
      big.className = 'big ok';
    } else {
      big.textContent = '未检测到';
      big.className = 'big no';
    }
    const cn = {sitting:'坐着', standing:'站立', walking:'走动', running:'奔跑',
                jumping:'跳跃', playing:'玩耍', lying:'趴着', absent:'不在画面'};
    document.getElementById('petType').textContent = petType || '—';
    document.getElementById('petAct').textContent = cn[d.label] || d.label || '—';
    document.getElementById('petConf').textContent =
      n > 0 ? (d.conf * 100).toFixed(1) + '%' : '—';
    const h = d.human || {};
    document.getElementById('humN').textContent = h.present ? (h.count + ' 个') : '无';
    document.getElementById('humC').textContent =
      h.present ? (h.conf * 100).toFixed(0) + '%' : '—';
    document.getElementById('fps').textContent = d.fps ?? '—';
    document.getElementById('infer').textContent = (d.infer_ms ?? '—') + ' ms';
    document.getElementById('basis').textContent = d.basis || '—';
    document.getElementById('live').textContent = '● LIVE';
  } catch (e) {
    document.getElementById('live').textContent = '● 取不到画面';
  }
}
setInterval(tick, 500); tick();
document.getElementById('v').onerror = () => {
  setTimeout(() => { document.getElementById('v').src = '/stream?t=' + Date.now(); }, 1500);
};
</script>
</body></html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        p = self.path.split("?")[0]

        if p == "/":
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

        if p == "/api/status":
            with _lock:
                snap = dict(_state["snap"])
                ts = _state["snap_ts"]
            if not snap or time.time() - ts > 6:
                snap = {"ok": False, "reason": "stale"}
            return self._send(200, json.dumps(snap, ensure_ascii=False).encode("utf-8"),
                              "application/json; charset=utf-8")

        if p == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                try:
                    jpg = _get("/v1/vision/frame.jpg", 4)
                except Exception:
                    time.sleep(0.5)
                    continue
                out = _draw(jpg)
                try:
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(out)}\r\n\r\n".encode())
                    self.wfile.write(out)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
                time.sleep(0.15)

        return self._send(404, b"not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=K1_DEFAULT)
    ap.add_argument("--port", type=int, default=8899)
    args = ap.parse_args()

    _state["base"] = args.base
    threading.Thread(target=_refresh_snapshot, daemon=True).start()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    print(f"预览已启动: http://127.0.0.1:{args.port}  (源 {args.base})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
