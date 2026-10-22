---
name: python-dev
description: >
  Python 项目工程化: 新建或整理 Python 项目时的 pyproject.toml、虚拟环境与依赖管理 (uv / pip)、ruff 检查与格式化、pytest 测试与覆盖率、类型检查。
  写 Python 脚本或包、修 Python 测试失败、配 lint 和测试命令时使用。Go 项目用 go skill。
aliases: ["pytest", "ruff", "pyproject", "uv pip"]
---

# Python 现代工程做法

## Keywords
python项目 python包 pyproject pyproject.toml uv venv 虚拟环境 requirements.txt pip ruff lint flake8 black isort 格式化 pytest 单元测试 覆盖率 coverage mypy pyright 类型检查 conftest fixture 打包 python脚本

## 0. 先探环境 (不要假设)
```
execute_shell(command="python3 --version; command -v uv ruff pytest mypy; ls pyproject.toml setup.py requirements*.txt uv.lock 2>/dev/null")
```
- 项目已有工具链 (poetry / pip-tools / tox / 已有的 lint 配置) 就**沿用项目的**, 不要擅自迁移。
- 缺工具就装: `pip install uv ruff pytest` (LiteCode 的 execute_shell 会自动补 `--break-system-packages`)。装不上 uv 就退回 `python3 -m venv .venv && .venv/bin/pip install ...`, 不要卡住。

## 1. 新项目骨架 (没有现成约定时)
```
uv init --package myproj && cd myproj     # 生成 pyproject.toml + src/myproj/
uv add httpx                              # 运行依赖, 自动写 pyproject 和 uv.lock
uv add --dev pytest ruff                  # 开发依赖
uv run pytest -q                          # 在项目虚拟环境里跑
```
没有 uv 时的等价物: `pyproject.toml` 手写 `[project]` 段 + `pip install -e ".[dev]"`。

依赖原则: 用标准库能搞定的不加依赖; 加依赖前用 `package_search(query="包名", ecosystem="pypi")` 看最新版本和维护情况; 版本写下限 (`httpx>=0.27`), 应用项目用 lock 文件锁死。

## 2. ruff: lint + 格式化一个工具搞定
```
ruff check . --fix      # 修能自动修的 (未用 import、排序等)
ruff format .           # 等价 black
ruff check .            # 剩下的必须人工看
```
pyproject 推荐最小配置:
```toml
[tool.ruff]
line-length = 100
target-version = "py310"
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```
**在已有项目里只对你改过的文件跑格式化** (`ruff format path/a.py path/b.py`), 全仓格式化会制造巨大无关 diff。

## 3. pytest
- 跑法: `python3 -m pytest -q` 全量; `-x` 遇错即停; `-k 关键词` 选用例; `tests/test_a.py::test_x` 单个; `--lf` 只跑上次失败的; `-vv` 看详细断言。
- 输出很长时 `2>&1 | tail -30` 看结尾的统计行, 交付时引用那一行原文。
- 长测试给 execute_shell 传 `timeout` (前台最多 900 秒); 更长的用 `background=true` 跑, `tail_log` 看进度。
- 写测试: 一个测试只验一件事; 用 `tmp_path` fixture 放临时文件; 外部 HTTP / 时间 / 随机数用 `monkeypatch` 固定; 修 bug 先写一个能复现它的失败测试 (见 test-driven-development)。
- 覆盖率需要时: `pip install pytest-cov` 后 `pytest --cov=src --cov-report=term-missing`。

## 4. 类型检查 (可选, 项目有就跑)
`mypy src/` 或 `pyright`。项目原来没开就不要为了它大改; 新写的公共函数加类型注解即可。

## 5. 交付前清单
1. `ruff check` 无新增问题 (只看你改过的文件)
2. 相关测试 + 全量 `pytest` 通过, 引用统计行原文
3. 新增依赖已写进 pyproject / requirements, 并说明为什么加
4. 脚本类交付物实际运行过一次 (`python3 script.py --help` 或真实输入)

## 常见坑
- `ModuleNotFoundError` 但包明明装了: 多半是装进了别的解释器, 用 `python3 -m pip` / `uv run` 保持同一个环境。
- 测试里 import 不到 `src/` 下的包: 用 `pip install -e .` 或 pyproject 里 `[tool.pytest.ini_options] pythonpath = ["src"]`。
- 文件读写一律显式 `encoding="utf-8"`, 中文 Windows 默认 GBK 会出乱码。
