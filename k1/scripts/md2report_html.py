#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown -> 符合 html4docx 规范的商务报告 HTML。

三条硬约束（都是 docx 转换器的真实限制，不是洁癖）：
  1. 转换器不计算 CSS 继承 —— 父容器上的 text-align/font 不下发到子块，
     所以每个块级元素必须自己带 inline style；
  2. html-review 要求所有样式走 CSS 变量（var(--*)），禁止裸字号/裸色值；
  3. 禁用 grid / dl / 装饰性 hr / span badge —— docx 里会拍平或变形；
     代码块用单列表格承载底纹，避免逐段 shd 拼出断纹。

用法: python md2report_html.py <in.md> <out.html> "<标题>" "<副标题>" "<版本/日期>" "<文档类型>"
"""
import html
import re
import sys

# ---------- 设计令牌（全篇唯一的裸值出现处） ----------
TOKENS = {
    "--ff-heading": '"黑体", SimHei, sans-serif',
    "--ff-body": '"宋体", SimSun, serif',
    "--ff-mono": 'Consolas, "Courier New", monospace',
    "--fs-h1": "17pt",
    "--fs-h2": "15pt",
    "--fs-h3": "13pt",
    "--fs-body": "12pt",
    "--fs-td": "10.5pt",
    "--fs-small": "11pt",
    "--fs-code": "9.5pt",
    "--color-primary": "#1a4d8f",
    "--color-accent": "#c8102e",
    "--color-text": "#1a1a1a",
    "--color-muted": "#5a5a5a",
    "--color-border": "#c9d3e0",
    "--color-bg": "#ffffff",
    "--color-head-bg": "#eaf0f7",
    "--color-code-bg": "#f4f6f9",
    "--sp-1": "3pt",
    "--sp-2": "6pt",
    "--sp-3": "9pt",
    "--sp-4": "12pt",
    "--sp-5": "18pt",
    "--sp-6": "26pt",
    "--page-content-width": "15.6cm",
    "--lh-body": "1.7",
}

S_BODY = ("font-family:var(--ff-body);font-size:var(--fs-body);line-height:var(--lh-body);"
          "color:var(--color-text);text-indent:2em;margin:0 0 var(--sp-2) 0;")
S_H1 = ("font-family:var(--ff-heading);font-size:var(--fs-h1);font-weight:700;"
        "color:var(--color-primary);margin:0 0 var(--sp-5) 0;padding:0 0 var(--sp-2) 0;"
        "border-bottom:2pt solid var(--color-primary);text-align:center;text-indent:0;")
S_H2 = ("font-family:var(--ff-heading);font-size:var(--fs-h2);font-weight:700;"
        "color:var(--color-primary);margin:var(--sp-5) 0 var(--sp-3) 0;"
        "padding:var(--sp-1) 0 var(--sp-1) var(--sp-2);"
        "border-left:5pt solid var(--color-primary);background:var(--color-head-bg);"
        "text-indent:0;")
S_H3 = ("font-family:var(--ff-heading);font-size:var(--fs-h3);font-weight:700;"
        "color:var(--color-primary);margin:var(--sp-4) 0 var(--sp-2) 0;text-indent:0;")
S_H4 = ("font-family:var(--ff-heading);font-size:var(--fs-body);font-weight:700;"
        "color:var(--color-text);margin:var(--sp-3) 0 var(--sp-2) 0;text-indent:0;")
S_LI = ("font-family:var(--ff-body);font-size:var(--fs-body);line-height:var(--lh-body);"
        "color:var(--color-text);margin:0 0 var(--sp-1) 0;text-indent:0;")
S_TD = ("font-family:var(--ff-body);font-size:var(--fs-td);line-height:1.5;"
        "color:var(--color-text);border:1pt solid var(--color-border);"
        "padding:var(--sp-1) var(--sp-2);text-indent:0;vertical-align:top;")
S_TH = ("font-family:var(--ff-heading);font-size:var(--fs-td);font-weight:700;"
        "color:var(--color-primary);border:1pt solid var(--color-border);"
        "padding:var(--sp-1) var(--sp-2);background:var(--color-head-bg);text-indent:0;")
S_CODE = ('font-family:var(--ff-mono);font-size:var(--fs-code);line-height:1.45;'
          'color:var(--color-text);background:var(--color-code-bg);text-indent:0;'
          'margin:0;padding:0;')
S_TABLE = ('width:100%;border-collapse:collapse;margin:var(--sp-2) 0 var(--sp-4) 0;'
           'background:var(--color-bg);')

BLOCK_START = ("#", "|", "```", "- ", "* ", "> ")


def inline(text: str) -> str:
    s = html.escape(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`",
               r'<span style="font-family:var(--ff-mono);font-size:var(--fs-td);'
               r'background:var(--color-code-bg);">\1</span>', s)
    return s


def is_table_sep(cells) -> bool:
    if not isinstance(cells, list) or not cells:
        return False
    return all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells if c.strip())


def split_row(line: str):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def table_html(rows):
    if not rows:
        return ""
    head = rows[0]
    rest = rows[2:] if len(rows) > 1 and is_table_sep(rows[1]) else rows[1:]
    out = [f'<table style="{S_TABLE}">']
    out.append("<thead><tr>" + "".join(f'<th style="{S_TH}">{inline(c)}</th>' for c in head)
               + "</tr></thead><tbody>")
    for r in rest:
        out.append("<tr>" + "".join(f'<td style="{S_TD}">{inline(c)}</td>' for c in r) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def code_html(lines):
    """代码块用 div 逐行承载。
    不用 <table>：审查器要求 table 必须有 thead+tbody，而代码块只有单列数据，
    补空表头反而多一行空白。div 在 docx 里同样落为段落，等宽字体与底纹都能保留。
    """
    out = ['<div style="border-left:3pt solid var(--color-primary);'
           'background:var(--color-code-bg);padding:var(--sp-2) var(--sp-3);'
           'margin:var(--sp-2) 0 var(--sp-4) 0;">']
    for ln in lines:
        out.append(f'<div style="{S_CODE}">{html.escape(ln) if ln else "&nbsp;"}</div>')
    out.append("</div>")
    return "".join(out)


def build(md: str, cover_title: str, cover_subtitle: str, meta_line: str, doc_type: str):
    lines = md.split("\n")
    body, toc = [], []
    cur = None
    sec = 0
    i, n = 0, len(lines)

    while i < n:
        s = lines[i].strip()
        if not s:
            i += 1
            continue

        if s.startswith("```"):
            i += 1
            buf = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i].rstrip("\n"))
                i += 1
            i += 1
            body.append(code_html(buf))
            continue

        if s.startswith("|"):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            body.append(table_html(rows))
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", s)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 1:
                i += 1
                continue
            if level == 2:
                sec += 1
                sid = f"section-{sec}"
                toc.append([sid, title, []])
                cur = toc[-1]
                body.append(f'<h2 id="{sid}" style="{S_H2}">{inline(title)}</h2>')
            elif level == 3:
                if cur is not None:
                    cur[2].append(title)
                body.append(f'<h3 style="{S_H3}">{inline(title)}</h3>')
            else:
                body.append(f'<h4 style="{S_H4}">{inline(title)}</h4>')
            i += 1
            continue

        if s.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            body.append(
                f'<p style="{S_BODY}border-left:3pt solid var(--color-primary);'
                f'background:var(--color-head-bg);padding:var(--sp-2) var(--sp-3);'
                f'text-indent:0;">&nbsp;&nbsp;{inline(" ".join(buf))}</p>')
            continue

        if re.match(r"^[-*]\s+", s):
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i].strip()):
                items.append(re.sub(r"^[-*]\s+", "", lines[i].strip()))
                i += 1
            for it in items:
                body.append(f'<p style="{S_LI}">· {inline(it)}</p>')
            continue

        if re.match(r"^\d+[.)]\s+", s):
            items = []
            while i < n and re.match(r"^\d+[.)]\s+", lines[i].strip()):
                items.append(re.sub(r"^\d+[.)]\s+", "", lines[i].strip()))
                i += 1
            for k, it in enumerate(items, 1):
                body.append(f'<p style="{S_LI}">{k}. {inline(it)}</p>')
            continue

        buf = [s]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(BLOCK_START)
                    or re.match(r"^\d+[.)]\s+", nxt) or re.match(r"^#{1,4}\s+", nxt)):
                break
            buf.append(nxt)
            i += 1
        body.append(f'<p style="{S_BODY}">{inline(" ".join(buf))}</p>')

    # 目录标题必须写成 <p> 且文本恰为「目录」：
    #   ① 转换器把 nav 整体当作 TOC 容器，nav 内的非列表子元素（div）会被丢弃，
    #      标题段因此不落盘（实测 2026-11-02：docx 里找不到「目录」段）；
    #   ② 标题用 class="caption" 排除在 TQ-06「正文字号一致性」统计之外，
    #      否则会与 --fs-body 的正文 p 一并被判为多字号；
    #   ③ 不用全角空格「目　录」——校验按去空白后文本 == "目录" 精确匹配，
    #      全角空格虽属空白，但多一层依赖不如直接给干净文本，视觉居中已由
    #      text-align:center 承担。
    # 目录条目只保留一级（全部为 <a> 链接）：转换器只为带 href 的条目生成
    # PAGEREF 域，嵌套的纯文本子条目会退化为普通段落，落在 TOC 域之后、
    # 下一分节符之前，被判为「目录后内容未分节」。
    t = [f'<p class="caption" style="font-family:var(--ff-heading);'
         f'font-size:var(--fs-h3);font-weight:700;color:var(--color-primary);'
         f'text-align:center;text-indent:0;margin:var(--sp-6) 0 var(--sp-4) 0;">目录</p>',
         '<nav class="doc-toc" aria-label="文档目录" style="text-indent:0;">',
         '<ol class="toc-list" style="list-style:none;padding-left:0;margin:0;">']
    for sid, title, subs in toc:
        inner = (f'<a href="#{sid}" style="color:var(--color-text);text-decoration:none;">'
                 f'{html.escape(title)}</a>')
        t.append(f'<li style="font-family:var(--ff-body);font-size:var(--fs-small);'
                 f'line-height:1.9;margin:0;text-indent:0;">{inner}</li>')
    t.append("</ol></nav>")

    root = "\n".join(f"    {k}: {v};" for k, v in TOKENS.items())
    style = f"""
  @page {{ @bottom-center {{ content: counter(page); }} }}
  @page cover {{ @bottom-center {{ content: none; }} }}
  section[role="cover"] {{ page: cover; }}
  :root {{
{root}
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: var(--ff-body); font-size: var(--fs-body);
         line-height: var(--lh-body); color: var(--color-text);
         background: var(--color-bg);
         max-width: var(--page-content-width); margin-left: auto; margin-right: auto; }}
  .toc-list, .toc-list ol, .toc-list ul {{ list-style: none; list-style-type: none; }}
  a {{ text-decoration: none; }}
  table {{ border-collapse: collapse; }}
