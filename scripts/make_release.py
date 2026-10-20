r"""MCChecker 离线发布包构建（规范 §14.3；卡片 M4-06 / INH-1908）。

只用标准库（H2）。产物 `dist/MCChecker-<版本>.zip` 要在**没有外网**的目标机上解压即用
（§16.7），所以本脚本的职责不只是「打包」，而是把 §16.7 的两个静默失败点变成构建期硬失败：

1. 名单是**白名单**：成员只来自 `RELEASE_ENTRIES`，不是把工作目录压进 zip；
   名单条目在源树里缺失就直接报错（§14.3 的「包含」与「不包含」都在这儿落实）。
2. 产物要**验收后才落地**：先写临时文件，`verify_release()` 通过后改名到位，
   磁盘上不会留下「没过验收的包」。
3. 外链判定是**默认拒绝**（M4-06 复审 Important 1 之后倒过来的）：产物里**每一个**成员文件
   （Python、Markdown、shell、bat、前端全在内）出现的每一个带主机的地址字面量都是违规，
   除非主机是本机回环，除非这条 `(路径, 地址)` 被单独写进 `INERT_URL_LITERALS`。
   旧实现按「引用位置」放行，等于给绕过留了语法课：变量里的基地址、srcset 的第二个候选、
   `meta http-equiv=refresh`、`.py` 里的 `urlopen("https://…")`、Markdown 里的 `<img src>`
   都能躲开，而它们照样能让一台没外网的机器发出请求。名单本身也会腐坏，所以
   `find_stale_inert_entries()` 反向检查两件事：条目对应的文件在产物里却找不到那条地址 ⇒ 失败；
   条目对应的文件**已经不在产物里**（僵尸条目）⇒ 同样失败。
   地址的读法**在字面量扫得到的范围内**按 WHATWG 的预处理步骤（实体解码、去首尾 C0/空格、
   删 TAB/LF/CR、`@` 之后才是主机）读主机，而不是按字符串形状猜；它不是完整的 WHATWG
   解析器（IDNA、DNS、运行期拼接不在范围）。
   ① 特殊 scheme 里 `\` 等同 `/`、单斜杠也进权威段（`https:\evil.example.com\a.js` 的浏览器目标
   就是 `evil.example.com`）；② 不带 scheme 的写法要**开头连续两个**分隔符才进权威段，
   `\\evil.example.com\x.js`、`/\evil.example.com\x.js`、`/\\evil.example.com\x.js` 的主机都是
   `evil.example.com`（基址 `http://127.0.0.1:50002/…`，node 24 实测），而单个分隔符开头的
   `\evil.example.com\manual`、`/manual/ch2` 与 `C:\Users\me\app.py` 是相对路径/本机路径，
   不算主机；③ HTML 字符引用（数字与命名，分号可省）在交给 URL 解析器之前由 HTML 解析器
   全量替换，所以 `&#47;&#47;`、`&#92;&#92;`、`&sol;&sol;` 与 `&#10;//` 都是外链；④ 主机段里出现 WHATWG 禁止的字符
   （`|`、`^`、`[` 等）时浏览器根本解析不出地址（实测 Invalid URL），那种写法不算目标——
   这一条同时保住 `app/utils/helpers.py` 里 `unsafe_chars = '<>:"/\\|?*'` 这类正文不被误杀；
   ⑤ Markdown 的 `[文字](//host/x)`、`[文字](<//host/a b>)`、`[ref]: //host/x`、`[ref]: <//host/x>`
   都是引用位置。

**这个闸门的已知边界**（字面量扫描的定义边界，不是漏放行；详见 `docs/know-how.md` 第 2 节）：
① 运行期拼出来的地址（`fetch("https://" + host + "/ping")`）；② UTF-16 等非 UTF-8/latin-1 编码的
成员（解码只有这两条路）；③「条目为什么惰性」所依赖的运行期假设（`run.py` 的 `http://%s:%d` 的
主机来自运行期配置，构建期看不见）；④ `<` 后紧跟空格的 Markdown 目的地（`](<  //host a>)`；CommonMark 允许目的地里有空白，
但渲染后是 `%20//…` 相对路径，不是网络目标）；⑤ 把控制符插进 scheme 字母之间
（`h<LF>ttps://…`）——起点正则不跨 scheme 字符（scheme 与分隔符之间的 TAB/LF/CR 已覆盖）。
①②③④⑤ 它抓不到，别把绿读成「产物绝对出不去网」。

确定性：同一棵源树 → 同一成员集合、同一逐文件 sha256；zip 条目时间戳固定为 1980-01-01，
唯一每次不同的是 `MANIFEST.json` 里的 `built_at_utc`（它记录的就是「这次构建」）。

发布说明不在这里编：条目编号取自 `docs/MCChecker_DELIVERY_PLAN.md` §10，标题取自
`docs/MCChecker_FEATURE_CHANGES.md` 的 `### Cxx` 小节；编号在文档里找不到就失败。

3.9 兼容：`from __future__ import annotations` 置首，运行期不用 `X | Y`、`match`。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePath, PurePosixPath
from typing import Dict, List, Optional, Sequence, Tuple

MANIFEST_NAME = "MANIFEST.json"

#: §14.3 的「包含」名单。`app` 是目录条目（整棵展开，仍受下面两组排除规则约束）。
RELEASE_ENTRIES: Tuple[str, ...] = (
    "run.py",
    "main.py",
    "README.md",
    "start.sh",
    "start.bat",
    "docs/manual.md",
    "app",
)

#: 名单之外还要硬排除的**路径成分**（任一层命中即丢弃）。`data/` 与 `.nicegui/` 是 §14.3
#: 点名的两个；其余是这台机器上确实存在、一旦「顺手打包」就会漏出去的目录。
FORBIDDEN_COMPONENTS: Tuple[str, ...] = (
    "data",
    ".nicegui",
    ".git",
    "tests",
    "dist",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mimosa",
    ".workbuddy",
    ".superpowers",
    ".claude",
    ".qoder",
    ".vscode",
)

#: 以这些前缀开头的成分同样排除（`.venv`、`.venv39`……§14.3「不包含」+ 本机虚拟环境）。
FORBIDDEN_COMPONENT_PREFIXES: Tuple[str, ...] = (".venv",)

#: 排除的后缀（`*.pyc` 见 §14.3）。
FORBIDDEN_SUFFIXES: Tuple[str, ...] = (".pyc", ".pyo", ".tmp", ".swp", ".swo", ".DS_Store")

#: §14.3 点名单独排除的前端条目（模板与浏览器内 mock 不属于生产包）。
EXCLUDED_PATHS: Tuple[str, ...] = (
    "app/web/template.html",
    "app/web/assets/js/mock",
    "app/web/tests",
)

#: §14.3「可选：把 python-3.9.11-embed-amd64.zip 解压到 runtime/python/」——
#: 由发布人员在有网环境放入，本脚本既不生成也不校验它。
OPTIONAL_RUNTIME_DIR = "runtime/python"

#: 本机地址不算外网（§16.7 说的是「非本服务器的请求」）。
#: `_host_of` 返回不带方括号的 IPv6 字面量，所以这里只需要 `::1`。
LOCAL_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "0.0.0.0", "::1", "0:0:0:0:0:0:0:1"}
)

#: 逐条核对过、确认**不是网络目标**的绝对地址字面量（§16.7 唯一的例外出口）。
#: 键是 `(成员相对路径, 地址字面量)`：换文件、换地址都要另开一条，目录级的放行不存在。
#: 加一条之前必须读懂那一处为什么发不出请求；`find_stale_inert_entries()` 会反过来查两件事——
#: 名单里的路径一旦进了产物却找不到这条字面量 ⇒ 失败；路径已经离开产物（僵尸条目）⇒ 也失败。
#: 所以这份名单不会一边失效一边继续放行。名单记的是**源文件里的原样字节**：
#: `_read_url` 取回源文本那一段，归一化（反斜杠当分隔符、数字字符引用还原成 `/`）只在
#: `_host_of()` 里做，所以「登记的字面量」与「找不找得到」两件事始终对得上。
INERT_URL_LITERALS: Tuple[Tuple[str, str, str], ...] = (
    # XML 命名空间标识符：SVG 规范要求的固定 URI，解析器拿它做名字，不会去取。
    ("app/static/favicon.svg", "http://www.w3.org/2000/svg", "SVG XML 命名空间"),
    ("app/web/assets/js/ui/icons.js", "http://www.w3.org/2000/svg", "内联 SVG 的 XML 命名空间"),
    ("app/web/index.html", "http://www.w3.org/2000/svg", "data: URI 里的 SVG XML 命名空间"),
    # 输入框提示文字与示例文案：值是给用户看的，不参与任何请求。
    ("app/web/assets/js/views/importer.js", "http://10.20.1.15/cfg/config.xml", "placeholder 提示文字"),
    ("app/web/assets/js/views/settings.js", "http://…", "示例文案里的主机占位（省略号不是主机名）"),
    ("app/core/reviewing.py", "http://…}tag", "注释里的 XML 命名空间举例（`…` 与 `}` 都当不了主机名）"),
    # 文档字符串/格式串：地址部分是占位词或运行期参数，构建期没有主机可解析。
    ("app/core/downloader.py", "scheme://host/path", "docstring 里的地址格式示意"),
    ("app/core/downloader.py", "scheme://...", "docstring 里的地址格式示意"),
    # 并主线后转红的就是这一条（控制方实测，2026-10-19）：`upload.py` 那句注释在讲「下载器已经把
    # URL 脱敏成 `scheme://host/path`」，而 `_SCHEME_PREFIX_RE` 按语法认 `scheme` 是个合法 scheme，
    # 于是从占位串里提取出主机 `host` 并拦下来。放行依据与上面 downloader 那两条**同一条**：
    # 这是给人看的格式示意，不是交给任何解析器的字节。判据没有放宽——同族里的真主机
    # （`https://evil.example.com/x`）照旧红。
    ("app/services/upload.py", "scheme://host/path", "注释里的脱敏格式示意，与 downloader 那两条同源"),
    ("run.py", "http://%s:%d", "启动日志打印访问地址的格式串（主机端口运行期才知道）"),
    # 「引号后紧跟两个分隔符」这一族里**被 WHATWG 读成主机、但从来没人交给 URL 解析器**的两处：
    # 反斜杠开头两个分隔符现在也进权威段（本轮 Important 1），于是正则字符类与 BOM 注释里的
    # `\` + 十六进制转义被切成主机 `x00-` / `x00`（node 24 实测 `new URL("\\x00-/x", 基址)` 这种
    # 形状确实解得出主机）。逐行核对过：`search.py` 那条是 JSON 非法控制字符的字符类，
    # `reviewing.py` 那条是描述 XML BOM 嗅探的注释，两者都不进任何 URL 解析器，所以放行；
    # 反过来 `\\evil.example.com\x.js` 那种真主机不在此列，仍然红。
    ("app/services/search.py", "\\\\\\x00-\\x1f", "regex 字符类：JSON 非法控制字符集，不进 URL 解析器"),
    ("app/core/reviewing.py", "\\\\x00", "注释里的 BOM 嗅探字面量，不进 URL 解析器"),
)

#: DELIVERY_PLAN §10：发布说明必须列出的行为变更（相对 v1.0.1）。
RELEASE_NOTE_IDS: Tuple[str, ...] = ("C09", "C12", "C13", "C20", "C25", "C26", "C27")
FEATURE_CHANGES_DOC = "docs/MCChecker_FEATURE_CHANGES.md"

#: zip 条目固定时间戳：让「同一棵源树」产出可比对的成员与哈希。
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

_VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.M)
_FEATURE_HEADING_RE = re.compile(r"""^###\s+(C\d+)\s+(.+?)\s*$""", re.M)

