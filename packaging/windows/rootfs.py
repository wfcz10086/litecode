"""LiteCode WSL 系统包分层构建 (加速: 依赖层缓存 + 代码层每次重打, gzip 多段直接拼接, 不重压 4GB)。

  rootfs.py deps <镜像> <输出.tar.gz>        docker export 镜像 → 去掉 /opt/litecode 和 .dockerenv → 隐私扫描 →
                                             压缩 (不写 tar 结束标记, 供后面拼代码层)
  rootfs.py code <公开导出目录> <版本> <输出.tar.gz> [--wsl-files <目录>]
                                             代码层: opt/litecode (公开版代码+扩展技能+默认配置) + entrypoint.sh +
                                             版本文件, 带 tar 结束标记; 同时做隐私扫描
  rootfs.py join <依赖层> <代码层> <输出>     两段 gzip 首尾相接 = 一个完整的 .tar.gz
  rootfs.py check <系统包>                   用 GNU tar / Python 读一遍, 确认能完整解出且关键文件都在

隐私扫描: 生产 config.json 里的真实密钥/密码 + 内部 IP/主机名, 命中即退出 1 (产物删掉)。
"""
import gzip
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time

PROD_CFG = "/opt/litecode/config.json"
# 固定特征 (内部 IP / 主机名 / 合作方名) 放在私有文件里, 不进公开版 (scripts/release_exclude.txt)
_PRIV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "private_patterns.txt")
FIXED = [l.strip() for l in open(_PRIV, encoding="utf-8") if l.strip() and not l.startswith("#")] \
    if os.path.exists(_PRIV) else []
SKIP_SCAN = ("usr/lib/", "usr/share/", "root/.cache/ms-playwright", "opt/google", "usr/bin", "usr/sbin", "lib/",
             "var/lib/apt", "var/cache")


def patterns():
    cfg = json.load(open(PROD_CFG))
    found = {}

    def walk(o, k=""):
        if isinstance(o, dict):
            for kk, v in o.items():
                walk(v, kk)
        elif isinstance(o, list):
            for v in o:
                walk(v, k)
        elif isinstance(o, str) and re.search(r"key|token|pass|secret|cookie", k, re.I) \
                and not k.endswith("_env") and len(o) >= 8 and "CHANGE_ME" not in o and o != "EMPTY":
            found.setdefault(o, k)
    walk(cfg)
    return [(f"secret:{k}", v.encode()) for v, k in found.items()] + [(s, s.encode()) for s in FIXED]


class Scanner:
    def __init__(self):
        self.pats = patterns()
        self.hits = {}
        self.n = 0

    def feed(self, name, data):
        if name.startswith(SKIP_SCAN) or len(data) > 20_000_000:
            return
        self.n += 1
        for label, p in self.pats:
            if p in data:
                self.hits.setdefault(label, []).append(name)

    def report(self, what):
        print(f"  隐私扫描[{what}]: {self.n} 个文件, 规则 {len(self.pats)} 条", flush=True)
        for k, v in self.hits.items():
            print("  命中", k, v[:5], flush=True)
        return not self.hits


class NoEndTar(tarfile.TarFile):
    """流式写 (w|), close() 时不写两个全零结束块和记录填充: 依赖层后面还要接代码层。"""
    def close(self):
        if self.closed:
            return
        self.closed = True
        self.fileobj.close()          # _Stream: 把缓冲写出; 外部传入的管道不关 (由调用方关)


def pigz_writer(path, level=6):
    out = open(path, "wb")
    p = subprocess.Popen(["pigz", "-p", str(os.cpu_count() or 4), f"-{level}"], stdin=subprocess.PIPE, stdout=out)
    return p, out


def cmd_deps(image, out_path):
    t0 = time.time()
    cid = subprocess.check_output(["docker", "create", image]).decode().strip()
    exp = subprocess.Popen(["docker", "export", cid], stdout=subprocess.PIPE)
    pz, fout = pigz_writer(out_path + ".part")
    sc = Scanner()
    src = tarfile.open(fileobj=exp.stdout, mode="r|")
    dst = NoEndTar.open(fileobj=pz.stdin, mode="w|", format=tarfile.PAX_FORMAT)
    n = 0
    for m in src:
        name = m.name.lstrip("./")
        # 代码层提供的文件一律不进依赖层: 被覆盖的旧版本内容也会留在包里 (实测底层镜像旧 entrypoint.sh 写死过 token)
        if name in (".dockerenv", "entrypoint.sh", "etc/litecode-version") or name == "opt/litecode" \
                or name.startswith("opt/litecode/"):
            continue
        data = src.extractfile(m).read() if m.isfile() else None
        if data is not None:
            sc.feed(name, data)
        dst.addfile(m, io.BytesIO(data) if data is not None else None)
        n += 1
    dst.close()
    pz.stdin.close()
    pz.wait()
    fout.close()
    exp.wait()
    subprocess.run(["docker", "rm", cid], stdout=subprocess.DEVNULL)
    ok = sc.report("依赖层")
    if not ok:
        os.remove(out_path + ".part")
        sys.exit(1)
    os.replace(out_path + ".part", out_path)
    print(f"  依赖层: {n} 个条目, {os.path.getsize(out_path)/1e9:.2f} GB, {time.time()-t0:.0f}s", flush=True)


