#!/usr/bin/env python3
"""Convert a (simple) Loon .lpx/.plugin into a Shadowrocket .sgmodule.

Handles: header, [Rule], [Rewrite] (reject family / 302 / 307), [Script],
[MitM], [Host]. Anything it cannot map is kept as a commented line
('# [UNCONVERTED] ...') and reported on stderr - nothing is dropped silently.

Usage: lpx2sgmodule.py <input.lpx> <output.sgmodule> <source_url>
"""
import re
import sys

REJECT_POLICIES = {"REJECT", "REJECT-DROP", "REJECT-NO-DROP", "REJECT-TINYGIF",
                   "REJECT-IMG", "REJECT-DICT", "REJECT-ARRAY", "REJECT-200"}
SIMPLE_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD",
                "IP-CIDR", "IP-CIDR6", "IP-ASN", "GEOIP", "URL-REGEX",
                "USER-AGENT", "DST-PORT", "DEST-PORT", "PROCESS-NAME"}
LOGICAL = {"AND", "OR", "NOT"}
REWRITE_REJECT = {"reject", "reject-200", "reject-img", "reject-dict", "reject-array"}

unconverted = []
stats = {"rule": 0, "rewrite": 0, "script": 0, "mitm_hosts": 0, "host": 0, "maplocal": 0}


def split_top(s):
    """Split on commas outside quotes/parentheses."""
    out, buf, depth, q = [], "", 0, False
    for ch in s:
        if ch == '"':
            q = not q
        elif not q and ch == "(":
            depth += 1
        elif not q and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not q:
            out.append(buf.strip()); buf = ""
        else:
            buf += ch
    out.append(buf.strip())
    return out


def unquote(v):
    if len(v) >= 2 and v[0] == v[-1] == '"' and "," not in v[1:-1]:
        return v[1:-1]
    return v


def norm_sub(expr):
    """Normalise a logical sub-expression like '(DOMAIN, x)' or '(OR, ((..),(..)))'."""
    expr = expr.strip()
    assert expr[0] == "(" and expr[-1] == ")", expr
    inner = expr[1:-1].strip()
    parts = split_top(inner)
    t = parts[0].upper()
    if t in LOGICAL:
        return "(" + t + "," + norm_group(parts[1]) + ")"
    if t not in SIMPLE_TYPES:
        raise ValueError("unsupported sub-rule type " + t)
    return "(" + ",".join([t, unquote(parts[1])] + parts[2:]) + ")"


def norm_group(g):
    g = g.strip()
    assert g[0] == "(" and g[-1] == ")", g
    items = split_top(g[1:-1])
    return "(" + ",".join(norm_sub(i) for i in items) + ")"


def conv_rule(line):
    parts = split_top(line)
    t = parts[0].upper()
    if t in LOGICAL:
        body, policy, opts = parts[1], parts[2], parts[3:]
        body = norm_group(body)
        res = [t, body]
    elif t in SIMPLE_TYPES:
        val, policy, opts = unquote(parts[1]), parts[2], parts[3:]
        res = [t, val]
    else:
        raise ValueError("unsupported rule type " + t)
    pu = policy.upper()
    if pu in REJECT_POLICIES or pu in ("DIRECT", "PROXY"):
        policy = pu
    res.append(policy)
    res += [o for o in opts if o]
    return ",".join(res)


def conv_rewrite(line):
    m = re.match(r"^(\S+)\s+(.+)$", line)
    if not m:
        raise ValueError("unparseable rewrite")
    pat, rest = m.group(1), m.group(2).strip()
    toks = rest.split()
    if len(toks) == 1 and toks[0].lower() in REWRITE_REJECT:
        return "URL Rewrite", f"{pat} - {toks[0].lower()}"
    if len(toks) == 1 and toks[0].lower() == "reject-video":
        return "URL Rewrite", f"{pat} - reject-200"  # adaptation
    if len(toks) == 2 and toks[0] in ("302", "307"):
        return "URL Rewrite", f"{pat} {toks[1]} {toks[0]}"
    if len(toks) == 2 and toks[1] in ("302", "307"):
        return "URL Rewrite", f"{pat} {toks[0]} {toks[1]}"
    raise ValueError("unsupported rewrite action: " + rest)


