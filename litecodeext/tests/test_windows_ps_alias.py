"""PowerShell 自定义函数名不能撞系统别名: 别名优先于函数, 撞名后函数根本不会被调用。

2026-10-17 真机 (Windows 11, PowerShell 5.1) 发现: 安装命令里的哈希函数叫 H, 而 h 是 Get-History 的别名,
导致安装命令在所有真实 Windows 上都直接报错。ALIASES 取自该真机 `Get-Alias` 的完整输出。
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "core")]

ALIASES = frozenset(('%', '?', 'ac', 'asnp', 'cat', 'cd', 'cfs', 'chdir', 'clc', 'clear', 'clhy', 'cli', 'clp', 'cls', 'clv', 'cnsn', 'compare', 'copy', 'cp', 'cpi', 'cpp', 'curl', 'cvpa', 'dbp', 'del', 'diff', 'dir', 'dnsn', 'ebp', 'echo', 'epal', 'epcsv', 'epsn', 'erase', 'etsn', 'exsn', 'fc', 'fhx', 'fl', 'foreach', 'ft', 'fw', 'gal', 'gbp', 'gc', 'gci', 'gcm', 'gcs', 'gdr', 'ghy', 'gi', 'gjb', 'gl', 'gm', 'gmo', 'gp', 'gps', 'gpv', 'group', 'gsn', 'gsnp', 'gsv', 'gu', 'gv', 'gwmi', 'h', 'history', 'icm', 'iex', 'ihy', 'ii', 'ipal', 'ipcsv', 'ipmo', 'ipsn', 'irm', 'ise', 'iwmi', 'iwr', 'kill', 'lp', 'ls', 'man', 'md', 'measure', 'mi', 'mount', 'move', 'mp', 'mv', 'nal', 'ndr', 'ni', 'nmo', 'npssc', 'nsn', 'nv', 'ogv', 'oh', 'popd', 'ps', 'pushd', 'pwd', 'r', 'rbp', 'rcjb', 'rcsn', 'rd', 'rdr', 'ren', 'ri', 'rjb', 'rm', 'rmdir', 'rmo', 'rni', 'rnp', 'rp', 'rsn', 'rsnp', 'rujb', 'rv', 'rvpa', 'rwmi', 'sajb', 'sal', 'saps', 'sasv', 'sbp', 'sc', 'select', 'set', 'shcm', 'si', 'sl', 'sleep', 'sls', 'sort', 'sp', 'spjb', 'spps', 'spsv', 'start', 'sujb', 'sv', 'swmi', 'tee', 'trcm', 'type', 'wget', 'where', 'wjb', 'write'))


def _defined_functions(text):
    return re.findall(r"function\s+([A-Za-z][\w-]*)", text)


def test_ps_sources_define_no_alias_named_functions():
    bad = []
    for pattern in ("lib/windows_*.ps1", "lib/windows_*.py", "routers/windows_*.py"):
        for f in glob.glob(os.path.join(ROOT, pattern)):
            for name in _defined_functions(open(f, encoding="utf-8", errors="replace").read()):
                if name.lower() in ALIASES:
                    bad.append(f"{os.path.relpath(f, ROOT)}: function {name}")
    assert not bad, "自定义函数名撞了 PowerShell 内置别名 (会被别名劫持): " + ", ".join(bad)


def test_generated_install_command_has_no_alias_named_functions():
    import base64
    from lib import windows_nodes as wn
    import inspect
    src = inspect.getsource(wn)
    for name in _defined_functions(src):
        assert name.lower() not in ALIASES, name
