#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
宠物本地知识库 —— 检索引擎 + 回答策略
零第三方依赖，只用 Python 标准库，可在 K1 板端直接运行。

用法：
  python3 rag.py "猫能喝牛奶吗"         # 检索 + 按策略回答
  python3 rag.py --raw "猫能喝牛奶吗"   # 只看检索结果，不调模型
  python3 rag.py --serve                # 启动 HTTP 服务，端口 11435
  python3 rag.py --eval                 # 跑内置检索评测

回答策略（三级，核心是防止小模型篡改事实）：
  1. risk=high（医疗/中毒/急救）      → 直接返回知识原文，模型完全不参与
  2. 检索得分 >= HARD_HIT（高置信）   → 直接返回知识原文
  3. HARD_HIT > 得分 >= MIN_HIT       → 模型改写，但改完要做数字校验，
                                         出现资料里没有的数字就回退原文
  4. 得分 < MIN_HIT                   → 明确回答"知识库没有"，不编
"""

import json
import math
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KB_PATH = os.path.join(HERE, "pet_kb.json")
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen3:0.6b"

MIN_HIT = float(os.environ.get("KB_MIN_HIT", 7.0))     # 低于此分视为未命中
# 默认 1000 = 永不进入模型改写。实测 0.6B 会把「资料说葡萄有毒」理解成「能吃」，
# 改写的风险远大于收益，因此默认全部走原文直出；需要实验时用 --rewrite 显式打开。
HARD_HIT = float(os.environ.get("KB_HARD_HIT", 1000.0))

# 这些词是提问模板/泛称，不承载具体内容，不能作为关键词加分依据
STOP_KEYS = {
    "猫", "狗", "犬", "宠物", "猫狗", "怎么办", "症状", "多少", "多久",
    "什么", "可以", "能不能", "怎么", "如何", "正常", "问题",
    "多少度", "几度", "哪个", "什么样", "多少钱", "一只", "一袋", "要紧",
}


# ---------------------------------------------------------------- 分词
def tokenize(text):
    """中文按字符 bigram，英文数字按整词。中文没有空格，bigram 对短问句效果好。"""
    s = text.lower()
    s = re.sub(r"[^\u4e00-\u9fff0-9a-z]+", " ", s)
    toks = []
    for seg in s.split():
        if re.fullmatch(r"[0-9a-z]+", seg):
            toks.append(seg)
            continue
        if len(seg) == 1:
            toks.append(seg)
        for i in range(len(seg) - 1):
            toks.append(seg[i : i + 2])
    return toks


def norm(text):
    return re.sub(r"[^\u4e00-\u9fff0-9a-z]", "", text.lower())


# ---------------------------------------------------------------- 检索
class Retriever:
    def __init__(self, entries):
        self.entries = entries
        self.docs = []
        for e in entries:
            blob = e["q"] + " " + " ".join(e.get("keys", [])) + " " + e["a"]
            self.docs.append(tokenize(blob))
        self.N = len(self.docs)
        self.avgdl = sum(len(d) for d in self.docs) / max(self.N, 1)

        self.df = {}
        for d in self.docs:
            for t in set(d):
                self.df[t] = self.df.get(t, 0) + 1
        self.idf = {
            t: math.log(1 + (self.N - df + 0.5) / (df + 0.5))
            for t, df in self.df.items()
        }
        self.tf = []
        for d in self.docs:
            c = {}
            for t in d:
                c[t] = c.get(t, 0) + 1
            self.tf.append(c)

    def score(self, query, k1=1.4, b=0.72):
        seen, qterms = set(), []
        for t in tokenize(query):
            if t not in seen:
                seen.add(t)
                qterms.append(t)

        qn = norm(query)
        out = []
        for i, e in enumerate(self.entries):
            s = 0.0
            dl = len(self.docs[i])
            for t in qterms:
                f = self.tf[i].get(t, 0)
                if not f:
                    continue
                idf = self.idf.get(t, 0.0)
                s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / self.avgdl))
            # 关键词命中加分：只用长度>=2 且有区分度的词，最多算 3 个
            hits = [
                kw
                for kw in e.get("keys", [])
                if kw and len(norm(kw)) >= 2 and kw not in STOP_KEYS and norm(kw) in qn
            ]
            if hits:
                s += min(len(hits), 3) * 1.5
            out.append((round(s, 2), i))
        out.sort(reverse=True)
        return out


# ---------------------------------------------------------------- 模型
def call_model(prompt, num_predict=120, temperature=0.2):
    body = json.dumps(
        {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"num_predict": num_predict, "temperature": temperature},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        j = json.loads(r.read().decode("utf-8"))
    return (j.get("message") or {}).get("content", "").strip()


# ---------------------------------------------------------------- 校验
def nums(text):
    return re.findall(r"\d+(?:\.\d+)?", text)


def verify_numbers(answer, sources):
    """答案里出现的每个数字必须能在资料里找到，否则视为篡改。"""
    src = " ".join(sources)
    bad = [n for n in nums(answer) if n not in src]
    return bad


# ---------------------------------------------------------------- 主流程
def answer(question, topk=2, allow_model=True, debug=False):
    hits = retriever.score(question)[:topk]
    top_score, top_idx = hits[0]
    entry = retriever.entries[top_idx]
    result = {
        "question": question,
        "hit": top_score >= MIN_HIT,
        "score": top_score,
        "id": entry["id"],
        "cat": entry["cat"],
        "title": entry["q"],
        "source_answer": entry["a"],
        "mode": None,
        "answer": None,
        "warn": None,
        "candidates": [
            {"id": retriever.entries[i]["id"], "score": s, "q": retriever.entries[i]["q"]}
            for s, i in hits
        ],
    }

    if not result["hit"]:
        result["mode"] = "miss"
        result["answer"] = (
            "知识库里没有找到相关内容。为避免给出错误信息，我不做推测，"
            "建议咨询兽医。"
        )
        return result

    if entry.get("risk") == "high":
        result["mode"] = "strict"
        result["answer"] = entry["a"]
        result["warn"] = "医疗/中毒类条目，直接返回知识库原文，未经模型改写"
        return result

    if top_score >= HARD_HIT or not allow_model:
        result["mode"] = "strict"
        result["answer"] = entry["a"]
        return result

    # 低风险 + 中等置信 → 让模型改写，但严格约束并校验数字
    ctx = "\n".join(f"{i + 1}. {retriever.entries[idx]['a']}" for i, (s, idx) in enumerate(hits))
    prompt = (
        "你是宠物知识问答模块。只能依据下面【资料】回答，不得使用资料以外的知识，"
        "不得补充资料里没有的数字、结论或建议。\n"
        f"【资料】\n{ctx}\n"
        f"【问题】{question}\n"
        "要求：用一到两句话回答，先给结论。如果资料里没有答案，只输出：资料不足。"
    )
    try:
        out = call_model(prompt)
    except Exception as ex:  # 模型不可用就退原文
        result["mode"] = "strict"
        result["answer"] = entry["a"]
        result["warn"] = f"模型调用失败({ex})，已回退知识原文"
        return result

    bad = verify_numbers(out, [retriever.entries[idx]["a"] for s, idx in hits])
    if not out or "资料不足" in out or bad:
        result["mode"] = "fallback"
        result["answer"] = entry["a"]
        result["warn"] = (
            f"模型改写未通过校验(多出数字 {bad})，已回退知识原文" if bad else "模型改写为空或拒答，已回退知识原文"
        )
        return result

    result["mode"] = "rewrite"
    result["answer"] = out
    result["warn"] = "低风险条目，模型改写已通过数字校验"
    return result


# ---------------------------------------------------------------- 入口
with open(KB_PATH, encoding="utf-8") as f:
    KB = json.load(f)
retriever = Retriever(KB["entries"])


def print_result(r):
    print(f"问：{r['question']}")
    print(f"命中：{'是' if r['hit'] else '否'}  得分 {r['score']}  条目 {r['id']}（{r['cat']}）")
    print(f"模式：{r['mode']}")
    if r["warn"]:
        print(f"说明：{r['warn']}")
    print(f"答：{r['answer']}")
    print("候选：" + " | ".join(f"{c['id']}({c['score']})" for c in r["candidates"]))


def serve(port=11435):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/health":
                body = json.dumps(
                    {"ok": True, "entries": len(KB["entries"]), "kb": KB.get("name", "")},
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if u.path != "/ask":
                self.send_response(404); self.end_headers(); return
            q = (parse_qs(u.query).get("q") or [""])[0]
            _t = time.time()
            _ans = answer(q)
            body = json.dumps(_ans, ensure_ascii=False).encode("utf-8")
            # 每次查询留痕，便于在板子上核对「这次回答确实来自本机检索」
            print(
                "[ASK] q=%r hit=%s id=%s score=%s mode=%s %.1fms"
                % (
                    q,
                    _ans.get("hit"),
                    _ans.get("id"),
                    _ans.get("score"),
                    _ans.get("mode"),
                    (time.time() - _t) * 1000,
                ),
                flush=True,
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    print(f"知识库服务已启动： http://0.0.0.0:{port}/ask?q=猫能喝牛奶吗", flush=True)
    HTTPServer(("0.0.0.0", port), H).serve_forever()


def run_eval():
    cases = [
        ("猫能喝牛奶吗", "cat_milk"),
        ("我家猫体温39.8度正常吗", "cat_temp"),
        ("狗吃了巧克力怎么办", "chocolate"),
        ("葡萄能给狗吃吗", "grape"),
        ("柯基一天遛多久", "corgi_exercise"),
        ("猫咪一天睡几个小时", "cat_sleep"),
        ("公猫尿不出来", "flutd"),
        ("多久打一次疫苗", "vaccine"),
        ("狗中暑怎么急救", "heatstroke"),
        ("猫粮狗粮能混着喂吗", "food_swap"),
    ]
    ok = 0
    print(f"{'问题':<24}{'期望':<16}{'实际':<16}{'得分':>7}  结果")
    print("-" * 78)
    for q, want in cases:
        hits = retriever.score(q)[:1]
        s, i = hits[0]
        got = retriever.entries[i]["id"]
        good = got == want
        ok += good
        print(f"{q:<24}{want:<16}{got:<16}{s:>7}  {'OK' if good else 'MISS'}")
    print("-" * 78)
    print(f"检索准确率 {ok}/{len(cases)}")

    print("\n--- 未命中边界测试（应全部判为未命中）---")
    probes = ["今天股市怎么样", "帮我写一首诗", "Python 怎么读文件", "北京到上海多远", "推荐一部电影"]
    for q in probes:
        s, i = retriever.score(q)[0]
        print(f"{q:<20} 最高分 {s:>6}  最接近 {retriever.entries[i]['id']:<14} {'判定未命中 OK' if s < MIN_HIT else '误命中 FAIL'}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--rewrite" in args:
        HARD_HIT = 0.0
        args = [a for a in args if a != "--rewrite"]
        print("[实验模式] 已强制开启模型改写，用于观察 0.6B 是否会篡改资料\n")
    if "--serve" in args:
        serve()
    elif "--eval" in args:
        run_eval()
    elif "--raw" in args:
        q = args[-1]
        hits = retriever.score(q)[:3]
        for s, i in hits:
            e = retriever.entries[i]
            print(f"{s:>7}  {e['id']:<14} {e['q']}")
    elif args:
        print_result(answer(args[-1]))
    else:
        print(__doc__)
