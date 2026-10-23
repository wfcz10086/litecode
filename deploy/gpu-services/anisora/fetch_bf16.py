"""流式把 ModelScope 上的 FP32 safetensors 转成 BF16 分片, 不在本地落 FP32 原文件。
用 HTTP Range 按张量区间分批拉取 → 转 bf16 → 写 ≤5GB 分片 + diffusers 索引 json。
Wan2.2 推理本身就以 bf16 (param_dtype) 运行, 所以对推理结果无损, 体积减半 (57GB → 28.6GB/专家)。
用法: python fetch_bf16.py <repo> <远端文件路径> <输出目录>
"""
import json, os, struct, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import torch
from safetensors.torch import save_file

REPO, RPATH, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
URL = f"https://www.modelscope.cn/models/{REPO}/resolve/master/{RPATH}"
BATCH = 256 << 20          # 每次 Range 拉 256MB
SHARD = 5 << 30            # bf16 分片上限 5GB
PAR = 6                    # 并发 Range 数


def get(a, b, tries=8):
    for i in range(tries):
        try:
            req = urllib.request.Request(URL, headers={"Range": f"bytes={a}-{b - 1}"})
            with urllib.request.urlopen(req, timeout=300) as r:
                data = r.read()
            if len(data) == b - a:
                return data
            raise IOError(f"short read {len(data)} != {b - a}")
        except Exception as e:
            print(f"retry {i} [{a},{b}): {e}", flush=True); time.sleep(3 * (i + 1))
    raise SystemExit(f"下载失败 [{a},{b})")


n = struct.unpack("<Q", get(0, 8))[0]
hdr = json.loads(get(8, 8 + n)); hdr.pop("__metadata__", None)
base = 8 + n
items = sorted(hdr.items(), key=lambda kv: kv[1]["data_offsets"][0])
total = items[-1][1]["data_offsets"][1]
# 按 256MB 切批 (批内是连续的若干张量)
batches, cur, cur_start = [], [], None
for k, v in items:
    s, e = v["data_offsets"]
    if cur and e - cur_start > BATCH:
        batches.append(cur); cur = []
    if not cur:
        cur_start = s
    cur.append((k, v))
if cur:
    batches.append(cur)
if os.environ.get("FETCH_LIMIT"):  # 调试: 只拉前 N 批
    batches = batches[:int(os.environ["FETCH_LIMIT"])]
print(f"{RPATH}: {len(items)} tensors, {total/1e9:.1f}GB fp32, {len(batches)} batches", flush=True)


def fetch(batch):
    s = batch[0][1]["data_offsets"][0]; e = batch[-1][1]["data_offsets"][1]
    buf = get(base + s, base + e)
    out = {}
    for k, v in batch:
        a, b = v["data_offsets"]
        t = torch.frombuffer(bytearray(buf[a - s:b - s]), dtype={"F32": torch.float32, "BF16": torch.bfloat16, "F16": torch.float16}[v["dtype"]])
        t = t.reshape(v["shape"])
        out[k] = t.to(torch.bfloat16) if t.dtype == torch.float32 else t
    return out


os.makedirs(OUT, exist_ok=True)
stem = os.path.basename(RPATH).replace(".safetensors", "")
shards, acc, acc_size, done, t0 = [], {}, 0, 0, time.time()


def flush():
    global acc, acc_size
    if acc:
        name = f"{stem}-{len(shards) + 1:05d}.safetensors"
        save_file(acc, os.path.join(OUT, name), metadata={"format": "pt"})
        shards.append((name, list(acc.keys()))); acc, acc_size = {}, 0


with ThreadPoolExecutor(PAR) as ex:
    for i in range(0, len(batches), PAR):
        for res in ex.map(fetch, batches[i:i + PAR]):
            for k, t in res.items():
                sz = t.numel() * t.element_size()
                if acc_size + sz > SHARD:
                    flush()
                acc[k] = t; acc_size += sz; done += sz * 2
        el = time.time() - t0
        print(f"  {min(i + PAR, len(batches))}/{len(batches)} batches, {done/1e9:.1f}GB fp32 done, {done/el/1e6:.0f}MB/s", flush=True)
flush()
# 改名为 diffusers 标准分片名并写索引
N = len(shards); wm = {}; tsize = 0
for i, (name, keys) in enumerate(shards):
    final = f"{stem}-{i + 1:05d}-of-{N:05d}.safetensors"
    os.rename(os.path.join(OUT, name), os.path.join(OUT, final))
    for k in keys:
        wm[k] = final
for k, v in hdr.items():
    tsize += (v["data_offsets"][1] - v["data_offsets"][0]) // (2 if v["dtype"] == "F32" else 1)
json.dump({"metadata": {"total_size": tsize}, "weight_map": wm}, open(os.path.join(OUT, stem + ".safetensors.index.json"), "w"))
print(f"OK {N} shards, {tsize/1e9:.1f}GB bf16, {time.time() - t0:.0f}s", flush=True)
