#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC 侧 11438 LLM 网关（K1 离线时的临时替身）。

── 它是什么，不是什么 ──────────────────────────────────────────────
这是**替身**，不是 K1。K1 上真身是 deploy/llm_gateway.py，跑 qwen3:0.6b，
会在转发前后暂停视觉推理循环以避免内存带宽争抢。K1 离线期间，模拟器
需要有人应答 11438，否则聊天页永远停在「正在思考…」，前端界面就没法验证。

── 为什么后端是云端 DeepSeek 而不是本地模型 ────────────────────────
PC 上没有 ollama，也没有 0.6B 权重。要现场拉一份 llama.cpp + GGUF 要
下载几百 MB，且第一次加载就要十几秒 —— 只为验证 UI 不值得。
DeepSeek 在这里只承担「把事实说成人话」这一步，事实本身仍由前端规则层判。

── 诚实性要求（重要，不要糊弄）────────────────────────────────────
前端气泡会按 source 标「本地模型」。用本网关时那句话**不是**本地模型
产出的。所以：
  1. /health 明确返回 engine=pc-mock、backend=deepseek；
  2. 每次请求都在 stdout 打印 [MOCK] 前缀；
  3. 交付说明里必须写清楚当前 LLM 来自 PC 替身。
K1 回来后把 TARGET 切 board，前端自然打到真身，这个脚本就该关掉。

用法：
    .venv-train/Scripts/python.exe scripts/pc_llm_gateway.py
    .venv-train/Scripts/python.exe scripts/pc_llm_gateway.py --port 11438 --backend deepseek

    # 探活
    curl http://127.0.0.1:11438/health
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# DeepSeek 凭证只从命令行或环境变量取，源码里不留默认值：
#     export DEEPSEEK_API_KEY=sk-xxx   （或 --key 传入）
DEFAULT_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEFAULT_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"

# 本地 ollama。K1 上跑的就是 qwen3:0.6b，PC 上跑同一份权重时用这组配置，
# 这样前后端契约、模型名、温度上限全都对得上，切换只改 --backend 一个参数。
OLLAMA_URL = "http://127.0.0.1:11434/v1/chat/completions"
OLLAMA_MODEL = "qwen3:0.6b"

# 本地 transformers 直跑。K1 上 qwen3:0.6b 走 ollama，PC 上没有 ollama 时
# 用同一份权重（Qwen3-0.6B）在 CUDA 上跑，模型本身一致，只是宿主不同。
HF_MODEL = "Qwen/Qwen3-0.6B"
# GGUF 权重路径（llama.cpp 用 mmap 加载，不占 Windows 提交内存）
GGUF_PATH = r"E:\CIE-RISC-V\models\Qwen3-0.6B-Q8_0.gguf"

STATE = {
    "api_key": os.environ.get("DEEPSEEK_API_KEY", DEFAULT_KEY),
    "api_url": os.environ.get("DEEPSEEK_API_URL", DEFAULT_URL),
    "model": DEFAULT_MODEL,
    "backend": "deepseek",
    # 对外声明的「我在哪跑」。k1=板端真身；local-ollama=PC 本地 ollama；
    # pc-mock=云端替身。前端据此决定标注写「K1 本地模型」还是别的。
    "engine": "pc-mock",
    "requests": 0,
    "errors": 0,
    "last_ms": 0.0,
    "last_error": "",
}


def log(msg: str) -> None:
    print(f"[MOCK] {msg}", flush=True)


# ── 规则兜底：云端不可用时用事实串拼一句，绝不让界面卡住 ────────────────
def rule_fallback(user_text: str) -> str:
    """从 user 消息里抽出「数据：...。问：」之间的部分，按事实给结论。

    这段刻意写得很朴素：它只是兜底，不假装是模型分析。
    """
    data = ""
    if "数据：" in user_text:
        seg = user_text.split("数据：", 1)[1]
        data = seg.split("问：", 1)[0].strip()
    if not data:
        return "当前没有拿到有效数据，没法判断它现在的状态。"
    has_pet = ("猫" in data or "犬" in data or "狗" in data)
    bits: list[str] = []
    if "未接入" in data:
        bits.append("部分数据还没接入")
    if has_pet:
        bits.append("画面里有宠物")
    if "没有宠物" in data:
        bits.append("画面里暂时没有宠物")
    if "偏高" in data or "超标" in data or "偏低" in data:
        bits.append("环境有指标偏离舒适区，建议调整")
    if not bits:
        bits.append("各项看着正常，继续观察")
    return "，".join(bits) + "。"


