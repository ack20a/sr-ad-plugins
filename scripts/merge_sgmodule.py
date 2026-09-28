#!/usr/bin/env python3
"""Merge several upstream plugins/modules into one Shadowrocket module (.sgmodule).

Each source is either a Loon plugin (.lpx / .plugin, converted with lpx2sgmodule)
or a Surge/Shadowrocket module (.sgmodule, used as is). Sections are concatenated
in source order (so earlier sources get script priority), [MITM] hostnames are
unioned (h2 is dropped, see lpx2sgmodule.H2_DROPPED), #!arguments are merged
(duplicate keys are an error) and the recipe in MERGES can override argument
defaults and add extra lines.

Usage: merge_sgmodule.py <name> <output.sgmodule>      (name is a key of MERGES;
       upstream files are read from upstream/<file>)
Library: merge(name, texts) -> module_text   (texts: {file: text})
"""
import os
import re
import sys

import lpx2sgmodule as C

MERGES = {
    "YouTube": {
        "name": "YouTube 去广告 + 双语字幕",
        "desc": ("移除 YouTube 视频、瀑布流、搜索和 Shorts 中的广告，可隐藏底栏按钮，支持画中画和后台播放"
                 "（可莉 YouTube 去广告）；提供双语字幕和歌词翻译（DualSubs）。"
                 "需要开启 HTTPS 解密，模块会阻断 YouTube 的 QUIC；不开 HTTP/2 解密（见 README）。不支持 tvOS。"),
        "author": ("可莉🅥[https://github.com/luestr/ProxyResource/blob/main/README.md]、"
                   "Maasea[https://github.com/Maasea]、VirgilClyne[https://github.com/VirgilClyne]、"
                   "Choler[https://github.com/Choler]、DivineEngine[https://github.com/DivineEngine]、"
                   "app2smile[https://github.com/app2smile]；Shadowrocket 合并: ack20a"),
        "homepage": "https://github.com/ack20a/sr-ad-plugins",
        "icon": "https://raw.githubusercontent.com/luestr/IconResource/main/App_icon/120px/YouTube.png",
        "category": "去广告",
        # Order matters: DualSubs' guide says the ad-block module must have higher priority.
        "sources": [
            ("YouTube_remove_ads.lpx", "https://kelee.one/Tool/Loon/Lpx/YouTube_remove_ads.lpx",
             "可莉 YouTube 去广告"),
            ("DualSubs.YouTube.sgmodule",
             "https://github.com/DualSubs/YouTube/releases/latest/download/DualSubs.YouTube.sgmodule",
             "DualSubs YouTube 双语字幕"),
        ],
        # DualSubs does the subtitles; the ad-block script's own caption translation would
        # translate the same track a second time (Maasea's module also defaults to off).
        "argument_defaults": {"captionLang": "off"},
        "desc_note": ("注意：合并版里 captionLang 默认为 off，字幕翻译交给 DualSubs（Type/Vendor 等参数）处理，"
                      "避免同一条字幕被翻译两次。"),
        "extra": {
            # Loon's plugin also asks for "MitM over HTTP/2"; it stays off here (repo policy,
            # lpx2sgmodule.H2_DROPPED). Maasea's and DualSubs' own modules do not enable it either.
            "Rule": ["# 阻断 QUIC，让 YouTube 回落到可以解密的 TCP（对应 Loon 的 QUIC 回退保护）",
                     # NO-DROP: a dropped (not refused) QUIC packet makes the app wait for a timeout
                     "AND,((DOMAIN-SUFFIX,googlevideo.com),(PROTOCOL,UDP)),REJECT-NO-DROP",
                     "AND,((DOMAIN,youtubei.googleapis.com),(PROTOCOL,UDP)),REJECT-NO-DROP"],
        },
    },
}

SECTION_ORDER = ("General", "Rule", "URL Rewrite", "Header Rewrite", "Body Rewrite", "Map Local",
                 "Script", "Host", "MITM")


def parse_module(text):
    """-> (header {key: value}, [(section, [lines])])"""
    header, sections, cur = {}, [], None
    for raw in text.lstrip("﻿").splitlines():
        line = raw.strip()
        if cur is None and line.startswith("#!"):
            k, _, v = line[2:].partition("=")
            header[k.strip().lower()] = v.strip()
            continue
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            cur = m.group(1).strip()
            cur = {"mitm": "MITM"}.get(cur.lower(), cur)
            sections.append((cur, []))
            continue
        if cur is not None:
            sections[-1][1].append(line)
    return header, sections


def parse_arguments(s):
    out = {}
    for kv in C.split_top(s) if s else []:
        k, _, v = kv.partition(":")
        out[k.strip()] = v.strip()
    return out


