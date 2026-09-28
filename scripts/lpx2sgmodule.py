#!/usr/bin/env python3
"""Convert a Loon plugin (.lpx / .plugin) into a Shadowrocket module (.sgmodule).

Supported (see README "转换时做的适配" for the full mapping table):
  header (#!key=value, also "#!key = value"), [Argument], [General], [Rule],
  [Rewrite] (reject family, 302/307/header, mock-response-body -> [Map Local],
  *-body-json-jq/del/replace/add and *-body-replace-regex -> [Body Rewrite],
  *-header-* -> [Header Rewrite]), [Script] (http-request/http-response/cron and
  the simple Loon "response if ${url} ~= /re/ then script(...)" form), [Host],
  [MitM] (hostname, h2).

Anything that cannot be mapped 1:1 is kept as a commented line
('# [UNCONVERTED] ...') and reported - nothing is dropped silently.
Adaptations that change behaviour slightly are reported as NOTE.

Usage: lpx2sgmodule.py <input> <output.sgmodule> <source_url>
Library: convert(text, source_url) -> (module_text, stats, unconverted, notes)
"""
import json
import re
import sys

REJECT_POLICIES = {"REJECT", "REJECT-DROP", "REJECT-NO-DROP", "REJECT-TINYGIF",
                   "REJECT-IMG", "REJECT-DICT", "REJECT-ARRAY", "REJECT-200"}
RULE_RENAME = {"DEST-PORT": "DST-PORT"}
SIMPLE_TYPES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-WILDCARD",
                "IP-CIDR", "IP-CIDR6", "IP-ASN", "GEOIP", "URL-REGEX",
                "USER-AGENT", "DST-PORT"}
SUB_ONLY_TYPES = {"PROTOCOL"}          # allowed inside AND/OR/NOT only
LOGICAL = {"AND", "OR", "NOT"}
REWRITE_REJECT = {"reject", "reject-200", "reject-img", "reject-dict", "reject-array"}
REWRITE_REJECT_MAP = {"reject-video": "reject-img", "reject-tinygif": "reject-img"}
MOCK_CT = {"json": "application/json", "text": "text/plain", "html": "text/html",
           "xml": "application/xml", "css": "text/css", "javascript": "application/javascript",
           "js": "application/javascript", "plain": "text/plain"}
SCRIPT_KEYS = ("script-path|pattern|timeout|argument|requires-body|max-size|binary-body-mode|"
               "enable|tag|img-url|engine|cronexpr?|script-update-interval|wake-system|debug|ability")


class Unsupported(ValueError):
    pass


# ---------------------------------------------------------------- helpers
def split_top(s, sep=","):
    """Split on `sep` outside double quotes, (), [] and {}."""
    out, buf, depth, q = [], "", 0, False
    for ch in s:
        if ch == '"':
            q = not q
        elif not q and ch in "([{":
            depth += 1
        elif not q and ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0 and not q:
            out.append(buf.strip()); buf = ""
        else:
            buf += ch
    out.append(buf.strip())
    return out


def unquote(v):
    """Drop surrounding quotes unless the value needs them (contains a comma)."""
    if len(v) >= 2 and v[0] == v[-1] == '"' and "," not in v[1:-1]:
        return v[1:-1]
    return v