#: 带 scheme 的绝对地址前缀（不限 http/https：`ftp://`、`gopher://`、自定义 scheme 都能被
#: 浏览器或 urllib 拿去发请求，所以一律当网络目标看待）。
_SCHEME_PREFIX_RE = re.compile(r"""[a-zA-Z][a-zA-Z0-9+.\-]*://""")
#: scheme 后面的 TAB/LF/CR 会被 WHATWG 删掉后再解析：`http:<LF>//host` 仍是外链。
#: 只覆盖 scheme 与分隔符之间的空白；把控制符插进 scheme 字母之间（`h<LF>ttps://`）仍是已知边界。
_SCHEME_LOOSE_RE = re.compile(r"""[a-zA-Z][a-zA-Z0-9+.\-]*:[ \t\r\n]*[/\\]""")
#: WHATWG 的「特殊 scheme」里**会联网的那五个**（http/https/ftp/ws/wss）：`scheme:` 后面哪怕只跟一个 `/` 或 `\`，
#: 浏览器照样解析出主机并发请求。node 24 实测（本卡据此改的判据，不是猜的字符串形状）：
#: `new URL("https:\\evil.example.com\\a.js").host === "evil.example.com"`、
#: `new URL("https:/evil.example/x").host === "evil.example"`、
#: `new URL("https:///evil.example/x").host === "evil.example"`。
#: 旧扫描只认 `scheme://`，所以 Important 1 那条反斜杠写法整绿着出了门。
#: `file:` **不在**这个集合里：`file:\d:\Project\x`、`C:\Users\mc\cfg.xml` 这类 Windows 路径
#: 跟着归一化就会把 `d:` 当成主机，那是误杀本机路径而不是拦住外网。
_NETWORK_SPECIAL_SCHEMES = frozenset({"http", "https", "ftp", "ws", "wss"})
#: 数字字符引用里**解码后是 `/`** 的那两种写法：十进制 `&#47;`、十六进制 `&#x2f;`（大小写都行、
#: 可以有多余前导零），HTML 的 legacy 规则允许**省略结尾分号**，所以 `;` 是可选项。
#: 两个否定预查是必需的而不是保守：`&#470;` 解出 `ǖ`、`&#X2FEvil` 解出 `˾`（node 24 实测），
#: 把它们当成 `/` 等于闸门自己拼出一个源文件里不存在的地址。
#: 命名引用（`&sol;`）与百分号编码（`%2F`）**不在**射程内，见模块文档字符串的边界说明。
_NUMERIC_SLASH_REF_SRC = r"""&(?:#0*47(?![0-9])|#[xX]0*2[fF](?![0-9a-fA-F]));?"""
_NUMERIC_SLASH_REF_RE = re.compile(_NUMERIC_SLASH_REF_SRC)
#: `&#92;` / `&#x5c;`：解出来是反斜杠；对特殊 scheme 它与 `/` 一样是权威分隔符。
_NUMERIC_BACKSLASH_REF_SRC = r"""&(?:#0*92(?![0-9])|#[xX]0*5[cC](?![0-9a-fA-F]));?"""
#: 命名实体里的 `&sol;`（`/`）与 `&bsol;`（`\`）；HTML 解析阶段就会替换成真分隔符。
_NAMED_SEP_REF_SRC = r"""&(?:sol|bsol);"""
#: 「一个权威分隔符」= `/`、`\`（对特殊 scheme 的基址二者等价），或一个解码成
#: `/` / `\` 的字符引用（数字或命名，分号可省）。
_AUTH_SEP_SRC = (
    r"""(?:[/\\]|"""
    + _NUMERIC_SLASH_REF_SRC
    + r"""|"""
    + _NUMERIC_BACKSLASH_REF_SRC
    + r"""|"""
    + _NAMED_SEP_REF_SRC
    + r""")"""
)
#: 权威段开头 = **连续两个**分隔符（`//`、`/\`、`\\`、`/\\`、`&#47;&#47;` 以及混着写）。
#: 必须两个：单个 `/` 或 `\` 在基址下是相对路径，node 24 实测
#: `new URL("\\evil.example.com\\manual", "http://127.0.0.1:50002/index.html")` 的主机
#: 还是 `127.0.0.1`（即没有外部主机），只按「看见反斜杠」拦就会误杀 `\evil.example.com\manual`
#: 与 `C:\Users\me\app.py` 这类正文。
_RELATIVE_AUTHORITY_SRC = _AUTH_SEP_SRC + _AUTH_SEP_SRC
#: 原始字节里的连续两个 `/`/`\`：`_read_url` 用它决定「已进权威段」，`_host_of` 先解码数字引用
#: 再用它走同一条路（`&#47;&#47;` 解码后就是 `//`，不必在这里重复匹配引用）。
_DOUBLE_SEP_RE = re.compile(r"""[/\\]{2}""")
#: 主机段里不许出现的字符——这是 WHATWG forbidden host code point 的一个**保守近似**
#: （不包含 IDNA 与 IPv6 的完整规范化；本闸门只做字面量扫描）。出现任何一个，浏览器
#: 直接解析失败（node 24 实测 `new URL("/\\|?*", 基址)` 报 Invalid URL），所以那不是网络目标。
#: 分隔符 `/ ? # \`、userinfo 的 `@` 与端口 `:` 在上面已经切掉，这里查剩下的那几个。
#: 少了这一条，新加的「引号后 `/\`」规则会误杀 `app/utils/helpers.py:36` 的
#: `unsafe_chars = '<>:"/\\|?*'`（切成 token `/\\|?*`，主机段是 `|`）——那是非法文件名字符集，
#: 不是地址。放行的是解析失败的目标，不是放宽判据。
_FORBIDDEN_HOST_CHARS = frozenset(' \t\r\n"#<>?@[]^|')
#: `scheme:` 后面紧跟一个权威分隔符（含 `&#47;` 写法）：见上面那段 node 实测。
_SPECIAL_SCHEME_AUTHORITY_RE = re.compile(r"""(?i)\b(?:https?|wss?|ftp):(?=%s)""" % _AUTH_SEP_SRC)
#: 引号后面直接跟权威段开头：写进字符串常量的协议相对地址（运行期拼一下就是 `http://host/…`）。
#: 限定「紧跟引号」是为了不把 JS/Python 的行注释 `// 说明` 和整除 `a // b` 当成地址。
_QUOTED_RELATIVE_RE = re.compile(r"""(?<=["'`])""" + _RELATIVE_AUTHORITY_SRC)
#: 引号后先有空白再出现权威段：`href=" //evil…"`、`fetch("\t//evil…")`。
#: 空白由 WHATWG 在解析前剥掉，所以它不能把这条写法变成「不是地址」。
_QUOTED_RELATIVE_WS_RE = re.compile(
    r"""(["'`])\s+(""" + _RELATIVE_AUTHORITY_SRC + r""")"""
)
#: 解成空白（换行/制表/回车/空格）的字符引用后面紧跟权威段：`&#10;//evil…`。
#: 起始位置取后面的权威段（group 1），因为空白本身不改变主机。
_ENTITY_WS_AUTH_RE = re.compile(
    r"""&#(?:0*9|0*10|0*13|0*32);?\s*(""" + _RELATIVE_AUTHORITY_SRC + r""")"""
)
#: srcset 的值是「地址 描述符」的逗号列表，必须逐候选切；整条属性一起匹配只会看见第一个候选。
_SRCSET_ATTR_RE = re.compile(r"""\bsrcset\s*=\s*["']([^"']*)["']""", re.I)
#: `srcset` 不带引号：值到空白为止；候选仍按逗号切（第四轮复审建议）。
_SRCSET_UNQUOTED_RE = re.compile(r"""\bsrcset\s*=\s*([^\s"'<>][^\s<>]*)""", re.I)
#: Markdown 行内链接/图片的目标：`[说明](//host/x)`、`![截图](//host/a.png)`、
#: `](<//host/a b>)`（CommonMark 允许左括号后有空白、可用 `<…>` 包住带空格的目的地）、
#: `](&#47;&#47;host/x)`（实体在 HTML/Markdown 解析阶段就变成 `/`，然后才交给 URL 解析器）。
#: Important 2 出事的写法：不带 scheme 又不在引号里，旧的两条捕网（scheme 前缀、引号后 `//`）
#: 都看不见它，位置网又不认 `](`。这里按**真引用语法**精确补，不是「Markdown 里不许出现 `//`」。
_MD_LINK_TARGET_RE = re.compile(r"""\]\(\s*<?(""" + _RELATIVE_AUTHORITY_SRC + r""")""")
#: Markdown 引用式链接的定义行：`[ref]: //host/x "标题"`、`[ref]: <//host/x>`（行首最多三个空格
#: 是 CommonMark 的缩进上限，`<…>` 包住目的地是合法语法，渲染器会把尖括号剥掉再交给 URL 解析）。
_MD_REF_DEF_RE = re.compile(
    r"""^[ \t]{0,3}\[[^\]\n]+\][ \t]*:[ \t]*<?(""" + _RELATIVE_AUTHORITY_SRC + r""")""", re.M
)
#: srcset 的值是「地址 描述符」的逗号列表，必须逐候选切；整条属性一起匹配只会看见第一个候选。
_SRCSET_UNQUOTED_RE = re.compile(r"""\bsrcset\s*=\s*([^\s"'<>][^\s<>]*)""", re.I)
#: `<meta http-equiv="refresh" content="0;url=…">`：跳转地址藏在 content 值里面，不是属性值开头。
_META_REFRESH_RE = re.compile(
    r"""\bcontent\s*=\s*["'][^"']*\burl\s*=\s*((?:https?:)?%s[^"'\s>]+)""" % _RELATIVE_AUTHORITY_SRC,
    re.I,
)
#: 「引用位置」模式：比 scheme 前缀、特殊 scheme 后跟单个分隔符、引号后权威段这三条多抓到的只有
#: **既不带 scheme、也不在引号里**的写法（如 CSS `url(//host/a.png)`；Markdown 的 `](//host)`
#: 见上面那两条专用模式）。它们是追加的捕网，不再是放行与否的判据——
#: M4-06 复审 Important 1 已经证明「按位置放行」可以靠换一个写法整体绕过。
#: 每条里的 `(?:https?:)?` + 权威段开头同时认 `//`、`/\`、`\\` 与 `&#47;&#47;` 这几种写法。
_REFERENCE_PATTERNS: Tuple[str, ...] = (
    # HTML 属性：src/href/data/... 的值直接是绝对地址或协议相对地址（srcset 见 `_SRCSET_ATTR_RE`）
    r"""\b(?:src|href|data|xlink:href|action|formaction|poster|codebase|manifest)\s*=\s*["']?((?:https?:)?%s[^"'\s<>]+)"""
    % _RELATIVE_AUTHORITY_SRC,
    # CSS：url(...) 与 @import
    r"""(?:url\s*\(\s*["']?|@import\s+["'])((?:https?:)?%s[^"'\s)]+)""" % _RELATIVE_AUTHORITY_SRC,
    # JS：把绝对地址当目标参数的调用与赋值
    r"""(?:\bfetch\s*\(|\bimportScripts\s*\(|\bnew\s+(?:Worker|EventSource|WebSocket)\s*\(|\.src\s*=|\.href\s*=)\s*["'`]?((?:https?:)?%s[^"'`\s)]+)"""
    % _RELATIVE_AUTHORITY_SRC,
    # JS：XHR 的第二个参数
    r"""\.open\s*\(\s*["'][^"'`]*["']\s*,\s*["']?((?:https?:)?%s[^"'`\s)]+)"""
    % _RELATIVE_AUTHORITY_SRC,
)
_COMPILED_REFERENCE_PATTERNS = [re.compile(p, re.I) for p in _REFERENCE_PATTERNS]

