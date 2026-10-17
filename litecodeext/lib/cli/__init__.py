"""litecli 子命令包: 命令按组放在 lib/cli/cmd_*.py, 各自声明 COMMANDS, 由 discover() 自动注册。

插拔: 新增一组命令 = 在这里加一个 cmd_<名>.py 并写 COMMANDS = [...]; 外部插件把含 COMMANDS 的
.py 放进 LITECODE_CLI_PLUGIN_PATH 指定的目录 (多个用系统路径分隔符), 移走即卸载。
内置命令优先; 插件重名或加载失败只告警跳过, 不影响其余命令。
"""
import importlib
import importlib.util
import os
import pkgutil
import sys
from pathlib import Path

PLUGIN_ENV = "LITECODE_CLI_PLUGIN_PATH"


def _warn(msg: str):
    print(f"[litecli] {msg}", file=sys.stderr)


def _builtin_modules():
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("cmd_"):
            yield importlib.import_module(f"{__name__}.{info.name}")


def _plugin_modules():
    for folder in filter(None, os.environ.get(PLUGIN_ENV, "").split(os.pathsep)):
        for path in sorted(Path(folder).glob("*.py")):
            name = f"litecli_plugin_{path.stem}"
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                _warn(f"插件 {path} 无法作为模块加载, 已跳过")
                continue
            try:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                yield module
            except Exception as e:  # 单个插件坏掉不能拖垮整个 CLI
                _warn(f"插件 {path} 加载失败, 已跳过: {e}")


def discover(taken=()):
    """返回待注册的 click 命令列表; taken 为根命令里已存在的名字 (如 chat / repl)。"""
    names = set(taken)
    found = []
    for builtin, modules in ((True, _builtin_modules()), (False, _plugin_modules())):
        for module in modules:
            for cmd in getattr(module, "COMMANDS", ()):
                if cmd.name in names:
                    if builtin:
                        raise RuntimeError(f"内置命令重名: {cmd.name} ({module.__name__})")
                    _warn(f"插件命令 {cmd.name} 与已有命令重名, 已跳过 ({module.__file__})")
                    continue
                names.add(cmd.name)
                found.append(cmd)
    return found
