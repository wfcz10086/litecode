# 版本与分支约定

## 版本号
- 点分数字 `主.次.修订` (如 `2.0.3`), 源码里在 `litecodeext/lib/version.py` 的 `VERSION`。
- 每个发布都有 git 标签 `v<版本号>`, 打在**出包的那个提交**上; 回到某个版本: `git checkout v2.0.2`。
- 发布只用 `bash packaging/release.sh <版本号> [--full]`: 预检要求工作区 (除 `config.json`) 没有未提交改动, 同名标签只能指向当前提交; 发布成功后自动打标签并推送 master 与标签。

## 分支
- `master` = 生产: 生产容器直接挂载这个工作区, master 上的东西随时可能被重启加载, 所以只合并测过的改动。
- 并行开发 (含子代理) 各在自己的 worktree 分支 `worktree-agent-*` 上提交, 不直接改 master。
- 合并流程: 建集成分支 `release/<版本号>` → 按依赖顺序逐个 `git merge --no-ff <分支>` → 每合一个跑 `scripts/run_release_tests.sh` → 全部通过再快进 master → 生产空闲时重启 (`scripts/litecode_idle.py --wait`) → 实测 → `release.sh` 出包打标签。
- 合并完的 worktree/分支及时清理: `git worktree remove <路径> && git branch -d <分支>` (只删已合并的)。

## 不要做
- 不提交 `config.json` (本机私有配置, 含密钥)。
- 不用 `git stash pop`: 栈里有很早以前的 stash, 弹错会把旧改动混进生产挂载的工作区。临时还原单个文件用 `cp` 备份 + `git checkout HEAD -- <文件>`。
- 不在 master 上 `git reset --hard` / 强推。