#: 地址 token 的终止字符（和浏览器一致：引号、尖括号、空白、常见标点都不算地址的一部分）。
#: 换行/回车/制表符**不终止** token：WHATWG URL 解析会把它们从地址里删掉再解析，
#: `src="http://\n evil.example/x"` 照样发得出去，所以这里跟着删（复审裁定「安全判据
#: 至少要和安全判据一样安全」——能删的必须是浏览器也删的）。
_URL_STOP_CHARS = frozenset(" '\"()<>,;`]")
#: 还在权威段里时，`;`、`(`、`)`、反引号、`]` 都可能属于 userinfo 或 IPv6；但只有当
#: 它们后面还有 `@` 时才放行，否则会把正文里的反引号/反斜杠散文吞成主机。
#: 第四轮复审判例：`//;@evil…`、`//(@evil…`、`` //`@evil… ``、`//[::1]@evil…`。
#: 残余代价：代码 span 里的 `` `//`,@tag `` 仍可能被读成主机 `tag`；当前真树没有这种写法。
_AUTHORITY_SOFT_STOPS = frozenset(" ;()`],")
#: 权威段里的硬终止符：空白、引号、尖括号；`/`、`?`、`#` 也结束权威段。
_URL_STOP_CHARS_AUTHORITY = frozenset(" '\"<>")
#: WHATWG 解析前会剥掉首尾 C0/空格；`_host_of` 用它做预处理。
_C0_AND_SPACE = "".join(chr(i) for i in range(0x21))
#: 一个完整的 HTML 字符引用（数字或命名；分号可省）。`_read_url` 用它把整段引用当成
#: 一个 token 的一部分，`_host_of` 再用标准库 `html.unescape` 全量解码。
_HTML_ENTITY_RE = re.compile(r"&(?:#[0-9]+;?|#[xX][0-9A-Fa-f]+;?|[A-Za-z][A-Za-z0-9]+;?)")