# ── GGUF / llama.cpp 推理（内存紧张时的首选路径）───────────────────────
# 为什么优先 GGUF 而不是 transformers：
#   这台机器同时跑着视觉推理 + 模拟器 + IDE，系统「提交可用」长期只剩
#   1~2 GB。transformers 加载 1.5 GB safetensors 时会在 CPU 侧建完整副本，
#   直接 OSError 1455「页面文件太小」，且没有管理员权限改不了页面文件。
#   GGUF 走 mmap，文件页按需换入，几乎不占 commit —— 同一个模型能跑起来。
_GGUF = {"llm": None, "lock": threading.Lock()}


def ensure_gguf() -> None:
    if _GGUF["llm"] is not None:
        return
    with _GGUF["lock"]:
        if _GGUF["llm"] is not None:
            return
        from llama_cpp import Llama
        path = STATE["model"]
        log(f"加载 GGUF {path} ...")
        t0 = time.time()
        # n_gpu_layers=0 → 纯 CPU。没装 CUDA 版 llama-cpp 时设非 0 会直接报错。
        llm = Llama(
            model_path=path,
            n_ctx=2048,
            n_threads=max(4, (os.cpu_count() or 8) - 4),
            n_gpu_layers=0,
            verbose=False,
        )
        _GGUF["llm"] = llm
        log(f"GGUF 就绪，耗时 {time.time() - t0:.1f}s")


def call_gguf(payload: dict) -> dict:
    ensure_gguf()
    llm = _GGUF["llm"]
    msgs = list(payload.get("messages", []))
    # Qwen3 系推理模型默认会先输出思考块。0.6B 想完再答反而更差，
    # 而且思考文本会被前端「复述提示词」检测误判。用 /no_think 关掉。
    if msgs:
        last = dict(msgs[-1])
        if last.get("role") == "user" and "/no_think" not in str(last.get("content", "")):
            last["content"] = str(last.get("content", "")) + " /no_think"
            msgs[-1] = last
    res = llm.create_chat_completion(
        messages=msgs,
        max_tokens=max(8, int(payload.get("max_tokens", 40) or 40)),
        temperature=max(float(payload.get("temperature", 0.3) or 0.3), 0.01),
        top_p=0.9,
    )
    # llama_cpp 返回的就是 OpenAI 兼容结构，补个 id 即可
    if isinstance(res, dict):
        res.setdefault("id", f"gguf-{int(time.time() * 1000)}")
        res.setdefault("object", "chat.completion")
        res.setdefault("created", int(time.time()))
        res.setdefault("model", "qwen3:0.6b")
    return res


# ── 本地 transformers 推理（PC 上没有 ollama 时的路径）─────────────────
_HF = {"tok": None, "model": None, "lock": threading.Lock()}


def ensure_hf() -> None:
    """首次调用时加载模型并常驻显存。冷加载约 5~15 秒。"""
    if _HF["model"] is not None:
        return
    with _HF["lock"]:
        if _HF["model"] is not None:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        mid = STATE["model"]
        log(f"加载本地模型 {mid} ...")
        t0 = time.time()
        tok = AutoTokenizer.from_pretrained(mid)
        # low_cpu_mem_usage 必须开：这台机器同时跑着视觉推理 + 模拟器，
        # 系统「提交可用」经常只剩 1~2 GB，默认加载会在 CPU 侧留完整副本，
        # 直接报 OSError 1455「页面文件太小」。开它之后权重直接流到显存，
        # CPU 侧不再整份驻留。
        kw = dict(device_map="cuda", low_cpu_mem_usage=True)
        try:
            model = AutoModelForCausalLM.from_pretrained(mid, dtype=torch.bfloat16, **kw)
        except TypeError:      # 老版 transformers 只认 torch_dtype
            model = AutoModelForCausalLM.from_pretrained(mid, torch_dtype=torch.bfloat16, **kw)
        model.eval()
        _HF["tok"] = tok
        _HF["model"] = model
        log(f"模型就绪，耗时 {time.time() - t0:.1f}s")


