#!/usr/bin/env python3
"""Tests for lpx2sgmodule.py.   Run:  python3 scripts/test_lpx2sgmodule.py

1. Round-trip: for every upstream/<name>.(lpx|plugin) the generated module must be
   byte-identical to modules/<name>.sgmodule, and an *independent* count of the
   upstream entries must equal converted entries + UNCONVERTED entries
   (rules, rewrites, scripts, MITM hosts, arguments).
2. Unit tests for the edge cases found in the audit.
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import lpx2sgmodule as C  # noqa: E402
import merge_sgmodule as M  # noqa: E402

SOURCES = {
    "BlockAdvertisers": ("BlockAdvertisers.lpx", "https://kelee.one/Tool/Loon/Lpx/BlockAdvertisers.lpx"),
    "Block_HTTPDNS": ("Block_HTTPDNS.lpx", "https://kelee.one/Tool/Loon/Lpx/Block_HTTPDNS.lpx"),
    "Tieba_remove_ads": ("Tieba_remove_ads.lpx", "https://kelee.one/Tool/Loon/Lpx/Tieba_remove_ads.lpx"),
    "BiliBili.ADBlock": ("BiliBili.ADBlock.plugin",
                         "https://github.com/Biliverse/ADBlock/releases/latest/download/BiliBili.ADBlock.plugin"),
}


def sections(text):
    out, cur = {}, None
    for raw in text.splitlines():
        l = raw.strip()
        m = re.match(r"^\[(.+)\]$", l)
        if m:
            cur = m.group(1).lower(); out.setdefault(cur, []); continue
        if cur and l:
            out[cur].append(l)
    return out


def entries(lines):
    return [l for l in lines if not l.startswith("#") and not l.startswith("//")]


class RoundTrip(unittest.TestCase):
    def check(self, name):
        src, url = SOURCES[name]
        with open(os.path.join(ROOT, "upstream", src), encoding="utf-8-sig") as f:
            up_text = f.read()
        out, stats, unconv, _ = C.convert(up_text, url)
        with open(os.path.join(ROOT, "modules", name + ".sgmodule"), encoding="utf-8") as f:
            self.assertEqual(out, f.read(), f"{name}: committed module is stale, regenerate it")
        up, sg = sections(up_text), sections(out)
        uc = {}
        for sec, _l in unconv:
            uc[sec] = uc.get(sec, 0) + 1
        live = lambda sec: [l for l in sg.get(sec, []) if not l.startswith("#")]  # noqa: E731

        # rules: count + normalized content
        up_rules = entries(up.get("rule", []))
        self.assertEqual(len(up_rules), len(live("rule")) + uc.get("rule", 0))
        norm = lambda r: re.sub(r"\s+|\"", "", r).upper().replace("DEST-PORT", "DST-PORT")  # noqa: E731
        conv = [r for r in up_rules if not any(r in u for s, u in unconv if s == "rule")]
        self.assertEqual([norm(r) for r in conv], [norm(r) for r in live("rule")])

        # rewrites -> URL Rewrite + Body Rewrite + Header Rewrite + Map Local (+ scripts' Map Local);
        # every Loon rewrite line becomes one line (json-del/replace with several keys: one jq)
        up_rw = entries(up.get("rewrite", []))
        exp = len([l for l in up_rw if not any(l in u for s, u in unconv if s == "rewrite")])
        got = sum(len(live(s)) for s in ("url rewrite", "body rewrite", "header rewrite", "map local"))
        up_sc = entries(up.get("script", []))
        sc_maplocal = sum(1 for l in up_sc if "mock_file(" in l)
        self.assertEqual(exp + sc_maplocal, got)
        self.assertEqual(len(up_rw), stats["src_entries"] - len(up_rules) - len(up_sc)
                         - len(entries(up.get("mitm", []))) - len(entries(up.get("argument", [])))
                         - len(entries(up.get("host", []))) - len(entries(up.get("general", []))))

        # scripts: count, script-path multiset, unique names
        self.assertEqual(len(up_sc), len(live("script")) + sc_maplocal + uc.get("script", 0))
        paths = lambda ls: sorted(re.search(r"script-path=\s*([^,\s]+)", l).group(1) for l in ls)  # noqa: E731
        self.assertEqual(paths([l for l in up_sc if "script-path" in l
                                and not any(l in u for s, u in unconv if s == "script")]), paths(live("script")))
        names = [l.split(" = ", 1)[0] for l in live("script")]
        self.assertEqual(len(names), len(set(names)))

        # MITM hosts
        uh = [h.strip() for l in up.get("mitm", []) if l.lower().startswith("hostname")
              and not any(l in u for s, u in unconv if s == "mitm")
              for h in l.split("=", 1)[1].split(",") if h.strip()]
        sh = [h.strip() for l in sg.get("mitm", []) if l.startswith("hostname")
              for h in l.split("%APPEND%", 1)[1].split(",") if h.strip()]
        self.assertEqual(uh, sh)
        # h2 is never emitted; an upstream h2 = true leaves the H2_DROPPED comment instead
        self.assertFalse([l for l in sg.get("mitm", []) if l.replace(" ", "").lower().startswith("h2=")])
        self.assertEqual(any(l.replace(" ", "") == "h2=true" for l in up.get("mitm", [])),
                         C.H2_DROPPED in sg.get("mitm", []))

        # arguments: declared == #!arguments keys; no leaked Loon {key}; every {{{k}}} declared
        keys = [l.split("=", 1)[0].strip() for l in entries(up.get("argument", []))]
        m = re.search(r"^#!arguments=(.*)$", out, re.M)
        sr_keys = [kv.split(":", 1)[0] for kv in C.split_top(m.group(1))] if m else []
        self.assertEqual(keys, sr_keys)
        for k in keys:
            self.assertNotRegex(out, r"(?<!\{)\{" + re.escape(k) + r"\}(?!\})")
        for k in re.findall(r"\{\{\{([^{}]+)\}\}\}", out):
            self.assertIn(k, keys)
        self.assertEqual(stats["unconverted"], len(unconv))
        return stats, unconv

    def test_block_advertisers(self):
        s, u = self.check("BlockAdvertisers")
        self.assertEqual((s["rule"], s["url_rewrite"], s["mitm_hosts"], len(u)), (345, 4, 3, 0))

    def test_block_httpdns(self):
        s, u = self.check("Block_HTTPDNS")
        self.assertEqual((s["rule"], s["url_rewrite"], s["mitm_hosts"], len(u)), (158, 6, 4, 0))

    def test_tieba(self):
        s, u = self.check("Tieba_remove_ads")
        # 6 json-del + 2 json-replace + 2 json-jq lines -> 10 jq lines (one per Loon line)
        self.assertEqual((s["rule"], s["url_rewrite"], s["body_rewrite"], s["map_local"], s["script"],
                          s["mitm_hosts"], s["argument"], len(u)), (2, 5, 10, 1, 1, 2, 1, 0))
        with open(os.path.join(ROOT, "modules", "Tieba_remove_ads.sgmodule"), encoding="utf-8") as f:
            text = f.read()
        # full module: Tieba only logs users out when Shadowrocket decrypts it over HTTP/2
        self.assertIn("hostname = %APPEND% tiebac.baidu.com, tieba.baidu.com", text)
        self.assertIn("要关闭 HTTP/2 解密", text.splitlines()[1])
        self.assertRegex(text, r"\nProtoBuf处理 = .*,max-size=-1,argument=")

    def test_no_module_enables_h2(self):
        # Shadowrocket's HTTP/2 MITM is global and a module's h2 overrides the config
        for name in sorted(os.listdir(os.path.join(ROOT, "modules"))):
            with open(os.path.join(ROOT, "modules", name), encoding="utf-8") as f:
                bad = [l for l in f.read().splitlines() if re.match(r"\s*h2\s*=", l, re.I)]
            self.assertEqual(bad, [], name)

    def test_bilibili(self):
        s, u = self.check("BiliBili.ADBlock")
        self.assertEqual((s["url_rewrite"], s["body_rewrite"], s["map_local"], s["script"],
                          s["mitm_hosts"], s["argument"], len(u)), (6, 1, 2, 16, 13, 31, 0))


class Merge(unittest.TestCase):
    def test_youtube(self):
        texts = {}
        for fname, _u, _l in M.MERGES["YouTube"]["sources"]:
            with open(os.path.join(ROOT, "upstream", fname), encoding="utf-8-sig") as f:
                texts[fname] = f.read()
        out = M.merge("YouTube", texts)
        with open(os.path.join(ROOT, "modules", "YouTube.sgmodule"), encoding="utf-8") as f:
            self.assertEqual(out, f.read(), "YouTube: committed module is stale, regenerate it")
        sg = sections(out)
        kelee = sections(C.convert(texts["YouTube_remove_ads.lpx"], M.MERGES["YouTube"]["sources"][0][1])[0])
        dual = sections(texts["DualSubs.YouTube.sgmodule"])
        live = lambda d, sec: [l for l in d.get(sec, []) if not l.startswith("#")]  # noqa: E731
        # every script of both sources, ad-block first (DualSubs guide: ad-block gets priority)
        self.assertEqual(live(sg, "script"), live(kelee, "script") + live(dual, "script"))
        self.assertEqual(len(live(sg, "script")), 3 + 12)
        # every upstream rule kept, plus the QUIC blocks
        for r in live(dual, "rule"):
            self.assertIn(r, live(sg, "rule"))
        self.assertIn("AND,((DOMAIN-SUFFIX,googlevideo.com),(PROTOCOL,UDP)),REJECT-NO-DROP", live(sg, "rule"))
        # MITM: union of both host lists (incl. the -redirector exclusion), no h2
        hosts = lambda d: [h.strip() for l in d.get("mitm", []) if l.lower().startswith("hostname")  # noqa: E731
                           for h in l.split("=", 1)[1].replace("%APPEND%", "").split(",") if h.strip()]
        self.assertEqual(set(hosts(sg)), set(hosts(kelee)) | set(hosts(dual)))
        self.assertIn("-redirector*.googlevideo.com", hosts(sg))
        self.assertEqual(live(sg, "mitm"), [l for l in live(sg, "mitm") if l.startswith("hostname")])
        # arguments: both sets, captionLang handed to DualSubs
        args = M.parse_arguments(re.search(r"^#!arguments=(.*)$", out, re.M).group(1))
        self.assertEqual(list(args), ["blockUpload", "blockShorts", "blockImmersive", "captionLang", "debug",
                                      "Type", "Types", "AutoCC", "Position", "Vendor", "ShowOnly", "LogLevel"])
        self.assertEqual(args["captionLang"], "off")
        for k in re.findall(r"\{\{\{([^{}]+)\}\}\}", out):
            self.assertIn(k, args)
        # the ad-block scripts JSON.parse($argument): must be valid JSON with real booleans
        import json
        for l in live(kelee, "script"):
            a = re.search(r',argument="(.*)"$', l).group(1)
            a = re.sub(r"\{\{\{([^{}]+)\}\}\}", lambda m: args[m.group(1)].strip('"'), a)
            obj = json.loads(a)
            self.assertIn("captionLang", obj)
            for k in ("blockUpload", "blockShorts", "debug"):
                if k in obj:
                    self.assertIsInstance(obj[k], bool, k)
            self.assertIn("max-size=-1", l)

    def test_duplicate_argument_is_an_error(self):
        recipe = dict(M.MERGES["YouTube"], sources=[("a.sgmodule", "u", "A"), ("b.sgmodule", "u", "B")],
                      argument_defaults={}, extra={})
        M.MERGES["_t"] = recipe
        try:
            with self.assertRaises(ValueError):
                M.merge("_t", {"a.sgmodule": "#!arguments=x:1\n[Rule]\nDOMAIN,a,REJECT\n",
                               "b.sgmodule": "#!arguments=x:2\n[Rule]\nDOMAIN,b,REJECT\n"})
        finally:
            del M.MERGES["_t"]

    def test_h2_from_sources_is_dropped(self):
        recipe = dict(M.MERGES["YouTube"], sources=[("a.sgmodule", "u", "A")], argument_defaults={}, extra={})
        M.MERGES["_t"] = recipe
        try:
            out = M.merge("_t", {"a.sgmodule": "[Rule]\nDOMAIN,a,REJECT\n[MITM]\nhostname = %APPEND% a.com\nh2 = true\n"})
            self.assertEqual(sections(out)["mitm"], ["hostname = %APPEND% a.com", C.H2_DROPPED])
            M.MERGES["_t"] = dict(recipe, extra={"MITM": ["h2 = true"]})
            with self.assertRaises(ValueError):
                M.merge("_t", {"a.sgmodule": "[Rule]\nDOMAIN,a,REJECT\n"})
        finally:
            del M.MERGES["_t"]


def conv(body, header="#!name=t\n", url="https://kelee.one/x.lpx"):
    return C.convert(header + body, url)


def section(out, name):
    return entries(sections(out).get(name.lower(), []))


class Units(unittest.TestCase):
    def test_script_argument_with_commas_not_truncated(self):
        out, s, u, _ = conv('[Script]\nhttp-request ^https://a\\.com/x script-path=https://s/a.js, '
                            'argument="a,b,c", tag=T, timeout=5\n')
        self.assertEqual(section(out, "Script"),
                         ['T = type=http-request,pattern=^https://a\\.com/x,script-path=https://s/a.js,'
                          'timeout=5,argument="a,b,c"'])
        self.assertEqual(u, [])

    def test_argument_section_and_list(self):
        out, s, u, _ = conv('[Argument]\nfoo=select, "true", "false", tag=Foo, desc=d, x\n'
                            'Bar.Baz = input,"",tag=B,desc=e\n'
                            '[Script]\nhttp-response ^https://a/\\d{6} script-path=https://s/a.js, '
                            'requires-body=true, argument=[{foo},{Bar.Baz}]\n')
        self.assertIn('#!arguments=foo:true,Bar.Baz:""', out)
        line = section(out, "Script")[0]
        self.assertTrue(line.endswith('argument="foo={{{foo}}}&Bar.Baz={{{Bar.Baz}}}"'), line)
        self.assertIn("\\d{6}", line)  # regex quantifier untouched
        self.assertEqual(u, [])

    def test_undeclared_argument_list_is_unconverted(self):
        out, s, u, _ = conv("[Script]\nhttp-response ^https://a script-path=https://s/a.js, argument=[{nope}]\n")
        self.assertEqual(len(u), 1)
        self.assertIn("# [UNCONVERTED]", out)

    def test_enable_false_comments_script(self):
        out, s, u, n = conv("[Argument]\nsw=switch,false,tag=x\n[Script]\nhttp-request ^https://a "
                            "script-path=https://s/a.js, enable={sw}, tag=A\n")
        self.assertIn("# [DISABLED: enable=false] A = type=http-request", out)

    def test_cron(self):
        out, s, u, _ = conv('[Script]\ncron "0 8 * * *" script-path=https://s/c.js, timeout=60, tag=Daily\n')
        self.assertEqual(section(out, "Script"),
                         ['Daily = type=cron,cronexp="0 8 * * *",script-path=https://s/c.js,timeout=60'])

    def test_generic_is_unconverted(self):
        out, s, u, _ = conv("[Script]\ngeneric script-path=https://s/g.js, tag=G\n")
        self.assertEqual(len(u), 1)

    def test_loon_conditional_script(self):
        out, s, u, _ = conv('[Script]\nresponse if ${url} ~= /^https:\\/\\/a\\.com\\/x/i then '
                            'script("https://s/a.js") with tag="T", requires_body=true, binary_body_mode=true\n')
        self.assertEqual(section(out, "Script"),
                         ["T = type=http-response,pattern=(?i)^https:\\/\\/a\\.com\\/x,requires-body=1,"
                          "binary-body-mode=1,script-path=https://s/a.js"])

    def test_script_name_dedupe_and_fallback(self):
        out, *_ = conv("[Script]\nhttp-request ^https://a script-path=https://s/a.js, tag=X\n"
                       "http-request ^https://b script-path=https://s/a.js, tag=X\n"
                       "http-request ^https://c script-path=https://s/my-script.js\n")
        self.assertEqual([l.split(" = ")[0] for l in section(out, "Script")], ["X", "X_2", "my-script"])

    def test_pattern_with_comma_is_quoted(self):
        out, *_ = conv("[Script]\nhttp-request ^https://a/\\d{1,3} script-path=https://s/a.js\n")
        self.assertIn('pattern="^https://a/\\d{1,3}"', out)

    def test_placeholder_dash_and_reject_video(self):
        out, s, u, _ = conv("[Rewrite]\n^https://a - reject-dict\n^https://b reject-video\n"
                            "^https://c https://d 302\n^https://e 307 https://f\n")
        self.assertEqual(section(out, "URL Rewrite"),
                         ["^https://a - reject-dict", "^https://b - reject-img",
                          "^https://c https://d 302", "^https://e https://f 307"])

    def test_mock_to_map_local(self):
        out, s, u, _ = conv('[Rewrite]\n^https://a mock-response-body data-type=json data="{"a":[1, 2]}" '
                            'status-code=200\n^https://b mock-response-body data-type=text data-path=https://x/y.txt\n'
                            '^https://c mock-response-body data-type=json data="e30=" mock-data-is-base64=true\n')
        self.assertEqual(section(out, "Map Local"), [
            '^https://a data-type=text data="{"a":[1, 2]}" header="Content-Type:application/json"',
            '^https://b data-type=file data="https://x/y.txt" header="Content-Type:text/plain"',
            '^https://c data-type=base64 data="e30=" header="Content-Type:application/json"'])
        out, s, u, _ = conv('[Rewrite]\n^https://a mock-response-body data-type=json data="{}" status-code=404\n')
        self.assertEqual(len(u), 1)  # Shadowrocket Map Local has no status-code

    def test_json_del_replace_jq(self):
        out, s, u, _ = conv("[Rewrite]\n^https://a response-body-json-del a.b c[0] d[\"e.f\"]\n"
                            "^https://a response-body-json-replace x.y 0 z \"s\\x20t\"\n"
                            "^https://a response-body-json-jq 'del(.ad)'\n"
                            "^https://a request-body-replace-regex foo bar\n")
        # one jq program per Loon line; every step is skipped when a parent is missing/not a container
        self.assertEqual(section(out, "Body Rewrite"), [
            "http-response-jq ^https://a '"
            'if type == "object" and (getpath(["a"]) | type) == "object" then delpaths([["a","b"]]) else . end | '
            'if type == "object" and (getpath(["c"]) | type) == "array" then delpaths([["c",0]]) else . end | '
            'if type == "object" and (getpath(["d"]) | type) == "object" then delpaths([["d","e.f"]]) else . end'
            "'",
            "http-response-jq ^https://a '"
            'if type == "object" and (getpath(["x"]) | type) == "object" and (getpath(["x"]) | has("y")) '
            'then setpath(["x","y"]; 0) else . end | '
            'if type == "object" and has("z") then setpath(["z"]; "s t") else . end'
            "'",
            "http-response-jq ^https://a 'del(.ad)'",
            "http-request ^https://a foo bar"])
        self.assertEqual(s["body_rewrite"], 4)

    def test_json_del_groups_keys_but_not_array_indices(self):
        # object keys under one parent share a delpaths; array indices stay separate steps
        # (sequential deletes: the second index applies to the already shortened array)
        out, *_ = conv("[Rewrite]\n^https://a response-body-json-del l[0] l[0] a b c.d e\n")
        self.assertEqual(section(out, "Body Rewrite"), [
            "http-response-jq ^https://a '"
            'if type == "object" and (getpath(["l"]) | type) == "array" then delpaths([["l",0]]) else . end | '
            'if type == "object" and (getpath(["l"]) | type) == "array" then delpaths([["l",0]]) else . end | '
            'if type == "object" then delpaths([["a"],["b"]]) else . end | '
            'if type == "object" and (getpath(["c"]) | type) == "object" then delpaths([["c","d"]]) else . end | '
            'if type == "object" then delpaths([["e"]]) else . end'
            "'"])

    def test_json_replace_array_index_guard(self):
        out, *_ = conv("[Rewrite]\n^https://a response-body-json-replace a[0] 1\n")
        self.assertIn('\'if type == "object" and (getpath(["a"]) | type) == "array" and (getpath(["a"]) | has(0)) '
                      'then setpath(["a",0]; 1) else . end\'', out)

    def test_empty_json_path_is_unconverted(self):
        # delpaths([[]]) / setpath([]; v) would wipe the whole body
        for act in ("json-del []", "json-replace [] 1", "json-add [] 1"):
            out, s, u, _ = conv(f"[Rewrite]\n^https://a response-body-{act}\n")
            self.assertEqual(len(u), 1, act)
            self.assertNotIn("[Body Rewrite]", out, act)

    def test_malformed_lines_do_not_abort_conversion(self):
        out, s, u, _ = conv('[Rule]\nAND, ((DOMAIN)), REJECT\nDOMAIN, a.com, REJECT\n'
                            '[Rewrite]\nresponse if ${url} ~= /^https:\\/\\/a/ then '
                            'response.body.mock("json", "\\x")\n')
        self.assertEqual(len(u), 2)
        self.assertEqual(section(out, "Rule"), ["DOMAIN,a.com,REJECT"])

    def test_homepage_default(self):
        out, *_ = conv("[Rule]\nDOMAIN, a.com, REJECT\n", url="https://example.com/x.plugin")
        self.assertIn("#!homepage=https://example.com/x.plugin", out)
        out, *_ = conv("[Rule]\nDOMAIN, a.com, REJECT\n")
        self.assertIn("#!homepage=https://hub.kelee.one", out)

    def test_local_disable_and_desc_note(self):
        body = ("[Rewrite]\n^https?:\\/\\/a\\.com\\/x$ reject-dict\n^http:\\/\\/b\\.com\\/y$ reject-dict\n"
                "[Script]\nhttp-response ^https?:\\/\\/a\\.com\\/z script-path=https://s/a.js\n"
                "[MitM]\nhostname=a.com\n")
        C.LOCAL_DISABLE["_t.lpx"] = [("mitm", r"^hostname\s*=", "R"), ("rewrite", r"^\^https\?", "R"),
                                     ("script", r"\s\^https\?", "R")]
        C.LOCAL_DESC_NOTE["_t.lpx"] = "【本地说明】"
        try:
            out, s, u, _ = conv(body, url="https://kelee.one/Tool/Loon/Lpx/_t.lpx")
        finally:
            del C.LOCAL_DISABLE["_t.lpx"], C.LOCAL_DESC_NOTE["_t.lpx"]
        self.assertEqual(section(out, "URL Rewrite"), ["^http:\\/\\/b\\.com\\/y$ - reject-dict"])
        self.assertEqual((section(out, "Script"), section(out, "MITM")), ([], []))
        self.assertIn("# [DISABLED: R] ", out)
        self.assertIn("【本地说明】", out.splitlines()[1])
        self.assertEqual((s["url_rewrite"], s["script"], len(u)), (1, 0, 3))
        out, s, u, _ = conv(body, url="https://kelee.one/Tool/Loon/Lpx/Other.lpx")  # other plugins untouched
        self.assertEqual((s["url_rewrite"], s["script"], len(u)), (2, 1, 0))
        self.assertEqual(section(out, "MITM"), ["hostname = %APPEND% a.com"])
        self.assertNotIn("【本地说明】", out)

    def test_json_argument_style_and_max_size(self):
        body = ("[Argument]\nsw=switch, false, true, tag=S\nlang=select, \"zh-Hans\", \"off\", tag=L\n"
                "[Script]\nhttp-response ^https://a script-path=https://s/a.js, requires-body=true, "
                "argument=[{sw},{lang}], tag=A\n")
        out, *_ = conv(body, url="https://kelee.one/Tool/Loon/Lpx/YouTube_remove_ads.lpx")
        self.assertEqual(section(out, "Script"), [
            'A = type=http-response,pattern=^https://a,requires-body=1,script-path=https://s/a.js,max-size=-1,'
            'argument="{"sw":{{{sw}}},"lang":"{{{lang}}}"}"'])
        out, *_ = conv(body)  # default: query string, no max-size
        self.assertTrue(section(out, "Script")[0].endswith('script-path=https://s/a.js,'
                                                          'argument="sw={{{sw}}}&lang={{{lang}}}"'))

    def test_header_rewrite(self):
        out, *_ = conv("[Rewrite]\n^https://a response-header-add X-A 1 X-B 2\n^https://b header-del Cookie\n")
        self.assertEqual(section(out, "Header Rewrite"), [
            "http-response ^https://a header-add X-A 1", "http-response ^https://a header-add X-B 2",
            "http-request ^https://b header-del Cookie"])

    def test_loon_expression_rewrites(self):
        out, s, u, _ = conv('[Rewrite]\nrequest if ${url} ~= /^https:\\/\\/a/i then reject_dict(200)\n'
                            'response if ${url} ~= /^https:\\/\\/b/ then response.json.jq(".a |= 1")\n'
                            'response if ${url} ~= /^https:\\/\\/c/ then response.body.mock_file("json", '
                            '"https://x/y.json", 200) | response.header.set("Cache-Control", "no-store")\n')
        self.assertEqual(section(out, "URL Rewrite"), ["(?i)^https:\\/\\/a - reject-dict"])
        self.assertEqual(section(out, "Body Rewrite"), ["http-response-jq ^https:\\/\\/b '.a |= 1'"])
        self.assertEqual(section(out, "Map Local"), [
            '^https:\\/\\/c data-type=file data="https://x/y.json" '
            'header="Content-Type:application/json|Cache-Control:no-store"'])

    def test_rules_quoted_comma_protocol_dst_port(self):
        out, s, u, _ = conv('[Rule]\nUSER-AGENT, "a,b*", REJECT\nDOMAIN, "x.com", reject\n'
                            'AND, ((PROTOCOL, QUIC), (DEST-PORT, 443)), REJECT-NO-DROP\n'
                            'DEST-PORT, 5228, DIRECT\nPROCESS-NAME, x, REJECT\n')
        self.assertEqual(section(out, "Rule"), [
            'USER-AGENT,"a,b*",REJECT', "DOMAIN,x.com,REJECT",
            "AND,((PROTOCOL,QUIC),(DST-PORT,443)),REJECT-NO-DROP", "DST-PORT,5228,DIRECT"])
        self.assertEqual(len(u), 1)  # PROCESS-NAME is not an iOS rule

    def test_bom_crlf_cr_and_header_spacing(self):
        text = "\ufeff#!name = N\r\n#!desc = D\r\n[Rule]\r\nDOMAIN, a.com, REJECT\rDOMAIN, b.com, REJECT\r\n"
        out, s, u, _ = C.convert(text, "https://kelee.one/x.lpx")
        self.assertTrue(out.startswith("#!name=N\n#!desc=D（"))
        self.assertEqual(section(out, "Rule"), ["DOMAIN,a.com,REJECT", "DOMAIN,b.com,REJECT"])

    def test_stats_reset_between_calls(self):
        conv("[Rule]\nDOMAIN, a.com, REJECT\nFOO, x, REJECT\n")
        _, s, u, _ = conv("[Rule]\nDOMAIN, a.com, REJECT\n")
        self.assertEqual((s["rule"], s["unconverted"], len(u)), (1, 0, 0))

    def test_mitm_h2_dropped_empty_and_general(self):
        out, s, u, _ = conv("[General]\nreal-ip = *.a.com, b.com\n[MitM]\nhostname =\nh2 = true\n")
        self.assertEqual(section(out, "MITM"), [])  # repo policy: HTTP/2 MITM stays off
        self.assertIn(C.H2_DROPPED, out)
        self.assertEqual(u, [])
        self.assertEqual(section(out, "General"), ["always-real-ip = %APPEND% *.a.com, b.com"])
        out, *_ = conv("[MitM]\nhostname = a.com\nh2 = false\n")
        self.assertEqual(sections(out)["mitm"], ["hostname = %APPEND% a.com"])

    def test_unknown_section_is_kept_commented(self):
        out, s, u, _ = conv("[Foo]\nbar\n")
        self.assertIn("# [UNCONVERTED][foo] bar", out)
        self.assertEqual(len(u), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
