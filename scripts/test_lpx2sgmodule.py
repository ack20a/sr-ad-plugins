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


def expected_rewrite_outputs(line):
    """Independent expectation: how many module lines one Loon rewrite line becomes."""
    m = re.search(r"\s(?:request|response)-body-json-(del|replace|add)\s+(.*)$", line)
    if m:
        n = len(m.group(2).split())
        return n if m.group(1) == "del" else n // 2
    return 1


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

        # rewrites -> URL Rewrite + Body Rewrite + Header Rewrite + Map Local (+ scripts' Map Local)
        up_rw = entries(up.get("rewrite", []))
        exp = sum(expected_rewrite_outputs(l) for l in up_rw
                  if not any(l in u for s, u in unconv if s == "rewrite"))
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
        self.assertEqual(paths([l for l in up_sc if "script-path" in l]), paths(live("script")))
        names = [l.split(" = ", 1)[0] for l in live("script")]
        self.assertEqual(len(names), len(set(names)))

        # MITM hosts
        uh = [h.strip() for l in up.get("mitm", []) if l.lower().startswith("hostname")
              for h in l.split("=", 1)[1].split(",") if h.strip()]
        sh = [h.strip() for l in sg.get("mitm", []) if l.startswith("hostname")
              for h in l.split("%APPEND%", 1)[1].split(",") if h.strip()]
        self.assertEqual(uh, sh)
        self.assertEqual(any(l.replace(" ", "") == "h2=true" for l in up.get("mitm", [])),
                         "h2 = true" in sg.get("mitm", []))

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
        self.assertEqual((s["rule"], s["url_rewrite"], s["body_rewrite"], s["map_local"], s["script"],
                          s["mitm_hosts"], s["argument"], len(u)), (2, 5, 77, 1, 1, 2, 1, 0))

    def test_bilibili(self):
        s, u = self.check("BiliBili.ADBlock")
        self.assertEqual((s["url_rewrite"], s["body_rewrite"], s["map_local"], s["script"],
                          s["mitm_hosts"], s["argument"], len(u)), (6, 1, 2, 16, 13, 31, 0))


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
        self.assertEqual(section(out, "Body Rewrite"), [
            "http-response-jq ^https://a 'delpaths([[\"a\",\"b\"]])'",
            "http-response-jq ^https://a 'delpaths([[\"c\",0]])'",
            "http-response-jq ^https://a 'delpaths([[\"d\",\"e.f\"]])'",
            "http-response-jq ^https://a 'if (getpath([\"x\"]) | has(\"y\")) then (setpath([\"x\",\"y\"]; 0)) else . end'",
            "http-response-jq ^https://a 'if (getpath([]) | has(\"z\")) then (setpath([\"z\"]; \"s t\")) else . end'",
            "http-response-jq ^https://a 'del(.ad)'",
            "http-request ^https://a foo bar"])

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

    def test_mitm_h2_empty_and_general(self):
        out, s, u, _ = conv("[General]\nreal-ip = *.a.com, b.com\n[MitM]\nhostname =\nh2 = true\n")
        self.assertEqual(section(out, "MITM"), ["h2 = true"])
        self.assertEqual(section(out, "General"), ["always-real-ip = %APPEND% *.a.com, b.com"])

    def test_unknown_section_is_kept_commented(self):
        out, s, u, _ = conv("[Foo]\nbar\n")
        self.assertIn("# [UNCONVERTED][foo] bar", out)
        self.assertEqual(len(u), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