def trim(lines):
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def merge(name, texts):
    recipe = MERGES[name]
    args, descs, body, hosts, h2_dropped, meta = {}, [], {}, [], False, []
    script_names = set()
    for fname, url, label in recipe["sources"]:
        text = texts[fname]
        if fname.endswith((".lpx", ".plugin")):
            up_header = C.parse_plugin(text.lstrip("﻿"))[0]
            text, _stats, unconv, _notes = C.convert(text, url)
            if unconv:
                raise ValueError(f"{fname}: unconverted entries {unconv}")
        else:
            up_header = parse_module(text)[0]
        header, sections = parse_module(text)
        meta.append((label, url, up_header))
        for k, v in parse_arguments(header.get("arguments", "")).items():
            if k in args:
                raise ValueError(f"argument {k} defined by two sources")
            args[k] = v
        if header.get("arguments-desc"):
            descs.append(re.sub(r"(\\n)+$", "", header["arguments-desc"]))
        for sec, lines in sections:
            if sec == "MITM":
                for l in lines:
                    k, _, v = l.partition("=")
                    k = k.strip().lower()
                    if k == "hostname":
                        for h in v.replace("%APPEND%", "").split(","):
                            if h.strip() and h.strip() not in hosts:
                                hosts.append(h.strip())
                    elif k == "h2" or l == C.H2_DROPPED:
                        h2_dropped = h2_dropped or l == C.H2_DROPPED or C.truthy(v)
                    elif l and not l.startswith("#"):
                        raise ValueError(f"{fname}: unsupported [MITM] line {l}")
                continue
            lines = trim(list(lines))
            if not lines:
                continue
            if sec == "Script":
                for l in lines:
                    if l and not l.startswith("#"):
                        n = l.split("=", 1)[0].strip()
                        if n in script_names:
                            raise ValueError(f"script name {n} defined by two sources")
                        script_names.add(n)
            body.setdefault(sec, []).append([f"# ---- {label} ----"] + lines)

    for k, v in recipe.get("argument_defaults", {}).items():
        if k not in args:
            raise ValueError(f"argument_defaults: unknown argument {k}")
        args[k] = v
    for sec, lines in recipe.get("extra", {}).items():
        if sec == "MITM":
            raise ValueError("extra MITM lines are not supported (h2 stays off, see H2_DROPPED)")
        body.setdefault(sec, []).insert(0, ["# ---- 合并时添加 ----"] + lines)

    h = [f"#!name={recipe['name']}", f"#!desc={recipe['desc']}", f"#!author={recipe['author']}",
         f"#!homepage={recipe['homepage']}", f"#!icon={recipe['icon']}", f"#!category={recipe['category']}"]
    if args:
        h.append("#!arguments=" + ",".join(f"{k}:{v}" for k, v in args.items()))
    if recipe.get("desc_note"):
        descs.append(recipe["desc_note"])
    if descs:
        h.append("#!arguments-desc=" + "\\n\\n".join(descs))
    h.append("")
    for label, url, up in meta:
        h.append(f"# 上游来源（{label}）: {url}")
        info = [f"{k}: {up[k]}" for k in ("date", "version") if up.get(k)]
        if info:
            h.append("#   " + "，".join(info))
    for k, v in recipe.get("argument_defaults", {}).items():
        h.append(f"# 合并时修改的参数默认值: {k} = {v}")
    h.append("# 由 scripts/merge_sgmodule.py 自动生成，请勿手动修改，同步时重新生成。")

    out = []
    order = list(SECTION_ORDER) + [s for s in body if s not in SECTION_ORDER]
    for sec in order:
        if sec == "MITM":
            mitm = []
            if hosts:
                mitm.append("hostname = %APPEND% " + ", ".join(hosts))
            if h2_dropped:
                mitm.append(C.H2_DROPPED)
            if mitm:
                out += ["", "[MITM]"] + mitm
            continue
        if sec in body:
            out += ["", f"[{sec}]"]
            for i, chunk in enumerate(body[sec]):
                out += ([""] if i else []) + chunk
    return "\n".join(h + out) + "\n"


def main(name, dst):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    texts = {}
    for fname, _url, _label in MERGES[name]["sources"]:
        with open(os.path.join(root, "upstream", fname), encoding="utf-8-sig", newline="") as f:
            texts[fname] = f.read()
    result = merge(name, texts)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"{dst}: merged {len(MERGES[name]['sources'])} sources", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in MERGES:
        sys.exit(__doc__ + "\nknown names: " + ", ".join(MERGES))
    main(*sys.argv[1:3])