class ReleaseError(RuntimeError):
    """构建或验收失败；`str(exc)` 是给发布人员看的原因。"""


# --------------------------------------------------------------------------- 名单

def read_version(source_root: Path) -> str:
    init = source_root / "app" / "__init__.py"
    if not init.is_file():
        raise ReleaseError("找不到 %s，无法确定版本号" % init.relative_to(source_root))
    match = _VERSION_RE.search(init.read_text(encoding="utf-8"))
    if not match:
        raise ReleaseError("%s 里没有 __version__，无法确定版本号" % init)
    return match.group(1)


def is_excluded(rel: PurePosixPath) -> bool:
    """成员相对路径是否被排除（任一层成分命中，或整条路径在 §14.3 点名的排除项里）。"""
    parts = rel.parts
    for part in parts[:-1]:
        if part in FORBIDDEN_COMPONENTS or part.startswith(FORBIDDEN_COMPONENT_PREFIXES):
            return True
    last = parts[-1]
    if last in FORBIDDEN_COMPONENTS:
        return True
    if any(last.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        return True
    posix = rel.as_posix()
    for excluded in EXCLUDED_PATHS:
        if posix == excluded or posix.startswith(excluded + "/"):
            return True
    return False


def collect_members(source_root: Path) -> List[str]:
    """按白名单展开成员相对路径（排序、 posix 分隔符）。条目缺失即失败。"""
    source_root = source_root.resolve()
    found: List[str] = []
    for entry in RELEASE_ENTRIES:
        target = source_root / entry
        if target.is_file():
            found.append(PurePosixPath(entry).as_posix())
        elif target.is_dir():
            found.extend(_walk(source_root, PurePosixPath(entry)))
        else:
            raise ReleaseError(
                "发布名单里的条目在源目录不存在：%s（源目录 %s）" % (entry, source_root)
            )
    return sorted(found)


def _rel_of(path: Path, base: Path) -> PurePosixPath:
    """相对源目录的路径，统一成 posix 分隔符（zip 成员名与清单都用它）。"""
    return PurePosixPath(PurePath(path).relative_to(base).as_posix())


def _walk(source_root: Path, entry: PurePosixPath) -> List[str]:
    found: List[str] = []
    base = source_root / entry.as_posix()
    for dirpath, dirnames, filenames in os.walk(str(base), followlinks=False):
        current = entry / _rel_of(Path(dirpath), base)
        # 就地剪枝：被排除的目录连读都不读，避免 `__pycache__`/`data` 里的东西被带进来。
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not is_excluded(current / name)
        )
        for name in sorted(filenames):
            rel = current / name
            if is_excluded(rel):
                continue
            path = Path(dirpath) / name
            if path.is_symlink():
                raise ReleaseError(
                    "发布名单内不允许符号链接（会把本机路径带进包里）：%s" % rel.as_posix()
                )
            found.append(rel.as_posix())
    return found