"""

    cover = f"""
<section role="cover">
  <div style="font-family:var(--ff-heading);font-size:var(--fs-body);font-weight:700;
            color:var(--color-accent);letter-spacing:0.14em;text-align:center;
            margin:var(--sp-6) 0 var(--sp-4) 0;">CIE 全国 RISC-V 高水平创新及应用大赛</div>
  <div style="font-family:var(--ff-body);font-size:var(--fs-small);color:var(--color-muted);
            text-align:center;margin:0 0 var(--sp-6) 0;">进迭时空赛道 · 赛题方向 4</div>
  <div style="font-family:var(--ff-heading);font-size:var(--fs-h1);font-weight:700;
            color:var(--color-primary);text-align:center;line-height:1.5;
            margin:0 0 var(--sp-4) 0;">{html.escape(cover_title)}</div>
  <div style="font-family:var(--ff-body);font-size:var(--fs-h3);color:var(--color-muted);
            text-align:center;line-height:1.8;margin:0 0 var(--sp-6) 0;">
    {html.escape(cover_subtitle)}</div>
  <div style="font-family:var(--ff-body);font-size:var(--fs-small);color:var(--color-muted);
            text-align:center;margin:0 0 var(--sp-1) 0;">文档类型　{html.escape(doc_type)}</div>
  <div style="font-family:var(--ff-body);font-size:var(--fs-small);color:var(--color-muted);
            text-align:center;margin:0 0 var(--sp-1) 0;">硬件平台　K1 MUSE Pi Pro + RK3568</div>
  <div style="font-family:var(--ff-body);font-size:var(--fs-small);color:var(--color-muted);
            text-align:center;margin:0 0 var(--sp-1) 0;">软件环境　Bianbu OS + OpenHarmony 5.0</div>
  <div style="font-family:var(--ff-body);font-size:var(--fs-small);color:var(--color-muted);
            text-align:center;margin:0 0 var(--sp-6) 0;">版本 / 日期　{html.escape(meta_line)}</div>
</section>
"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="docx-page-size" content="A4">
<title>{html.escape(cover_title)}</title>
<style>{style}</style>
</head>
<body>
{cover}
<section role="cover">
{''.join(t)}
</section>
<section role="body" data-page-restart="1">
<h1 style="{S_H1}">{html.escape(cover_title)}</h1>
{''.join(body)}
</section>
</body>
</html>
"""


def main():
    src, dst = sys.argv[1], sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else "文档"
    subtitle = sys.argv[4] if len(sys.argv) > 4 else ""
    meta = sys.argv[5] if len(sys.argv) > 5 else "v1.0 / 2026-11-02"
    doc_type = sys.argv[6] if len(sys.argv) > 6 else "技术方案书"
    md = open(src, encoding="utf-8").read()
    out = build(md, title, subtitle, meta, doc_type)
    open(dst, "w", encoding="utf-8").write(out)
    print(f"[ok] {dst}  {len(out)} bytes")


if __name__ == "__main__":
    main()