def strip_q(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def truthy(v):
    return strip_q(str(v)).lower() in ("true", "1")


# ---------------------------------------------------------------- rules
def rule_type(t):
    t = t.upper()
    return RULE_RENAME.get(t, t)


def norm_sub(expr):
    expr = expr.strip()
    if not (expr.startswith("(") and expr.endswith(")")):
        raise Unsupported("bad logical sub-rule " + expr)
    parts = split_top(expr[1:-1].strip())
    t = rule_type(parts[0])
    if t in LOGICAL:
        return "(" + t + "," + norm_group(parts[1]) + ")"
    if t not in SIMPLE_TYPES and t not in SUB_ONLY_TYPES:
        raise Unsupported("unsupported sub-rule type " + t)
    return "(" + ",".join([t, unquote(parts[1])] + parts[2:]) + ")"


def norm_group(g):
    g = g.strip()
    if not (g.startswith("(") and g.endswith(")")):
        raise Unsupported("bad logical group " + g)
    return "(" + ",".join(norm_sub(i) for i in split_top(g[1:-1])) + ")"


def conv_rule(line):
    parts = split_top(line)
    if len(parts) < 3:
        raise Unsupported("rule needs TYPE,VALUE,POLICY")
    t = rule_type(parts[0])
    if t in LOGICAL:
        res = [t, norm_group(parts[1])]
    elif t in SIMPLE_TYPES:
        res = [t, unquote(parts[1])]
    else:
        raise Unsupported("unsupported rule type " + t)
    policy, opts = parts[2], parts[3:]
    pu = policy.upper()
    if pu in REJECT_POLICIES or pu in ("DIRECT", "PROXY"):
        policy = pu
    res.append(policy)
    res += [o for o in opts if o]
    return ",".join(res)


# ---------------------------------------------------------------- rewrites
def parse_json_path(p):
    """Same semantics as Script-Hub parseJsonPath: a.b[0]["c.d"]."""
    out = []
    for m in re.finditer(r"\.?([^.\[\]]+)|\[(['\"])(.*?)\2\]|\[(\d+)\]", p.strip()):
        if m.group(1) is not None:
            out.append(m.group(1))
        elif m.group(3) is not None:
            out.append(m.group(3))
        else:
            out.append(int(m.group(4)))
    return out


def loon_key(v):
    return v.replace("\\x20", " ")


def loon_value(v):
    v = v.replace("\\x20", " ")
    if len(v) >= 2 and v[0] == v[-1] == '"':
        return v[1:-1]
    try:
        return json.loads(v)
    except ValueError:
        return v


def jq_lit(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def parse_params(s):
    """Parse 'k=v k2="v with spaces / inner "quotes"" flag' (Loon mock/Map Local style)."""
    params = {}
    pat = re.compile(r'\s*([\w-]+)=("(?:.*?)"(?=\s+[\w-]+=|\s*$)|\S+)')
    pos = 0
    s = s.strip()
    while pos < len(s):
        m = pat.match(s, pos)
        if not m:
            raise Unsupported("cannot parse parameters: " + s[pos:])
        params[m.group(1)] = m.group(2)
        pos = m.end()
    return params


def conv_mock(pat, rest, notes):
    p = parse_params(rest)
    dtype = strip_q(p.pop("data-type", "text")).lower()
    status = strip_q(p.pop("status-code", "200"))
    b64 = truthy(p.pop("mock-data-is-base64", "false"))
    data = p.pop("data", None)
    path = p.pop("data-path", None)
    if p:
        raise Unsupported("unknown mock-response-body keys " + ",".join(p))
    ct = MOCK_CT.get(dtype)
    if path is not None:
        out = f'{pat} data-type=file data="{strip_q(path)}"'
    elif data is not None and b64:
        out = f"{pat} data-type=base64 data={data}"
    elif data is not None and ct:
        out = f"{pat} data-type=text data={data}"
    elif data is not None:
        raise Unsupported("mock data-type " + dtype)
    elif ct:
        out = f'{pat} data-type=text data=""'      # Loon: no data -> empty body
    else:
        raise Unsupported("mock-response-body without data/data-path")
    if ct:
        out += f' header="Content-Type:{ct}"'
    if status != "200":
        raise Unsupported(f"mock status-code={status} (Shadowrocket Map Local has no status-code)")
    notes.append(f"mock-response-body -> [Map Local] ({dtype}; status-code=200 is the default)")
    return out


def conv_rewrite(line, notes):
    """Returns list of (section, line)."""
    m = re.match(r"^(\S+)\s+(.+)$", line)
    if not m:
        raise Unsupported("unparseable rewrite")
    pat, rest = m.group(1), m.group(2).strip()
    if rest.startswith("- "):                  # Loon/Surge placeholder: "regex - reject"
        rest = rest[2:].strip()
    toks = rest.split()
    a0 = toks[0].lower()
    if len(toks) == 1 and a0 in REWRITE_REJECT:
        return [("URL Rewrite", f"{pat} - {a0}")]
    if len(toks) == 1 and a0 in REWRITE_REJECT_MAP:
        notes.append(f"{a0} -> {REWRITE_REJECT_MAP[a0]}")
        return [("URL Rewrite", f"{pat} - {REWRITE_REJECT_MAP[a0]}")]
    if len(toks) == 2 and toks[0] in ("302", "307", "header"):
        return [("URL Rewrite", f"{pat} {toks[1]} {toks[0]}")]
    if len(toks) == 2 and toks[1] in ("302", "307", "header"):
        return [("URL Rewrite", f"{pat} {toks[0]} {toks[1]}")]
    m = re.match(r"^(request|response)-body-(json-jq|json-del|json-replace|json-add|replace-regex)\s+(.*)$",
                 rest, re.S)
    if m:
        kind, act, arg = m.group(1), m.group(2), m.group(3).strip()
        if act == "json-jq":
            if "jq-path=" in arg:
                raise Unsupported("jq-path= (external jq file) not supported")
            if not (arg.startswith("'") and arg.endswith("'")):
                raise Unsupported("jq expression must be single-quoted")
            return [("Body Rewrite", f"http-{kind}-jq {pat} {arg}")]
        items = arg.split()
        if act == "replace-regex":
            if len(items) % 2:
                raise Unsupported("replace-regex needs find/replace pairs")
            return [("Body Rewrite", f"http-{kind} {pat} {arg}")]
        out = []
        if act == "json-del":
            for k in items:
                path = parse_json_path(loon_key(k))
                out.append(("Body Rewrite", f"http-{kind}-jq {pat} 'delpaths([{jq_lit(path)}])'"))
            return out
        if len(items) % 2:
            raise Unsupported(act + " needs key/value pairs")
        for k, v in zip(items[::2], items[1::2]):
            path, val = parse_json_path(loon_key(k)), loon_value(v)
            if "'" in jq_lit(val):
                raise Unsupported("single quote in json value")
            if act == "json-add":
                expr = f"setpath({jq_lit(path)}; {jq_lit(val)})"
            else:
                parent, last = path[:-1], path[-1]
                expr = (f"if (getpath({jq_lit(parent)}) | has({jq_lit(last)})) "
                        f"then (setpath({jq_lit(path)}; {jq_lit(val)})) else . end")
            out.append(("Body Rewrite", f"http-{kind}-jq {pat} '{expr}'"))
        return out
    m = re.match(r"^(?:(request|response)-)?header-(add|del|replace|replace-regex)\s+(.*)$", rest)
    if m:
        kind = m.group(1) or "request"
        act, arg = m.group(2), m.group(3).split()
        step = {"del": 1, "add": 2, "replace": 2, "replace-regex": 3}[act]
        if not arg or len(arg) % step:
            raise Unsupported(f"header-{act} argument count")
        return [("Header Rewrite", f"http-{kind} {pat} header-{act} " + " ".join(arg[i:i + step]))
                for i in range(0, len(arg), step)]
    if a0 == "mock-response-body":
        return [("Map Local", conv_mock(pat, rest[len(toks[0]):], notes))]
    raise Unsupported("unsupported rewrite action: " + toks[0])


def conv_loon_if(line, notes):
    """Loon 'response if ${url} ~= /re/flags then ...' expressions (simple forms only)."""
    m = re.match(r"^(request|response)\s+if\s+\$\{url\}\s*~=\s*/(.+)/([a-z]*)\s+then\s+(.+)$", line)
    if not m:
        raise Unsupported("unsupported Loon expression")
    kind, regex, flags, action = m.groups()
    if set(flags) - {"i"}:
        raise Unsupported("regex flags " + flags)
    if "i" in flags:
        regex = "(?i)" + regex
    sm = re.match(r'^script\("([^"]+)"\)(?:\s+with\s+(.+))?$', action)
    if sm:
        opts = {}
        for p in split_top(sm.group(2) or ""):
            if p:
                k, _, v = p.partition("=")
                opts[k.strip().replace("_", "-")] = v.strip()
        opts["script-path"] = sm.group(1)
        return "script", (f"http-{kind}", regex, opts)
    rm = re.fullmatch(r"reject_(dict|array|200|img)\((?:200)?\)", action.strip())
    if rm:
        return "lines", [("URL Rewrite", f"{regex} - reject-{rm.group(1)}")]
    jm = re.fullmatch(r'(request|response)\.json\.(jq|delete)\((".*"|\[.*\])\)', action.strip())
    if jm and jm.group(1) == kind:
        try:
            arg = json.loads(jm.group(3))
        except ValueError:
            raise Unsupported("cannot parse " + jm.group(2) + " argument")
        if jm.group(2) == "jq":
            if "'" in arg:
                raise Unsupported("single quote in jq expression")
            return "lines", [("Body Rewrite", f"http-{kind}-jq {regex} '{arg}'")]
        paths = arg if isinstance(arg, list) else [arg]
        return "lines", [("Body Rewrite", f"http-{kind}-jq {regex} 'delpaths([{jq_lit(parse_json_path(x))}])'")
                         for x in paths]
    acts = [a.strip() for a in re.split(r"\s+\|\s+", action)]
    tm = re.match(r'^response\.body\.mock\("(\w+)",\s*(".*")(?:,\s*(\d+))?\)$', acts[0])
    if kind == "response" and tm and len(acts) == 1 and tm.group(3) in (None, "200"):
        ct = MOCK_CT.get(tm.group(1).lower())
        data = json.loads(tm.group(2))
        if not ct or "\n" in data:
            raise Unsupported("mock type " + tm.group(1))
        notes.append("Loon response.body.mock(...) -> [Map Local] data-type=text")
        return "lines", [("Map Local", f'{regex} data-type=text data="{data}" header="Content-Type:{ct}"')]
    fm = re.match(r'^response\.body\.mock_file\("(\w+)",\s*"([^"]+)"(?:,\s*(\d+))?\)$', acts[0])
    if kind == "response" and fm:
        headers = []
        ct = MOCK_CT.get(fm.group(1).lower())
        if ct:
            headers.append("Content-Type:" + ct)
        if fm.group(3) not in (None, "200"):
            raise Unsupported("mock status " + fm.group(3))
        for a in acts[1:]:
            hm = re.match(r'^response\.header\.set\("([^"|:]+)",\s*"([^"|]*)"\)$', a)
            if not hm:
                raise Unsupported("unsupported action " + a)
            headers.append(f"{hm.group(1)}:{hm.group(2)}")
        out = f'{regex} data-type=file data="{fm.group(2)}"'
        if headers:
            out += ' header="' + "|".join(headers) + '"'
        notes.append("Loon response.body.mock_file(...) -> [Map Local] data-type=file")
        return "lines", [("Map Local", out)]
    raise Unsupported("unsupported Loon expression action")


# ---------------------------------------------------------------- scripts
def parse_script_opts(rest):
    """Split 'k=v, k2=v2' only before known keys (values may contain commas)."""
    cuts, depth, q = [0], 0, False
    key_re = re.compile(r",\s*(?:%s)\s*=" % SCRIPT_KEYS)
    for i, ch in enumerate(rest):
        if ch == '"':
            q = not q
        elif not q and ch in "[{(":
            depth += 1
        elif not q and ch in "]})":
            depth -= 1
        elif ch == "," and not q and depth == 0 and key_re.match(rest, i):
            cuts.append(i)
    cuts.append(len(rest))
    opts = {}
    for a, b in zip(cuts, cuts[1:]):
        seg = rest[a:b].lstrip(",").strip()
        if not seg:
            continue
        k, eq, v = seg.partition("=")
        k = k.strip().lower()
        if not eq or not re.fullmatch(SCRIPT_KEYS, k):
            raise Unsupported("unknown script option: " + seg)
        opts[k] = v.strip()
    return opts


def conv_argument(v, args):
    """Loon argument -> Shadowrocket argument string."""
    m = re.fullmatch(r"\[(.*)\]", v.strip())
    if m:
        keys = []
        for item in split_top(m.group(1)):
            km = re.fullmatch(r"\{([^{}]+)\}", item.strip())
            if not km or km.group(1) not in args:
                raise Unsupported("argument list item " + item + " is not a declared {Argument}")
            keys.append(km.group(1))
        # Loon hands the script an object; Shadowrocket hands it a string, so we
        # pass a query string (key=value&...), which is what both tieba-proto.js
        # and the Biliverse bundle (and most kelee scripts) parse.
        return '"' + "&".join(f"{k}={{{k}}}" for k in keys) + '"', True
    v = v.strip()
    if "," in v and not (v.startswith('"') and v.endswith('"')):
        v = '"' + v + '"'
    return v, False


def conv_script(line, args, notes, names):
    if re.match(r"^(request|response)\s+if\s", line):
        kind, payload = conv_loon_if(line, notes)
        if kind == "lines":
            if len(payload) != 1:
                raise Unsupported("expression expands to several lines inside [Script]")
            return payload[0]
        typ, pat, opts = payload
        notes.append("Loon conditional script(...) expression -> type=" + typ)
    else:
        m = re.match(r'^(http-request|http-response)\s+(\S+)\s+(.*)$', line)
        cm = re.match(r'^cron\s+("[^"]+"|\{[^}]+\}|\S+)\s+(.*)$', line)
        if m:
            typ, pat, rest = m.groups()
            opts = parse_script_opts(rest)
        elif cm:
            typ, pat = "cron", strip_q(cm.group(1))
            opts = parse_script_opts(cm.group(2))
        else:
            raise Unsupported("unsupported script type " + line.split()[0])
    if "script-path" not in opts:
        raise Unsupported("script without script-path")
    spath = strip_q(opts.pop("script-path"))
    fallback = re.sub(r"\.[^.]*$", "", spath.rsplit("/", 1)[-1]) or "script"
    name = re.sub(r"[=,]", "", strip_q(opts.pop("tag", ""))).strip() or fallback
    base, n = name, 2
    while name in names:
        name = f"{base}_{n}"; n += 1
    names.add(name)
    enable = opts.pop("enable", None)
    commented = False
    if enable is not None:
        em = re.fullmatch(r"\{([^{}]+)\}", strip_q(enable))
        val = args[em.group(1)]["default"] if em and em.group(1) in args else strip_q(enable)
        if val.lower() in ("false", "0"):
            commented = True
            notes.append(f"script '{name}': enable={enable} (default off) -> emitted commented out; "
                         "Shadowrocket has no per-script enable switch")
        else:
            notes.append(f"script '{name}': enable={enable} dropped (Shadowrocket has no per-script enable)")
    if opts.pop("img-url", None) is not None:
        notes.append(f"script '{name}': img-url dropped")
    out = [f"type={typ}"]
    if typ == "cron":
        out.append(f'cronexp="{pat}"')
    else:
        out.append("pattern=" + (f'"{pat}"' if "," in pat else pat))
    if truthy(opts.pop("requires-body", "false")):
        out.append("requires-body=1")
    if truthy(opts.pop("binary-body-mode", "false")):
        out.append("binary-body-mode=1")
    out.append("script-path=" + spath)
    for k in ("timeout", "max-size", "script-update-interval"):
        if k in opts:
            out.append(f"{k}={strip_q(opts.pop(k))}")
    if "argument" in opts:
        a, was_list = conv_argument(opts.pop("argument"), args)
        if was_list:
            n_keys = a.count("&") + 1
            notes.append(f"argument=[{{..}}] (object in Loon) -> query string k={{{{{{k}}}}}}&... "
                         f"({n_keys} keys)")
        out.append("argument=" + a)
    for k in ("engine", "wake-system", "debug", "ability", "cronexp", "cronexpr", "pattern"):
        opts.pop(k, None)
    if opts:
        raise Unsupported("unhandled script options " + ",".join(opts))
    text = f"{name} = " + ",".join(out)
    return "Script", ("# [DISABLED: enable=false] " + text) if commented else text


# ---------------------------------------------------------------- [Argument]
def parse_argument(line):
    k, eq, rest = line.partition("=")
    if not eq:
        raise Unsupported("argument without '='")
    key = k.strip()
    tm = re.search(r",\s*tag\s*=", rest)
    dm = re.search(r",\s*desc\s*=", rest)
    cut = min([m.start() for m in (tm, dm) if m] or [len(rest)])
    vals = split_top(rest[:cut])
    typ = vals[0].strip().lower()
    if typ not in ("switch", "input", "select"):
        raise Unsupported("argument type " + typ)
    values = [strip_q(v) for v in vals[1:]]
    tag = desc = ""
    if tm:
        tag = rest[tm.end():dm.start() if dm and dm.start() > tm.end() else len(rest)].strip()
    if dm:
        desc = rest[dm.end():tm.start() if tm and tm.start() > dm.end() else len(rest)].strip()
    return key, {"type": typ, "values": values, "default": values[0] if values else "",
                 "tag": strip_q(tag), "desc": strip_q(desc)}


def sr_arg_value(v):
    if v == "":
        return '""'
    if re.search(r'[,:"]', v):
        return '"' + v.replace('"', "") + '"'
    return v


# ---------------------------------------------------------------- main
def parse_plugin(text):
    header, sections, order, cur = {}, {}, [], None
    for raw in text.splitlines():
        line = raw.strip()
        if cur is None and line.startswith("#!"):
            k, _, v = line[2:].partition("=")
            header[k.strip().lower()] = v.strip()
            continue
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            cur = m.group(1).strip().lower()
            if cur not in sections:
                order.append(cur); sections[cur] = []
            continue
        if cur is not None:
            sections[cur].append(line)
    return header, sections, order


def convert(text, url):
    if text.startswith("\ufeff"):
        text = text[1:]
    header, sections, order = parse_plugin(text)
    stats = {"rule": 0, "url_rewrite": 0, "header_rewrite": 0, "body_rewrite": 0, "map_local": 0,
             "script": 0, "host": 0, "mitm_hosts": 0, "argument": 0, "general": 0,
             "src_entries": 0, "unconverted": 0}
    unconverted, notes, names = [], [], set()

    # [Argument] first: every other section may reference {key}
    args = {}
    for l in sections.get("argument", []):
        if not l or l.startswith("#") or l.startswith("//"):
            continue
        stats["src_entries"] += 1
        try:
            k, a = parse_argument(l)
            args[k] = a; stats["argument"] += 1
        except Unsupported as e:
            unconverted.append(("argument", f"{l}  ({e})")); stats["unconverted"] += 1

    out_sec = {}
    pending = []

    def add(sec, l):
        out_sec.setdefault(sec, []).extend(pending); pending.clear()
        out_sec.setdefault(sec, []).append(l)

    count_key = {"Rule": "rule", "URL Rewrite": "url_rewrite", "Header Rewrite": "header_rewrite",
                 "Body Rewrite": "body_rewrite", "Map Local": "map_local", "Script": "script",
                 "Host": "host", "General": "general"}
    general_append = {"real-ip": "always-real-ip", "always-real-ip": "always-real-ip",
                      "skip-proxy": "skip-proxy", "force-http-engine-hosts": "force-http-engine-hosts",
                      "bypass-tun": "tun-excluded-routes"}
    for sec in order:
        if sec == "argument":
            continue
        target = {"rule": "Rule", "rewrite": "URL Rewrite", "script": "Script", "host": "Host",
                  "mitm": "MITM", "general": "General"}.get(sec)
        for l in sections[sec]:
            if target is None:
                if l and not l.startswith("#"):
                    stats["src_entries"] += 1; stats["unconverted"] += 1
                    unconverted.append((sec, l + "  (unsupported section)"))
                    add("Rule", f"# [UNCONVERTED][{sec}] {l}")
                continue
            if not l:
                if target != "MITM":
                    pending.append("")
                continue
            if l.startswith("#") or l.startswith("//"):
                if target != "MITM":
                    pending.append("#" + l.lstrip("#/"))
                continue
            stats["src_entries"] += 1
            try:
                if sec == "rule":
                    res = [("Rule", conv_rule(l))]
                elif sec == "rewrite":
                    if re.match(r"^(request|response)\s+if\s", l):
                        kind, payload = conv_loon_if(l, notes)
                        if kind != "lines":
                            raise Unsupported("script() inside [Rewrite]")
                        res = payload
                    else:
                        res = conv_rewrite(l, notes)
                elif sec == "script":
                    res = [conv_script(l, args, notes, names)]
                elif sec == "host":
                    res = [("Host", re.sub(r"\s*=\s*", " = ", l, count=1))]
                elif sec == "general":
                    k, eq, v = l.partition("=")
                    k = k.strip().lower()
                    if not eq or k not in general_append:
                        raise Unsupported("unsupported [General] key")
                    vals = [x.strip() for x in v.split(",") if x.strip()]
                    res = [("General", f"{general_append[k]} = %APPEND% " + ", ".join(vals))]
                elif sec == "mitm":
                    k, _, v = l.partition("=")
                    k = k.strip().lower()
                    if k == "hostname":
                        hosts = [h.strip() for h in v.split(",") if h.strip()]
                        stats["mitm_hosts"] += len(hosts)
                        res = [("MITM", "hostname = %APPEND% " + ", ".join(hosts))] if hosts else []
                    elif k == "h2":
                        res = [("MITM", "h2 = " + ("true" if truthy(v) else "false"))]
                        notes.append("[MitM] h2 kept (Shadowrocket >= 2.2.81 supports h2 in [MITM])")
                    else:
                        raise Unsupported("unsupported MITM key " + k)
                for s, c in res:
                    add(s, c)
                    if not c.startswith("#"):
                        stats[count_key.get(s, "rule")] += 1 if s != "MITM" else 0
            except Unsupported as e:
                stats["unconverted"] += 1
                unconverted.append((sec, f"{l}  ({e})"))
                add(target if target not in ("MITM", "General") else "Rule", f"# [UNCONVERTED] {l}")
        if target and target != "MITM" and pending:
            out_sec.setdefault(target, []).extend(pending); pending.clear()
        pending.clear()

    for k, v in out_sec.items():          # collapse repeated / leading / trailing blanks
        res = []
        for l in v:
            if l == "" and (not res or res[-1] == ""):
                continue
            res.append(l)
        while res and res[-1] == "":
            res.pop()
        out_sec[k] = res

    kelee = "kelee.one" in url
    desc = header.get("desc", "").replace("\\n\\n", " ").replace("\\n", " ")
    desc += "（转换自 kelee.one Loon 插件，原作者见 author）" if kelee else "（转换自 Loon 插件，原作者见 author）"
    tags = header.get("tag", "")
    category = tags.split(",")[0].strip() if tags else "去广告"
    h = [f"#!name={header.get('name', '')}",
         f"#!desc={desc}",
         f"#!author={header.get('author', '')}；Shadowrocket 转换: ack20a",
         f"#!homepage={header.get('homepage', 'https://hub.kelee.one')}"]
    if header.get("icon"):
        h.append(f"#!icon={header['icon']}")
    h.append(f"#!category={category}")
    if args:
        h.append("#!arguments=" + ",".join(f"{k}:{sr_arg_value(a['default'])}" for k, a in args.items()))
        d = []
        for k, a in args.items():
            opts = f"（可选: {' / '.join(x if x else '空' for x in a['values'])}）" if a["type"] == "select" else ""
            d.append(f"{k}: {a['tag']}{opts}" + (f"\\n{a['desc']}" if a["desc"] else ""))
        h.append("#!arguments-desc=" + "\\n\\n".join(d))
    elif header.get("arguments"):
        h.append("#!arguments=" + header["arguments"])
        if header.get("arguments-desc"):
            h.append("#!arguments-desc=" + header["arguments-desc"])
    h += ["", f"# 上游来源: {url}", f"# 上游日期: {header.get('date', 'N/A')}"]
    if header.get("version"):
        h.append(f"# 上游版本: {header['version']}")
    h += [f"# 上游 Loon 最低版本: {header.get('loon_version', 'N/A')}",
          f"# 上游标签: {tags}",
          "# 由 scripts/lpx2sgmodule.py 自动转换，请勿手动修改，同步时重新生成。"]
    body = []
    for sec in ("General", "Rule", "URL Rewrite", "Header Rewrite", "Body Rewrite", "Map Local",
                "Script", "Host", "MITM"):
        if out_sec.get(sec):
            body += ["", f"[{sec}]"] + out_sec[sec]
    result = "\n".join(h + body) + "\n"
    # Loon {key} -> Shadowrocket {{{key}}} for declared arguments only (never touches regex {6})
    for k in sorted(args, key=len, reverse=True):
        result = re.sub(r"(?<!\{)\{" + re.escape(k) + r"\}(?!\})", "{{{" + k + "}}}", result)
    return result, stats, unconverted, sorted(set(notes))


def main(src, dst, url):
    with open(src, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    result, stats, unconverted, notes = convert(text, url)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"{dst}: {stats}", file=sys.stderr)
    for n in notes:
        print("NOTE:", n, file=sys.stderr)
    for u in unconverted:
        print("UNCONVERTED:", u, file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:4])