def conv_script(line, idx):
    m = re.match(r"^(http-request|http-response)\s+(\S+)\s+(.*)$", line)
    if not m:
        raise ValueError("unsupported script line")
    typ, pat, rest = m.groups()
    kv = dict(p.split("=", 1) for p in (x.strip() for x in rest.split(",")) if "=" in p)
    name = kv.pop("tag", f"script_{idx}")
    out = [f"type={typ}", f"pattern={pat}"]
    if kv.pop("requires-body", "false").lower() in ("true", "1"):
        out.append("requires-body=1")
    if kv.pop("binary-body-mode", "false").lower() in ("true", "1"):
        out.append("binary-body-mode=1")
    out.append("script-path=" + kv.pop("script-path"))
    for k in ("timeout", "argument", "max-size"):
        if k in kv:
            out.append(f"{k}={kv.pop(k)}")
    kv.pop("enable", None)
    if kv:
        unconverted.append(("Script-params", str(kv)))
    return f"{name} = " + ",".join(out)


def main(src, dst, url):
    text = open(src, encoding="utf-8").read().replace("\r\n", "\n")
    header, sections, order, cur = {}, {}, [], None
    for raw in text.split("\n"):
        line = raw.strip()
        if cur is None and line.startswith("#!"):
            k, _, v = line[2:].partition("=")
            header[k.strip()] = v.strip()
            continue
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            cur = m.group(1).strip().lower()
            order.append(cur); sections.setdefault(cur, [])
            continue
        if cur is not None:
            sections[cur].append(line)

    out_sec = {}
    def add(sec, l):
        out_sec.setdefault(sec, []).append(l)

    for sec in order:
        lines = sections[sec]
        target = {"rule": "Rule", "rewrite": "URL Rewrite", "script": "Script",
                  "host": "Host", "mitm": "MITM"}.get(sec)
        if target is None:
            for l in lines:
                if l:
                    unconverted.append((sec, l)); add("Rule", f"# [UNCONVERTED][{sec}] {l}")
            continue
        for i, l in enumerate(lines):
            if not l:
                if target in ("Rule", "URL Rewrite", "Script", "Host"):
                    add(target, "")
                continue
            if l.startswith("#") or l.startswith("//"):
                add(target, "#" + l.lstrip("#/"))
                continue
            try:
                if sec == "rule":
                    add("Rule", conv_rule(l)); stats["rule"] += 1
                elif sec == "rewrite":
                    t, c = conv_rewrite(l); add(t, c); stats["rewrite"] += 1
                elif sec == "script":
                    add("Script", conv_script(l, i)); stats["script"] += 1
                elif sec == "host":
                    add("Host", re.sub(r"\s*=\s*", " = ", l, count=1)); stats["host"] += 1
                elif sec == "mitm":
                    k, _, v = l.partition("=")
                    if k.strip().lower() != "hostname":
                        raise ValueError("unsupported MITM key")
                    hosts = [h.strip() for h in v.split(",") if h.strip()]
                    stats["mitm_hosts"] += len(hosts)
                    add("MITM", "hostname = %APPEND% " + ", ".join(hosts))
            except Exception as e:  # keep, commented
                unconverted.append((sec, f"{l}  ({e})"))
                add(target if target != "MITM" else "Rule", f"# [UNCONVERTED] {l}")

    # collapse repeated / trailing blank lines
    for k, v in out_sec.items():
        res = []
        for l in v:
            if l == "" and (not res or res[-1] == ""):
                continue
            res.append(l)
        while res and res[-1] == "":
            res.pop()
        out_sec[k] = res

    desc = header.get("desc", "").replace("\\n\\n", " ").replace("\\n", " ")
    desc += "（转换自 kelee.one Loon 插件，原作者见 author）"
    tags = header.get("tag", "")
    category = tags.split(",")[0].strip() if tags else "去广告"
    h = [f"#!name={header.get('name', '')}",
         f"#!desc={desc}",
         f"#!author={header.get('author', '')}；Shadowrocket 转换: ack20a",
         f"#!homepage={header.get('homepage', 'https://hub.kelee.one')}"]
    if header.get("icon"):
        h.append(f"#!icon={header['icon']}")
    h.append(f"#!category={category}")
    if header.get("arguments"):
        unconverted.append(("header", "#!arguments=" + header["arguments"]))
    h += ["",
          f"# 上游来源: {url}",
          f"# 上游日期: {header.get('date', 'N/A')}",
          f"# 上游 Loon 最低版本: {header.get('loon_version', 'N/A')}",
          f"# 上游标签: {tags}",
          "# 由 scripts/lpx2sgmodule.py 自动转换，请勿手动修改，同步时重新生成。"]
    body = []
    for sec in ("Rule", "URL Rewrite", "Map Local", "Script", "Host", "MITM"):
        if out_sec.get(sec):
            body += ["", f"[{sec}]"] + out_sec[sec]
    open(dst, "w", encoding="utf-8").write("\n".join(h + body) + "\n")
    print(f"{dst}: {stats}; date={header.get('date')}", file=sys.stderr)
    for u in unconverted:
        print("UNCONVERTED:", u, file=sys.stderr)


if __name__ == "__main__":
    main(*sys.argv[1:4])
