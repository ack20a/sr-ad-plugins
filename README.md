# sr-ad-plugins

从 [可莉 (kelee.one)](https://hub.kelee.one/) 的 Loon 插件转换而来的 **Shadowrocket 模块 (`.sgmodule`)**。

规则的版权归原作者所有，本仓库只做格式转换，不改规则内容。

## 模块列表

| 模块 | 说明 | 安装链接 (Shadowrocket → 配置 → 模块 → 右上角 + ) | 上游来源 |
| --- | --- | --- | --- |
| 广告平台拦截器 | 拦截各大广告平台/SDK 的广告与统计请求，是其他去广告模块的依赖，建议排在最前面 | `https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/BlockAdvertisers.sgmodule` | [BlockAdvertisers.lpx](https://kelee.one/Tool/Loon/Lpx/BlockAdvertisers.lpx) |
| HTTPDNS拦截器 | 拦截常见的 HTTPDNS 服务，让 App 的域名解析回到代理工具的 DNS 框架里，是其他去广告模块的依赖 | `https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/Block_HTTPDNS.sgmodule` | [Block_HTTPDNS.lpx](https://kelee.one/Tool/Loon/Lpx/Block_HTTPDNS.lpx) |

直接点击：

- [BlockAdvertisers.sgmodule](https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/BlockAdvertisers.sgmodule)
- [Block_HTTPDNS.sgmodule](https://raw.githubusercontent.com/ack20a/sr-ad-plugins/main/modules/Block_HTTPDNS.sgmodule)

## 使用注意

- 模块里有 `[URL Rewrite]` 和 `[MITM]`，所以要在 Shadowrocket 里**生成并信任 HTTPS 解密证书**，并打开 HTTPS 解密。
- Shadowrocket 默认只在「全局路由 = 配置」时执行 `reject` 类 URL 重写。想在代理、直连模式下也生效，可以在配置的 `[General]` 里加上 `always-reject-url-rewrite = true`。
- 两个模块都是「依赖」类，建议放在模块列表的最上面。

## 目录结构

```
modules/    转换后的 Shadowrocket 模块
upstream/   上游 Loon 插件原文件（方便以后 diff 同步）
scripts/    转换脚本 lpx2sgmodule.py
```

## 转换规则

| Loon | Shadowrocket |
| --- | --- |
| `#!name/desc/author/homepage/icon` | 原样保留，author 后面加上转换说明；`#!tag` 的第一个标签作为 `#!category` |
| `#!date` / `#!loon_version` | 以注释的形式写在头部 |
| `[Rule]` `TYPE, value, POLICY, no-resolve` | `[Rule]` `TYPE,value,POLICY,no-resolve`（去掉空格和多余的引号，逻辑规则 AND/OR 保留嵌套结构，顺序不变，重复项也保留） |
| `[Rewrite]` `regex reject` / `reject-dict` | `[URL Rewrite]` `regex - reject` / `regex - reject-dict` |
| `[Script]` | `name = type=...,pattern=...,requires-body=1,script-path=...` |
| `[MitM] hostname=` | `[MITM] hostname = %APPEND% ...` |

## 同步上游

```bash
UA="Loon/867 CFNetwork/1498.700.2 Darwin/23.6.0"   # kelee.one 会拦截非代理工具的 UA
for f in BlockAdvertisers Block_HTTPDNS; do
  curl -sSL -A "$UA" -o upstream/$f.lpx https://kelee.one/Tool/Loon/Lpx/$f.lpx
  python3 scripts/lpx2sgmodule.py upstream/$f.lpx modules/$f.sgmodule https://kelee.one/Tool/Loon/Lpx/$f.lpx
done
git diff
```

脚本转换不了的条目会以 `# [UNCONVERTED]` 注释的形式留在模块里，同时输出到 stderr，不会被悄悄丢掉。

## 致谢

- 规则作者：[可莉🅥](https://github.com/luestr/ProxyResource/blob/main/README.md)、[VirgilClyne](https://github.com/VirgilClyne)（HTTPDNS）
- 插件中心：<https://hub.kelee.one/>