def call_hf(payload: dict) -> dict:
    """用本地 transformers 跑一次对话，返回 OpenAI schema。"""
    import torch
    ensure_hf()
    tok = _HF["tok"]
    model = _HF["model"]
    msgs = payload.get("messages", [])
    # qwen3 的 chat template 支持关思考块。必须关：0.6B 想完再答反而更差，
    # 而且思考文本会被前端「复述提示词」检测误判成退化。
    try:
        prompt = tok.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    except TypeError:
        prompt = tok.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=True)
    inputs = tok(prompt, return_tensors="pt").to(model.device)
    max_new = max(8, int(payload.get("max_tokens", 40) or 40))
    temp = float(payload.get("temperature", 0.3) or 0.3)
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=max_new,
            do_sample=temp > 0.01,
            temperature=max(temp, 0.01),
            top_p=0.9,
            repetition_penalty=1.05,
            pad_token_id=tok.eos_token_id,
        )
    gen_ids = out[0][inputs["input_ids"].shape[1]:]
    text = tok.decode(gen_ids, skip_special_tokens=True).strip()
    p_tok = int(inputs["input_ids"].shape[1])
    c_tok = int(gen_ids.shape[0])
    return {
        "id": f"hf-{int(time.time() * 1000)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": STATE["model"],
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok,
                  "total_tokens": p_tok + c_tok},
    }


