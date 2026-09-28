# sr-ad-plugins

从 Loon 插件转换而来的 **Shadowrocket 模块 (`.sgmodule`)**，来源包括 [可莉 (kelee.one)](https://hub.kelee.one/) 和 [Biliverse](https://github.com/Biliverse/ADBlock)。

规则和脚本的版权归原作者所有。本仓库只做格式转换，不改规则内容；脚本仍然从原作者的地址加载。

## 模块列表

| 模块 | 说明 | 安装链接 (Shadowrocket → 配置 → 模块 → 右上角 + ) | 上游来源 | 原作者 |
| --- | --- | --- | --- | --- |
| 广告平台拦截器 | 拦截各大广告平台/SDK 的广告与统计请求，是其他去广告模块的依赖，建议排在最前面 | `https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/BlockAdvertisers.sgmodule` | [BlockAdvertisers.lpx](https://kelee.one/Tool/Loon/Lpx/BlockAdvertisers.lpx) | [可莉🅥](https://github.com/luestr/ProxyResource/blob/main/README.md) |
| HTTPDNS拦截器 | 拦截常见的 HTTPDNS 服务，让 App 的域名解析回到代理工具的 DNS 框架里，是其他去广告模块的依赖 | `https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/Block_HTTPDNS.sgmodule` | [Block_HTTPDNS.lpx](https://kelee.one/Tool/Loon/Lpx/Block_HTTPDNS.lpx) | 可莉🅥、[VirgilClyne](https://github.com/VirgilClyne) |
| 百度贴吧去广告 | 移除开屏、信息流和帖内广告，精简侧拉抽屉和「我的」页面 | `https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/Tieba_remove_ads.sgmodule` | [Tieba_remove_ads.lpx](https://kelee.one/Tool/Loon/Lpx/Tieba_remove_ads.lpx) | 可莉🅥、[app2smile](https://github.com/app2smile) |
| 📺 BiliBili: 🛡️ ADBlock | 哔哩哔哩去广告，可以自定义去除 App 内的界面元素 | `https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/BiliBili.ADBlock.sgmodule` | [BiliBili.ADBlock.plugin（最新 release）](https://github.com/Biliverse/ADBlock/releases/latest/download/BiliBili.ADBlock.plugin)，[项目主页](https://biliverse.github.io/guide/ad-block) | [Biliverse](https://github.com/Biliverse/ADBlock)：[ClydeTime](https://github.com/ClydeTime)、[VirgilClyne](https://github.com/VirgilClyne)、[app2smile](https://github.com/app2smile)、[RuCu6](https://github.com/RuCu6)、[Maasea](https://github.com/Maasea) |

直接点击：

- [BlockAdvertisers.sgmodule](https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/BlockAdvertisers.sgmodule)
- [Block_HTTPDNS.sgmodule](https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/Block_HTTPDNS.sgmodule)
- [Tieba_remove_ads.sgmodule](https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/Tieba_remove_ads.sgmodule)
- [BiliBili.ADBlock.sgmodule](https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/BiliBili.ADBlock.sgmodule)

> Biliverse 官方也发布了 Surge 版模块 [BiliBili.ADBlock.sgmodule](https://github.com/Biliverse/ADBlock/releases/latest/download/BiliBili.ADBlock.sgmodule)。本仓库的版本是从它的 Loon 插件转换来的，和 Loon 版的内容一一对应。

## 使用注意

- 模块里有 `[URL Rewrite]`、`[Body Rewrite]`、`[Map Local]`、`[Script]` 和 `[MITM]`，所以要在 Shadowrocket 里**生成并信任 HTTPS 解密证书**，并打开 HTTPS 解密。
- Shadowrocket 默认只在「全局路由 = 配置」时执行 `reject` 类 URL 重写。想在代理、直连模式下也生效，可以在配置的 `[General]` 里加上 `always-reject-url-rewrite = true`。
- 两个拦截器模块都是「依赖」类，建议放在模块列表的最上面。
- **模块参数**：贴吧和 BiliBili 模块带有 `#!arguments` 参数，对应 Loon 插件里的 `[Argument]`，默认值和 Loon 一致。可以在 Shadowrocket 的模块详情里修改。
- BiliBili 模块的 `[MITM]` 带有 `h2 = true`，也就是通过 HTTP/2 解密，gRPC 接口需要它。这需要 Shadowrocket 2.2.81 或以上版本。
- `[Body Rewrite]` 里的 `http-response-jq` 需要较新版本的 Shadowrocket。

## 转换时做的适配

| Loon | Shadowrocket | 说明 |
| --- | --- | --- |
| `[Argument]` `key = switch/select/input, 默认值, …, tag=, desc=` | `#!arguments=key:默认值,…` + `#!arguments-desc=` | 默认值取第一个值；正文里的 `{key}` 换成 `{{{key}}}`，只替换声明过的 key，不会碰正则里的 `{6}` 这类写法 |
| 脚本 `argument=[{a},{b}]` | `argument="a={{{a}}}&b={{{b}}}"` | Loon 传给脚本的是对象，Shadowrocket 只能传字符串，所以改成 `k=v&k2=v2` 查询串。`tieba-proto.js` 和 Biliverse 的脚本都会解析这种查询串，Biliverse 官方的 Surge 模块用的也是这种格式 |
| `mock-response-body data-type=json data="…"` | `[Map Local]` `data-type=text data="…" header="Content-Type:application/json"` | `data-path=` 转成 `data-type=file`，base64 转成 `data-type=base64`。Shadowrocket 的 Map Local 没有 `status-code` 参数，所以只转换 200（也就是默认值）的情况 |
| `response if ${url} ~= /re/ then response.body.mock_file("json", URL, 200) \| response.header.set(…)` | `[Map Local]` `re data-type=file data="URL" header="…"` | BiliBili 的 BoxJS 设置页用的就是这种写法 |
| `response-body-json-del k1 k2 …` | `[Body Rewrite]` 每个 key 一行 `http-response-jq re 'delpaths([[…]])'` | 路径解析规则和 Script-Hub 一致，支持 `a.b`、`[0]`、`["x.y"]` |
| `response-body-json-replace k v …` | `http-response-jq re 'if (getpath(父路径) \| has(key)) then setpath(…) else . end'` | 和 Loon 一样只替换已经存在的字段 |
| `response-body-json-jq '…'` | `http-response-jq re '…'` | 原样保留。`jq-path=`（外部 jq 文件）暂不支持 |
| `regex - reject-dict` / `regex reject-dict` | `[URL Rewrite]` `regex - reject-dict` | `reject-video`、`reject-tinygif` 转成 `reject-img` |
| `[Rule]` `TYPE, value, POLICY` | `TYPE,value,POLICY` | 去掉空格和多余的引号；逻辑规则保留嵌套结构，支持 `PROTOCOL` 子规则；`DEST-PORT` 转成 `DST-PORT` |
| `[Script]` `http-response re script-path=…, requires-body=true, tag=名称` | `名称 = type=http-response,pattern=re,requires-body=1,…` | 重名的脚本会自动加上 `_2`、`_3` 这样的后缀；`cron "表达式"` 转成 `type=cron,cronexp=`；`enable=` 和 `img-url=` 在 Shadowrocket 里没有对应，会被去掉（默认关闭的脚本会被注释掉） |
| `[MitM] hostname=` / `h2=true` | `[MITM] hostname = %APPEND% …` / `h2 = true` | |
| `#!name/desc/author/homepage/icon` | 原样保留，author 后面加上转换说明 | `#!tag` 的第一个标签作为 `#!category`；`#!date`、`#!version`、`#!loon_version` 以注释的形式写在头部 |

**不支持的内容**：`generic` 脚本、`jq-path=`、非 200 的 mock 状态码、复杂的 Loon 表达式（比如 `as item`、`reject(404)`）等。这些条目会以 `# [UNCONVERTED]` 注释的形式留在模块里，同时输出到 stderr，不会被悄悄丢掉。本仓库现有的 4 个模块里没有这样的条目。

## 目录结构

```
modules/    转换后的 Shadowrocket 模块
upstream/   上游 Loon 插件原文件（方便以后 diff 同步）
scripts/    转换脚本 lpx2sgmodule.py 及测试 test_lpx2sgmodule.py
```

## 同步上游

```bash
UA="Loon/867 CFNetwork/1498.700.2 Darwin/23.6.0"   # kelee.one 会拦截非代理工具的 UA
for f in BlockAdvertisers Block_HTTPDNS Tieba_remove_ads; do
  u=https://kelee.one/Tool/Loon/Lpx/$f.lpx
  curl -sSL -A "$UA" -o upstream/$f.lpx "$u"
  python3 scripts/lpx2sgmodule.py upstream/$f.lpx modules/$f.sgmodule "$u"
done
u=https://github.com/Biliverse/ADBlock/releases/latest/download/BiliBili.ADBlock.plugin
curl -sSL -o upstream/BiliBili.ADBlock.plugin "$u"
python3 scripts/lpx2sgmodule.py upstream/BiliBili.ADBlock.plugin modules/BiliBili.ADBlock.sgmodule "$u"
python3 scripts/test_lpx2sgmodule.py   # 条目计数对账 + 边界用例
git diff
```

测试会逐个模块核对「上游条目数 = 转换后的条目数 + UNCONVERTED 条目数」（规则、重写、脚本、MITM 主机、参数），并检查 `modules/` 里的文件和重新生成的结果完全一致。

## 致谢

- 规则/脚本作者：[可莉🅥](https://github.com/luestr/ProxyResource/blob/main/README.md)、[VirgilClyne](https://github.com/VirgilClyne)（HTTPDNS）、[app2smile](https://github.com/app2smile)（贴吧）
- BiliBili ADBlock：[Biliverse/ADBlock](https://github.com/Biliverse/ADBlock)（ClydeTime、VirgilClyne、app2smile、RuCu6、Maasea）
- 插件中心：<https://hub.kelee.one/>
- 转换规则参考：[Script-Hub](https://github.com/Script-Hub-Org/Script-Hub)
