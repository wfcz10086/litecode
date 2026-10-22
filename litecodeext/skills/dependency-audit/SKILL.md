---
name: dependency-audit
description: >
  依赖与安全检查: 扫描项目依赖的已知漏洞 (CVE)、过期和弃用的包、许可证风险、代码里硬编码的密钥, 给出升级方案并验证。
  适用于依赖漏洞扫描、依赖升级、许可证合规、密钥泄露排查。Python 用 pip-audit, Node 用 npm audit, Go 用 govulncheck。
aliases: ["依赖漏洞", "安全漏洞", "pip-audit", "npm audit", "govulncheck"]
---

# 依赖与安全检查

## Keywords
依赖漏洞 漏洞扫描 安全检查 安全审计 安全扫描 cve CVE pip-audit npm audit osv 升级依赖 依赖升级 过期依赖 outdated 弃用 许可证 license 商用 GPL AGPL 硬编码密钥 泄露密钥 供应链 dependabot

## 1. 先认清项目用什么生态
```
execute_shell(command="ls pyproject.toml requirements*.txt uv.lock poetry.lock package.json package-lock.json pnpm-lock.yaml yarn.lock go.mod Cargo.toml 2>/dev/null")
```
有 lock 文件就以 lock 文件为准 (那才是实际装的版本)。

## 2. 漏洞扫描

| 生态 | 命令 | 备注 |
| --- | --- | --- |
| Python | `pip install pip-audit` 后 `pip-audit -r requirements.txt` 或在项目环境里 `pip-audit` | 有 pyproject 没 requirements 时 `pip-audit .` |
| Node | `npm audit --omit=dev` (先看生产依赖) ; `npm audit` 全量 | pnpm: `pnpm audit`; yarn: `yarn npm audit` |
| Go | `go install golang.org/x/vuln/cmd/govulncheck@latest && govulncheck ./...` | 只报实际调用到的漏洞 |
| Rust | `cargo install cargo-audit && cargo audit` | |

扫描工具装不上或无网络时: 用 `package_search(query="包名", ecosystem=...)` 查最新版本, 再 `web_search(query="包名 版本 CVE", category="it")` 或 `github_search(kind="issues", query="包名 security")` 手工核对; 并在报告里写明"未经工具扫描"。

## 3. 过期 / 弃用
- Python: `pip list --outdated`; Node: `npm outdated`。
- 关键依赖用 `github_repo(action="info", repo=...)` 看是否已归档、最近一次推送; 一年以上没发版的标为"维护风险"。

## 4. 许可证
- Python: `pip install pip-licenses && pip-licenses --format=markdown`; Node: `npx license-checker --summary`。
- 闭源/商用项目重点标出 GPL / AGPL / SSPL / 无许可证 的包; 不下"可以商用"的法律结论, 只列事实和风险。

## 5. 硬编码密钥
```
execute_shell(command="git grep -nIE '(api[_-]?key|secret|token|passw(or)?d)\\s*[:=]\\s*[\"\\'][^\"\\']{8,}' -- . ':!*.lock' | head -50")
```
有 gitleaks 就用 `gitleaks detect --no-banner`。找到的结果只报文件:行和类型, **回复里不要原样贴出密钥值** (打码到前 4 位)。注意: 用户明确说过配置文件里放明文密钥是可接受现状的项目 (例如 LiteCode 自己的 config.json), 只提示不劝改。

## 6. 修复与验证
1. 按严重程度排: 有公开利用 / 远程可触发的先修; 只影响开发依赖的后修。
2. 优先**最小升级**: 升到修复版本所在的同一大版本 (patch / minor); 需要跨大版本的单独列出, 说明破坏性变化, 让用户拍板。
3. 改完重新安装 + 跑全量测试 + 再扫一次, 报告"修前 N 个 → 修后 M 个", 剩下的写明原因 (无修复版本 / 需大版本升级 / 误报)。
4. 不要为了让扫描清零就 `npm audit fix --force` 或删 lock 文件重装, 那会引入大量不受控的升级。

## 报告格式
| 包 | 当前版本 | 问题 (CVE/类型) | 严重度 | 修复版本 | 处理 |
| --- | --- | --- | --- | --- | --- |

末尾附: 使用的扫描命令、扫描时间、未能扫描的部分。