def call_upstream(payload: dict) -> dict:
    """转发到当前后端，返回 OpenAI schema 原样的响应。

    ollama 与 DeepSeek 都吃 OpenAI 兼容格式，所以只有 model 与鉴权头不同。
    """
    body = {
        "model": STATE["model"],
        # 前端的 model 写的是 qwen3:0.6b；这里按后端换成真实模型名，
        # messages/temperature/max_tokens 原样透传，保持与前端的约定一致。
        "messages": payload.get("messages", []),
        "stream": False,
        "temperature": float(payload.get("temperature", 0.3) or 0.3),
        "max_tokens": int(payload.get("max_tokens", 40) or 40),
    }
    if STATE["backend"] == "ollama":
        # qwen3 默认会把思考过程写进 content，前端「复述提示词」检测会误伤，
        # 而且 0.6B 想多了答得更差。显式关掉。
        body["think"] = False
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if STATE["api_key"]:
        headers["Authorization"] = f"Bearer {STATE['api_key']}"
    req = urllib.request.Request(
        STATE["api_url"], data=data, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 关掉默认逐请求日志，太吵
        return

    # ── 响应工具 ──────────────────────────────────────────────────
    def _json(self, code: int, obj) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        # 模拟器/板端直连，放开跨域，省得再排查 CORS
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self._json(200, {"ok": True})

    # ── 探活 ─────────────────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            self._json(200, {
                "ok": True,
                "service": "llm-gateway",
                "engine": STATE["engine"],     # ← 前端据此决定来源标注
                "backend": STATE["backend"],
                "model": STATE["model"],
                "requests": STATE["requests"],
                "errors": STATE["errors"],
                "last_ms": round(STATE["last_ms"], 1),
                "last_error": STATE["last_error"],
                "note": "k1=板端真身；local-ollama=PC 本地 ollama；pc-mock=云端替身",
            })
            return
        if path == "/v1/models":
            self._json(200, {
                "object": "list",
                "data": [{"id": "qwen3:0.6b", "object": "model", "owned_by": "pc-mock"}],
            })
            return
        self._json(404, {"ok": False, "reason": "not found"})

    # ── 聊天 ─────────────────────────────────────────────────────
    def do_POST(self):
        path = self.path.split("?")[0]
        if path != "/v1/chat/completions":
            self._json(404, {"ok": False, "reason": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:  # noqa: BLE001
            self._json(400, {"ok": False, "reason": f"bad body: {e}"})
            return

        STATE["requests"] += 1
        rid = STATE["requests"]
        msgs = payload.get("messages", [])
        user_text = ""
        for m in reversed(msgs):
            if m.get("role") == "user":
                user_text = m.get("content", "")
                break

        t0 = time.time()
        try:
            if STATE["backend"] == "gguf":
                out = call_gguf(payload)
            elif STATE["backend"] == "hf":
                out = call_hf(payload)
            else:
                out = call_upstream(payload)
            used = time.time() - t0
            STATE["last_ms"] = used * 1000.0
            text = ""
            try:
                text = out["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError):
                text = ""
            if not text.strip():
                raise ValueError("空输出")
            log(f"#{rid} {STATE['backend']} {used*1000:.0f}ms → {text[:40]}")
            # 补上 engine 标记，方便日后抓包区分是板端、本地还是替身
            out.setdefault("x_engine", STATE["engine"])
            self._json(200, out)
            return
        except Exception as e:  # noqa: BLE001
            STATE["errors"] += 1
            STATE["last_error"] = f"{type(e).__name__}: {e}"
            used = time.time() - t0
            log(f"#{rid} 云端失败({STATE['last_error']})，回退规则")
            # 仍然返回 200 + 合法 OpenAI 结构：让前端走正常解析路径，
            # 由前端自己的退化检测决定是否再降级，而不是在这里造 5xx。
            text = rule_fallback(user_text)
            self._json(200, {
                "id": f"mock-{rid}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "pc-mock-rule",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                "x_engine": "pc-mock-rule",
            })


def main() -> int:
    ap = argparse.ArgumentParser(description="PC 侧 11438 LLM 网关替身")
    ap.add_argument("--host", default="0.0.0.0", help="监听地址，默认全部网卡（模拟器要能访问）")
    ap.add_argument("--port", type=int, default=11438)
    ap.add_argument("--backend", default="gguf",
                    choices=["gguf", "hf", "ollama", "deepseek", "rule"],
                    help="gguf=PC本地 llama.cpp 跑 GGUF（默认，省内存）；"
                         "hf=PC本地 transformers；ollama=PC 本地 ollama；"
                         "deepseek=云端；rule=纯规则")
    ap.add_argument("--model", default="", help="留空则按 backend 取默认模型名/路径")
    ap.add_argument("--api-url", default="", help="留空则按 backend 取默认地址")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--ollama-url", default=OLLAMA_URL)
    args = ap.parse_args()

    STATE["backend"] = args.backend
    if args.backend == "gguf":
        STATE["engine"] = "local-gguf"
        STATE["model"] = args.model or GGUF_PATH
        STATE["api_url"] = "(in-process llama.cpp)"
        STATE["api_key"] = ""
    elif args.backend == "hf":
        STATE["engine"] = "local-hf"
        STATE["model"] = args.model or HF_MODEL
        STATE["api_url"] = "(in-process transformers)"
        STATE["api_key"] = ""
    elif args.backend == "ollama":
        STATE["engine"] = "local-ollama"
        STATE["api_url"] = args.api_url or args.ollama_url
        STATE["model"] = args.model or OLLAMA_MODEL
        STATE["api_key"] = ""            # 本机 ollama 不校验鉴权头
    elif args.backend == "deepseek":
        STATE["engine"] = "pc-mock"
        STATE["api_url"] = args.api_url or DEFAULT_URL
        STATE["model"] = args.model or DEFAULT_MODEL
        STATE["api_key"] = args.api_key or DEFAULT_KEY
    else:
        # 纯规则模式：把地址指向一个必然连接失败的端口，走兜底分支
        STATE["engine"] = "pc-mock-rule"
        STATE["api_url"] = "http://127.0.0.1:1/never"
        STATE["model"] = "rule"
        STATE["api_key"] = ""

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("=" * 66)
    print("  11438 LLM 网关")
    print(f"  监听    : http://{args.host}:{args.port}")
    print(f"  后端    : {STATE['backend']} / {STATE['model']}")
    print(f"  上游    : {STATE['api_url']}")
    print(f"  engine  : {STATE['engine']}  (前端据此标注来源)")
    print(f"  探活    : curl http://127.0.0.1:{args.port}/health")
    print("=" * 66)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[stop] 收到中断，退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
