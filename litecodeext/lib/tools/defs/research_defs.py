"""lib/tools/defs/research_defs.py — 开源实现 / 库选型调研 (3 个): github_search / github_repo / package_search."""

RESEARCH_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "github_search",
            "description": (
                "搜 GitHub: 找开源项目/参考实现/别人怎么写某段代码。kind: repositories 找项目 (返回星标/许可证/最近推送/topics); "
                "code 搜代码片段 (有 token 走 GitHub, 否则 grep.app/sourcegraph, 结果注明后端); issues 搜 issue+PR; "
                "commits 搜提交; topics 搜主题。无 token 时 search 10 次/分钟, 被限流会给出重置时间, 不要连续重试。"
                "报错排查优先用 search_code_error。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["repositories", "code", "issues", "commits", "topics"],
                             "description": "默认 repositories"},
                    "query": {"type": "string", "description": "关键词; code 用精确代码片段效果最好"},
                    "language": {"type": "string", "description": "可选语言过滤, 如 python / typescript / go"},
                    "sort": {"type": "string", "enum": ["best-match", "stars", "updated"],
                             "description": "repositories 默认 best-match; 选型一般用 stars"},
                    "limit": {"type": "integer", "description": "返回数, 默认 8, 最多 20"},
                    "qualifiers": {"type": "string",
                                   "description": "可选 GitHub 限定符原样追加, 如 'stars:>500 pushed:>2025-01-01' "
                                                  "/ 'repo:owner/name is:pr is:open' / 'org:fastapi'"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github_repo",
            "description": (
                "不 clone 就研读一个 GitHub 仓库。action: info 概况 (星标/许可证/活跃度/默认分支); readme; "
                "tree 目录树 (path 作前缀过滤); file 读文件 (超 20k 字符保留头尾, 二进制拒绝); "
                "releases 最近版本说明; commits 最近提交 (可按 path); search_in_repo 仓库内搜代码 (需 query)。"
                "readme/file 走 raw.githubusercontent.com 不占 API 配额。要大量阅读或运行时, "
                "用 execute_shell 'git clone --depth 1 <url>' 到工作区再 search_code。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string",
                               "enum": ["info", "readme", "tree", "file", "releases", "commits", "search_in_repo"]},
                    "repo": {"type": "string", "description": "owner/name 或 GitHub 链接 (链接里的分支/路径会自动带上)"},
                    "path": {"type": "string", "description": "file 必填; tree/commits 可选前缀"},
                    "ref": {"type": "string", "description": "分支/tag/commit, 默认仓库默认分支"},
                    "query": {"type": "string", "description": "search_in_repo 的搜索词"},
                    "limit": {"type": "integer",
                              "description": "tree 条目 (默认 200, 最多 1000) / releases (5) / commits (10) / 搜索 (10)"},
                },
                "required": ["action", "repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "package_search",
            "description": (
                "查包/库: PyPI / npm / crates.io / Go。给包名查最新版本、发布时间、简介、源码仓库; "
                "给功能关键词 (如 'markdown sanitize') 找候选库, npm 带周下载量, crates 带下载量。"
                "选型时配合 github_search + github_repo 看活跃度和许可证。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "包名或功能关键词"},
                    "ecosystem": {"type": "string", "enum": ["all", "pypi", "npm", "crates", "go"],
                                  "description": "默认 all (pypi+npm+crates)"},
                    "limit": {"type": "integer", "description": "每个生态返回数, 默认 5, 最多 10"},
                },
                "required": ["query"],
            },
        },
    },
]