def _add_tree(tf, root, arc_prefix, sc):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        rel = os.path.relpath(dirpath, root)
        arc_dir = arc_prefix if rel == "." else f"{arc_prefix}/{rel}"
        ti = tf.gettarinfo(dirpath, arc_dir)
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        tf.addfile(ti)
        for fn in sorted(filenames):
            fp = os.path.join(dirpath, fn)
            if os.path.islink(fp):
                ti = tf.gettarinfo(fp, f"{arc_dir}/{fn}")
                ti.uid = ti.gid = 0
                tf.addfile(ti)
                continue
            data = open(fp, "rb").read()
            sc.feed(f"{arc_dir}/{fn}", data)
            ti = tf.gettarinfo(fp, f"{arc_dir}/{fn}")
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = "root"
            tf.addfile(ti, io.BytesIO(data))


def _add_bytes(tf, name, data, mode=0o644):
    ti = tarfile.TarInfo(name)
    ti.size, ti.mode, ti.mtime = len(data), mode, int(time.time())
    ti.uname = ti.gname = "root"
    tf.addfile(ti, io.BytesIO(data))


def cmd_code(export_dir, ver, out_path):
    """代码层 (系统包里用, 带 opt/ 前缀和版本文件)。代码更新包另由 release.sh 打 (litecode/ + entrypoint.sh)。"""
    sc = Scanner()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tf:
        _add_tree(tf, os.path.join(export_dir, "litecodeext"), "opt/litecode", sc)
        _add_tree(tf, os.path.join(export_dir, "skills_extra"), "opt/litecode/skills", sc)
        cfg = open(os.path.join(export_dir, "config.example.json"), "rb").read()
        sc.feed("config.example.json", cfg)
        _add_bytes(tf, "opt/litecode/config.default.json", cfg)
        _add_bytes(tf, "opt/litecode/config.json", cfg, 0o600)
        _add_bytes(tf, "entrypoint.sh", open(os.path.join(export_dir, "entrypoint.sh"), "rb").read(), 0o755)
        _add_bytes(tf, "etc/litecode-version", f"{ver}\n".encode())
    if not sc.report("代码层"):
        sys.exit(1)
    with gzip.open(out_path, "wb", compresslevel=6) as g:
        g.write(raw.getvalue())
    print(f"  代码层: {os.path.getsize(out_path)/1e6:.1f} MB", flush=True)


def cmd_join(deps, code, out_path):
    t0 = time.time()
    with open(out_path + ".part", "wb") as out:
        for p in (deps, code):
            with open(p, "rb") as f:
                while True:
                    b = f.read(16 << 20)
                    if not b:
                        break
                    out.write(b)
    os.replace(out_path + ".part", out_path)
    print(f"  拼接: {os.path.getsize(out_path)/1e9:.2f} GB, {time.time()-t0:.0f}s", flush=True)


def cmd_check(path):
    t0 = time.time()
    need = {"etc/wsl.conf", "usr/local/bin/litecode-wsl-start", "usr/local/bin/litecode-apply-update",
            "opt/litecode/web_ui.py", "opt/litecode/litecode_server.py", "opt/litecode/config.default.json",
            "entrypoint.sh", "etc/litecode-version", "etc/litecode-rootfs-version"}
    seen, names = set(), 0
    out = subprocess.run(["tar", "-tzf", path], capture_output=True, text=True)
    for line in out.stdout.splitlines():
        names += 1
        n = line.lstrip("./").rstrip("/")
        if n in need:
            seen.add(n)
    bad = [l for l in out.stderr.splitlines() if "hdrcharset" not in l]
    miss = need - seen
    print(f"  校验: {names} 个条目, 缺 {sorted(miss) or '无'}, tar 报错 {bad[:3] or '无'}, {time.time()-t0:.0f}s")
    sys.exit(1 if miss or out.returncode not in (0,) and bad else 0)


if __name__ == "__main__":
    c = sys.argv[1]
    if c == "deps":
        cmd_deps(sys.argv[2], sys.argv[3])
    elif c == "code":
        cmd_code(sys.argv[2], sys.argv[3], sys.argv[4])
    elif c == "join":
        cmd_join(sys.argv[2], sys.argv[3], sys.argv[4])
    elif c == "check":
        cmd_check(sys.argv[2])
    else:
        sys.exit(__doc__)