def collect_release_notes(source_root: Path) -> List[str]:
    doc = source_root / FEATURE_CHANGES_DOC
    if not doc.is_file():
        raise ReleaseError("发布说明缺来源：%s 不存在" % FEATURE_CHANGES_DOC)
    headings: Dict[str, str] = {}
    for cid, title in _FEATURE_HEADING_RE.findall(doc.read_text(encoding="utf-8")):
        headings.setdefault(cid, title)
    notes: List[str] = []
    missing: List[str] = []
    for cid in RELEASE_NOTE_IDS:
        title = headings.get(cid)
        if title is None:
            missing.append(cid)
        else:
            notes.append("%s %s" % (cid, title))
    if missing:
        raise ReleaseError(
            "%s 里找不到这些发布说明条目的标题：%s（编号取自 DELIVERY_PLAN §10，不允许在这里编）"
            % (FEATURE_CHANGES_DOC, "、".join(missing))
        )
    return notes


# ------------------------------------------------------------------- 外链与泄漏检查

_INERT_KEYS = frozenset((rel, url) for rel, url, _why in INERT_URL_LITERALS)


def _as_text(data: bytes) -> str:
    """成员内容当文本看：UTF-8 解不动就按字节逐位看（二进制里藏 URL 同样要抓）。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _host_of(url: str) -> str:
    r"""地址的权威段（主机）；`data:`、`file:///…`、`http://`、Windows 路径这种没有主机的返回 ""。

    特殊 scheme（`_NETWORK_SPECIAL_SCHEMES`）按 WHATWG 的读法先归一化再取主机：`\` 等同 `/`，
    且一个斜杠就进权威段，多余的斜杠跳过——`https:\evil.example.com\a.js` 的浏览器目标就是
    `evil.example.com`（node 24 实测），所以这里必须跟着归一化，而不是按 `://` 的形状猜。
    不带 scheme 的写法走同一条 WHATWG 规则（本轮补上的那半族）：**开头两个**分隔符才进权威段，
    权威段里的 `\` 同样按 `/` 处理——`\\evil.example.com\x.js`、`/\evil.example.com\x.js`、
    `/\\evil.example.com\x.js` 的主机都是 `evil.example.com`，而 `//localhost\x` 的主机是
    `localhost`（改之前这里报的是 `localhost\x`，是个假主机名）。单个分隔符开头
    （`\evil.example.com\manual`、`/manual/ch2`）是相对路径，没有主机。
    解析前先做 WHATWG 的预处理：`html.unescape` **全量**解码 HTML 字符引用（数字与命名，
    分号可省；`&#92;`、`&sol;`、`&#10;` 都算），再剥首尾 C0/空格并删 TAB/LF/CR。这不是完整的
    WHATWG 解析器（IDNA、DNS、运行期拼接不在范围内），但足以让「实体编码的分隔符」与
    「空白藏法」不再从字面量扫描里漏出去。
    权威段里先按**最后一个 `@`** 切掉 userinfo，再判方括号 IPv6；顺序反过来会把
    `[::1]@evil.example.com` 读成 `[::1]`（本机）而静默放行。方括号里是 IPv6 字面量，
    整段取回：按 `:` 切会把 `[2001:db8::1]` 切成空串。
    主机段里含 WHATWG 禁止的字符（`_FORBIDDEN_HOST_CHARS`）时返回 ""——浏览器根本解析不出
    地址（实测 Invalid URL），那种写法发不出请求，不算外链。
    """
    url = html.unescape(url)
    url = url.strip(_C0_AND_SPACE)
    url = url.replace("\t", "").replace("\n", "").replace("\r", "")
    if _DOUBLE_SEP_RE.match(url):
        # 无 scheme：开头连续跳过分隔符后进权威段（special authority ignore slashes 状态），
        # 剩下的 `\` 与 `/` 一样是分隔符。
        body = url.lstrip("/\\").replace("\\", "/")
    else:
        scheme, colon, rest = url.partition(":")
        if not colon:
            return ""
        if rest.startswith("//"):
            body = rest[2:]
            if scheme.lower() in _NETWORK_SPECIAL_SCHEMES:
                # WHATWG 的 special authority ignore slashes：权威段前多余的 `/` 与 `\` 一律跳过
                # （node 24 实测 `https:///evil.example/x` 的 host 也是 evil.example）。
                # `file:` 不在其中，所以 `file:///d:/Project/x` 仍按本机路径处理，不算主机。
                body = body.lstrip("/\\")
        elif scheme.lower() in _NETWORK_SPECIAL_SCHEMES:
            body = rest.replace("\\", "/").lstrip("/")
        else:
            return ""
    for sep in ("?", "#", "/"):
        body = body.split(sep, 1)[0]
    # 权威段 = `@` 之前是 userinfo；先分它，再判 IPv6 方括号。顺序反过来会把
    # `[::1]@evil.example.com` 读成 `[::1]`（本机）而静默放行。
    body = body.rsplit("@", 1)[-1]
    if body.startswith("["):
        literal, _closes, _rest = body.partition("]")
        return literal[1:].lower()
    host = body.split(":", 1)[0].lower()
    if any(ch in _FORBIDDEN_HOST_CHARS for ch in host):
        return ""
    return host


def _has_at_before_authority_end(text: str, index: int) -> bool:
    """从 `index` 往后看：在权威段结束前是否还有 `@`（userinfo 的判据）。

    空白（含 WHATWG 会删的 TAB/LF/CR）可以出现在 userinfo 里，扫描时跳过；
    `/`、`?`、`#` 与引号/尖括号仍是权威段的硬边界。
    """
    for ch in text[index + 1:]:
        if ch == "@":
            return True
        if ch in "\t\r\n ":
            continue
        if ch in _URL_STOP_CHARS_AUTHORITY or ch in "/?#":
            return False
    return False


def _read_url(text: str, start: int) -> str:
    """从 `start` 起读一个地址 token（**保留源文件里的原样字节**，归一化只在 `_host_of` 里做，
    这样违规消息与惰性名单记的始终是文件里那一串）。

    浏览器在解析前会删掉整条地址里的 tab/换行/回车（不只是权威段），所以这里一律跳过；
    例如 `http:/<LF>/evil.example.com/x` 的换行会被删掉；`<`、引号、空白仍终止 token。
    特殊 scheme 的 `\\` 与 `/` 一样结束权威段（WHATWG authority state），所以它也要置
    `past_authority`，否则 `https:\\evil.example.com\\n/x` 会把整行吞进来；**开头两个分隔符**的
    无 scheme 写法同理（模块文档字符串 ② 的那一整族反斜杠权威段——基址是特殊的 http，
    `\\` 在那里与 `/` 等价，一样结束权威段）。
    HTML 字符引用（`&#47;`、`&#92;`、`&sol;`，分号可省）整段留在 token 里；`&`、`#`、数字本身
    都不是终止符，引用内部的 `;` 也不能把 token 切断。解码出来的 `/` 或反斜杠置
    `past_authority`；空白引用交给 `_host_of` 统一删掉。
    """
    special = False
    if _DOUBLE_SEP_RE.match(text, start):
        body = start + 2
        special = True  # 基址是特殊 scheme 才会走到权威段，`\` 与 `/` 同等对待
    else:
        scheme = _SCHEME_PREFIX_RE.match(text, start)
        if scheme is not None:
            body = scheme.end()
            special = text[start:body].partition(":")[0].lower() in _NETWORK_SPECIAL_SCHEMES
        else:
            authority = _SPECIAL_SCHEME_AUTHORITY_RE.match(text, start)
            if authority is None:
                body = start  # srcset 候选等：可能只是个相对路径，交给 `_host_of` 判掉
            else:
                body = authority.end()
                special = True
    chars: List[str] = list(text[start:body])
    index = body
    past_authority = False
    while index < len(text):
        ch = text[index]
        if ch == "&":
            reference = _HTML_ENTITY_RE.match(text, index)
            if reference is not None:
                decoded = html.unescape(reference.group(0))
                if decoded != reference.group(0):
                    chars.append(reference.group(0))
                    index = reference.end()
                    if decoded in ("/", "\\"):
                        past_authority = True
                    continue
        if ch in "\t\r\n":
            index += 1
            continue
        if ch in _URL_STOP_CHARS:
            if (
                not past_authority
                and ch in _AUTHORITY_SOFT_STOPS
                and _has_at_before_authority_end(text, index)
            ):
                chars.append(ch)
                index += 1
                continue
            break
        if ch == "/" or (special and ch == "\\"):
            past_authority = True
        chars.append(ch)
        index += 1
    return "".join(chars)


def _srcset_candidate_starts(value: str, base: int) -> List[int]:
    """`srcset="a.png 1x, //host/b.png 2x"` 的每个候选起点（偏移是源文本里的绝对位置）。"""
    starts: List[int] = []
    offset = 0
    for chunk in value.split(","):
        stripped = chunk.lstrip()
        tokens = stripped.split()
        if tokens:
            starts.append(base + offset + (len(chunk) - len(stripped)))
        offset += len(chunk) + 1
    return starts


def url_literals(text: str) -> List[Tuple[int, str]]:
    """文本里所有「可能成为网络目标」的地址字面量：[(行号, 字面量)]，去重后按位置排序。

    几条来源取并集，**判定不看它出现在哪个语法位置**（Important 1 的教训：位置判据可以
    整体绕过——变量里的地址、srcset 的第二个候选、meta 刷新、Python/Markdown 里的地址
    都不在旧的位置模式里）：scheme 前缀、特殊 scheme 后跟一个权威分隔符（`/`、`\\`、`&#47;`）、
    引号后的权威段开头（`//`、`/\\`、`\\`、`&#47;&#47;`……）、Markdown 链接/图片目标与引用式定义、
    引用位置模式、srcset 候选。
    没有主机的写法（`http://`、`file:///d:/…`、`data:`、Windows 路径、单个分隔符开头的相对路径、
    浏览器根本解析不出地址的 `_FORBIDDEN_HOST_CHARS` 写法）不在这里，它们指不了任何机器。
    """
    starts: List[int] = []
    for regex in (
        _SCHEME_PREFIX_RE,
        _SCHEME_LOOSE_RE,
        _QUOTED_RELATIVE_RE,
        _SPECIAL_SCHEME_AUTHORITY_RE,
    ):
        for match in regex.finditer(text):
            starts.append(match.start())
    # 任意字符引用都可能是「实体编码的 scheme/空白/分隔符」的一部分；从这里起读，
    # 由 `_read_url` 消费整段、`_host_of` 统一解码，避免只枚举少数几种实体。
    for match in _HTML_ENTITY_RE.finditer(text):
        starts.append(match.start())
    for match in _QUOTED_RELATIVE_WS_RE.finditer(text):
        starts.append(match.start(2))
    for regex in (
        _META_REFRESH_RE,
        _MD_LINK_TARGET_RE,
        _MD_REF_DEF_RE,
        _ENTITY_WS_AUTH_RE,
    ) + tuple(_COMPILED_REFERENCE_PATTERNS):
        for match in regex.finditer(text):
            starts.append(match.start(1))
    for match in _SRCSET_ATTR_RE.finditer(text):
        starts.extend(_srcset_candidate_starts(match.group(1), match.start(1)))
    for match in _SRCSET_UNQUOTED_RE.finditer(text):
        starts.extend(_srcset_candidate_starts(match.group(1), match.start(1)))

    found: Dict[Tuple[int, str], None] = {}
    for start in starts:
        url = _read_url(text, start)
        if not url or not _host_of(url):
            continue
        found[(text.count("\n", 0, start) + 1, url)] = None
    return sorted(found)


def scan_member(rel_path: str, text: str) -> Tuple[List[str], List[Tuple[int, str]]]:
    """倒过来的默认：带主机的绝对地址就是违规，除非主机是本机或这条被单独放行。

    返回 (违规说明, 已放行的惰性项)；不再有「看着不像引用位置所以算文本」这条路。
    """
    violations: List[str] = []
    inert: List[Tuple[int, str]] = []
    for line, url in url_literals(text):
        host = _host_of(url)
        if not host or host in LOCAL_HOSTS:
            continue
        if (rel_path, url) in _INERT_KEYS:
            inert.append((line, url))
            continue
        violations.append(
            "%s:%d 引用了非本机主机 %s（%s；不在惰性名单 INERT_URL_LITERALS 上）"
            % (rel_path, line, host, url)
        )
    return violations, inert


def scan_artifact(members: Dict[str, bytes]) -> Tuple[List[str], Dict[str, List[Tuple[int, str]]]]:
    """逐成员扫**全部**产物文件（§14.3 名单里的 .py/.md/.sh/.bat 和前端文件一样在射程内）。"""
    violations: List[str] = []
    inert: Dict[str, List[Tuple[int, str]]] = {}
    for rel, data in sorted(members.items()):
        hits, skipped = scan_member(rel, _as_text(data))
        violations.extend(hits)
        if skipped:
            inert[rel] = skipped
    return violations, inert


def find_stale_inert_entries(members: Dict[str, bytes]) -> List[str]:
    """放行名单与产物脱节的两种形态，都算失败。

    ① 条目对应的文件在产物里却找不到那条地址——名单一旦和产物脱节，它就在替未知地址放行；
    ② 条目对应的文件**已经不在产物里**（僵尸条目）。第二轮复审实测过旧行为的洞，本轮在同一
    棵树上复核：把 `app/static/favicon.svg` 从源树删掉，改之前的代码构建 **exit 0 且产物落地**，
    那条放行还在原地「放行」一个已经不存在的文件——「陈旧条目一定失败」的说法只有方向 ① 成立。
    """
    stale: List[str] = []
    for rel, url, why in INERT_URL_LITERALS:
        data = members.get(rel)
        if data is None:
            stale.append(
                "%s 的放行条目 %s（%s）指向的文件已经不在产物里，这条放行要删掉"
                % (rel, url, why)
            )
            continue
        if url not in _as_text(data):
            stale.append("%s 的放行条目 %s（%s）在产物里已经找不到" % (rel, url, why))
    return stale


def find_leaked_paths(members: Dict[str, bytes], forbidden: Sequence[str]) -> List[str]:
    """任一成员（含清单）里出现本机绝对路径 → 失败。二进制也照样查（按字节）。"""
    needles = [(lit, lit.encode("utf-8")) for lit in forbidden if lit]
    leaks: List[str] = []
    for rel, data in sorted(members.items()):
        for literal, blob in needles:
            if blob in data:
                leaks.append("%s 里含本机绝对路径 %s" % (rel, literal))
    return leaks


def _forbidden_literals(source_root: Path, zip_parent: Path) -> List[str]:
    """构建机上不该出现在包里的绝对路径。

    `/var` 与 `/private/var` 这类软链在 macOS 上是同一个目录的两种写法，所以实参与
    `resolve()` 后的形态都要查；测试里传的就是未解析的 tmp 路径。
    """
    candidates = [source_root, source_root.resolve(), Path.home(), zip_parent, Path(zip_parent).resolve()]
    seen: Dict[str, None] = {}
    for path in candidates:
        for text in (str(path), path.as_posix()):
            if text and text not in ("/", "."):
                seen[text] = None
    return sorted(seen)


# ------------------------------------------------------------------------- 打包

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_member_bytes(source_root: Path, members: Sequence[str]) -> Dict[str, bytes]:
    blob: Dict[str, bytes] = {}
    for rel in members:
        path = source_root / rel
        if not path.is_file():
            raise ReleaseError("成员在展开后消失了：%s" % rel)
        blob[rel] = path.read_bytes()
    return blob


def _member_mode(rel: str, path: Path) -> int:
    """入口脚本要能被双击/直接执行，权限位写进 zip（Linux/macOS 解压后可 `./start.sh`）。"""
    if rel in ("start.sh", "start.bat"):
        return 0o755
    mode = path.stat().st_mode & 0o777
    return mode or 0o644


def write_zip(zip_path: Path, members: Dict[str, bytes], manifest: Dict[str, object], modes: Dict[str, int]) -> None:
    with zipfile.ZipFile(str(zip_path), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for rel in sorted(members):
            info = zipfile.ZipInfo(rel, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            # UNIX 头 + 权限位：start.sh 解压后才能直接 `./start.sh`（§14.2 双击入口）。
            info.create_system = 3
            info.external_attr = modes[rel] << 16
            archive.writestr(info, members[rel])
        info = zipfile.ZipInfo(MANIFEST_NAME, date_time=ZIP_DATE_TIME)
        info.create_system = 3
        info.external_attr = 0o644 << 16
        archive.writestr(info, (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def build_release(
    source_root: Path,
    out_path: Optional[Path] = None,
    force: bool = False,
) -> Dict[str, object]:
    """构建 + 验收，返回给 CLI 打印的结果。验收不过不落最终文件。"""
    source_root = source_root.resolve()
    version = read_version(source_root)
    target = out_path if out_path is not None else source_root / "dist" / (
        "MCChecker-%s.zip" % version
    )
    target = Path(target)
    if target.exists() and not force:
        raise ReleaseError(
            "产物已存在：%s（要覆盖就加 --force）" % target
        )
    target.parent.mkdir(parents=True, exist_ok=True)

    names = collect_members(source_root)
    blob = read_member_bytes(source_root, names)
    modes = {rel: _member_mode(rel, source_root / rel) for rel in names}
    manifest: Dict[str, object] = {
        "product": "MCChecker",
        "version": version,
        "built_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "scripts/make_release.py",
        "release_notes_source": FEATURE_CHANGES_DOC,
        "release_notes": collect_release_notes(source_root),
        "files": [
            {"path": rel, "sha256": _sha256(blob[rel]), "bytes": len(blob[rel])}
            for rel in sorted(names)
        ],
    }

    temp = target.parent / (target.name + ".part")
    if temp.exists():
        temp.unlink()
    try:
        write_zip(temp, blob, manifest, modes)
        report = verify_release(temp, source_root=source_root, version=version)
        if target.exists():
            target.unlink()
        os.replace(str(temp), str(target))
    finally:
        if temp.exists():
            temp.unlink()

    return {
        "zip": target,
        "version": version,
        "file_count": len(names),
        "zip_bytes": target.stat().st_size,
        "zip_sha256": _sha256(target.read_bytes()),
        "inert_references": report["inert_references"],
        "release_notes": manifest["release_notes"],
    }


# ------------------------------------------------------------------------- 验收

def verify_release(
    zip_path: Path,
    source_root: Path,
    version: Optional[str] = None,
) -> Dict[str, object]:
    """重开产物逐条核对；任何一条不过就 `ReleaseError`。"""
    zip_path = Path(zip_path)
    source_root = source_root.resolve()
    if not zipfile.is_zipfile(str(zip_path)):
        raise ReleaseError("产物不是合法的 zip：%s" % zip_path)

    expected = collect_members(source_root)

    with zipfile.ZipFile(str(zip_path)) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise ReleaseError("zip 解压校验失败：%s" % bad)
        names = [info.filename for info in archive.infolist()]
        blob = {name: archive.read(name) for name in names}

    # 1) 成员集合必须与名单**完全一致**（多的少的都算失败）。
    extra = sorted(set(names) - set(expected) - {MANIFEST_NAME})
    missing = sorted(set(expected) - set(names))
    if extra or missing:
        raise ReleaseError(
            "产物成员与发布名单不一致。多出：%s；缺少：%s"
            % ("、".join(extra) or "无", "、".join(missing) or "无")
        )

    # 2) 名字里不许有被排除的成分（data/、.venv、tests/、.git、缓存、本机草稿目录）。
    for name in names:
        if is_excluded(PurePosixPath(name)) or name.startswith("data/") or name == "data":
            raise ReleaseError("产物里出现了被排除的路径：%s" % name)

    # 3) 清单结构 + 逐文件 sha256 复核（目标机可以离线照这个自证完整性）。
    if MANIFEST_NAME not in blob:
        raise ReleaseError("产物缺少 %s" % MANIFEST_NAME)
    try:
        manifest = json.loads(blob[MANIFEST_NAME].decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReleaseError("%s 解析失败：%s" % (MANIFEST_NAME, exc))
    if version is not None and manifest.get("version") != version:
        raise ReleaseError(
            "%s 版本 %r 与 app/__init__.py 的 %r 不一致" % (MANIFEST_NAME, manifest.get("version"), version)
        )
    listed = manifest.get("files")
    if not isinstance(listed, list) or not listed:
        raise ReleaseError("%s 里的 files 不是非空列表" % MANIFEST_NAME)
    seen = set()
    for item in listed:
        if not isinstance(item, dict) or "path" not in item or "sha256" not in item:
            raise ReleaseError("%s 里有不完整的文件条目：%r" % (MANIFEST_NAME, item))
        path = item["path"]
        if path not in blob:
            raise ReleaseError("清单登记了产物里不存在的文件：%s" % path)
        digest = _sha256(blob[path])
        if digest != item["sha256"]:
            raise ReleaseError(
                "清单哈希与实际内容不符：%s 清单 %s… 实际 %s…"
                % (path, str(item["sha256"])[:12], digest[:12])
            )
        if int(item.get("bytes", -1)) != len(blob[path]):
            raise ReleaseError("清单字节数与实际不符：%s" % path)
        if path in seen:
            raise ReleaseError("清单里 %s 出现多次" % path)
        seen.add(path)
    if seen != set(expected):
        raise ReleaseError("清单覆盖的文件集合与成员不一致")

    # 4) 本机绝对路径不许留在任何成员（含清单）里。
    leaks = find_leaked_paths(blob, _forbidden_literals(source_root, Path(zip_path).parent))
    if leaks:
        raise ReleaseError("产物里泄漏了本机绝对路径：\n  " + "\n  ".join(leaks))

    # 5) 产物里每一个带主机的地址字面量：不是本机，就必须被单独放行（§16.7 的真正失败模式）。
    violations, inert = scan_artifact(blob)
    stale = find_stale_inert_entries(blob)
    if violations or stale:
        detail = violations + stale
        raise ReleaseError(
            "打包产物里出现未被放行的外部主机引用（§16.7 离线会失败），或惰性名单已脱节：\n  "
            + "\n  ".join(detail)
        )

    return {
        "members": sorted(names),
        "manifest": manifest,
        "inert_references": inert,
        "violations": violations,
    }


# -------------------------------------------------------------------------- CLI

def _print_result(result: Dict[str, object], stream=sys.stdout) -> None:
    print("已生成发布包：%s" % result["zip"], file=stream)
    print("  版本 %s，成员 %d 个，产物 %d 字节（含 %s）"
          % (result["version"], result["file_count"], result["zip_bytes"], MANIFEST_NAME), file=stream)
    print("  产物 sha256：%s" % result["zip_sha256"], file=stream)
    inert = result["inert_references"] or {}
    total = sum(len(v) for v in inert.values())
    print(
        "  外部主机引用：0 个；逐条放行、确认发不出请求的地址字面量 %d 处（分布在 %d 个文件，名单共 %d 条）"
        % (total, len(inert), len(INERT_URL_LITERALS)),
        file=stream,
    )
    for rel, hits in sorted(inert.items()):
        for line, url in hits:
            print("    已放行 %s:%d %s" % (rel, line, url), file=stream)
    print("  发布说明（取自 %s）：" % FEATURE_CHANGES_DOC, file=stream)
    for note in result["release_notes"]:
        print("    %s" % note, file=stream)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="make_release.py",
        description="构建 MCChecker 离线发布包（规范 §14.3；M4-06 / INH-1908）",
    )
    parser.add_argument(
        "--source-root",
        default=None,
        help="源目录（默认本仓库根目录）",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="产物 zip 路径（默认 <源目录>/dist/MCChecker-<版本>.zip）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="允许覆盖已存在的产物",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="不构建，只按同一套规则验收已有产物",
    )
    args = parser.parse_args(list(sys.argv[1:]) if argv is None else list(argv))

    source_root = Path(args.source_root) if args.source_root else Path(__file__).resolve().parents[1]
    if not source_root.is_dir():
        print("失败：源目录不存在 %s" % source_root, file=sys.stderr)
        return 2

    try:
        if args.verify_only:
            if not args.out:
                print("失败：--verify-only 需要 --out 指定要验收的产物", file=sys.stderr)
                return 2
            report = verify_release(Path(args.out), source_root=source_root)
            print("验收通过：%s（成员 %d 个，外部主机引用 0 个）"
                  % (args.out, len(report["members"]) - 1))
            return 0
        result = build_release(
            source_root=source_root,
            out_path=Path(args.out) if args.out else None,
            force=args.force,
        )
    except ReleaseError as exc:
        print("发布包构建失败：%s" % exc, file=sys.stderr)
        return 1
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
