"""Index-AniSora V3.2 (Wan2.2 i2v-A14B) 多卡常驻推理服务.
启动: torchrun --nproc_per_node=$ANISORA_NPROC server.py   (FSDP 切 DiT/T5 + Ulysses 序列并行, nproc 须整除 40)
rank0 跑 HTTP (FastAPI), 其余 rank 在 gloo 控制组上等任务; 每个任务所有 rank 一起算, rank0 存 mp4.

接口 (端口 8000):
  POST /v1/videos            multipart: prompt, image (可多张, 同名字段重复), positions (逗号分隔, 0=首帧 0.5=中间 1=尾帧, 默认首图 0),
                             size=832*480|480*832|1280*720|720*1280 (默认 832*480), frame_num=81 (16fps, 81 帧≈5 秒, 须 4n+1),
                             steps=8, shift=5, guide_scale=1, seed=-1
                             → {"id","status":"queued"}
  GET  /v1/videos/{id}       → {"id","status":queued|running|succeeded|failed,"elapsed","url","error"}
  GET  /v1/videos/{id}/content  → video/mp4
  GET  /health
注意: V3.2 只发布了图生视频 (i2v) 权重, 没有纯文生视频; 不传 image 会返回 400。
"""
import os, sys, time, uuid, json, threading, queue, logging, random, datetime
sys.path.insert(0, "/app/anisoraV3.2")
import torch
import torch.distributed as dist
from PIL import Image

RANK = int(os.getenv("RANK", 0)); WORLD = int(os.getenv("WORLD_SIZE", 1)); LOCAL = int(os.getenv("LOCAL_RANK", 0))
CKPT = os.getenv("ANISORA_CKPT", "/models/V3.2")
OUT = os.getenv("ANISORA_OUT", "/data/outputs"); IN = os.getenv("ANISORA_IN", "/data/inputs")
logging.basicConfig(level=logging.INFO if RANK == 0 else logging.ERROR, format=f"[%(asctime)s r{RANK}] %(message)s", stream=sys.stdout)
torch.cuda.set_device(LOCAL)
if WORLD > 1:
    dist.init_process_group("nccl", timeout=datetime.timedelta(hours=1))
    CTRL = dist.new_group(backend="gloo", timeout=datetime.timedelta(days=3650))

import wan
from wan.configs import WAN_CONFIGS, MAX_AREA_CONFIGS, SUPPORTED_SIZES
from wan.modules.model import WanModel, rope_params
from wan.utils.utils import save_video
if WORLD > 1:
    from wan.distributed.util import init_distributed_group
    init_distributed_group()

# 省内存加载: rank0 以 bf16 读权重; 其它 rank 只建空壳 (meta→未初始化内存, 不占物理页), FSDP sync_module_states 从 rank0 广播真实权重
_orig_fp = WanModel.from_pretrained.__func__


def _from_pretrained(cls, path, subfolder=None, **kw):
    if RANK == 0:
        return _orig_fp(cls, path, subfolder=subfolder, torch_dtype=torch.bfloat16, **kw)
    cfg = cls.load_config(path, subfolder=subfolder)
    with torch.device("meta"):
        m = cls.from_config(cfg)
    m = m.to(torch.bfloat16).to_empty(device="cpu")
    d = m.dim // m.num_heads
    m.freqs = torch.cat([rope_params(1024, d - 4 * (d // 6)), rope_params(1024, 2 * (d // 6)), rope_params(1024, 2 * (d // 6))], dim=1)
    return m


WanModel.from_pretrained = classmethod(_from_pretrained)

# 换专家 (high→low) 时先回收缓存并记录显存; T5 默认放 CPU (每卡省 ~2.5GB 显存, 每 rank 占 ~11GB 内存)
import gc
import wan.image2video as _i2v
_orig_prep = _i2v.WanI2V._prepare_model_for_timestep


def _prep(self, t, boundary, offload_model):
    name = "high" if t.item() >= boundary else "low"
    if getattr(self, "_lc_last", None) != name:
        gc.collect(); torch.cuda.empty_cache()
        logging.info(f"expert -> {name}, allocated={torch.cuda.memory_allocated() / 2**30:.2f}G")
        self._lc_last = name
    return _orig_prep(self, t, boundary, offload_model)


_i2v.WanI2V._prepare_model_for_timestep = _prep
T5_CPU = os.getenv("ANISORA_T5_CPU", "1") == "1"
cfg = WAN_CONFIGS["i2v-A14B"]
t0 = time.time()
pipe = wan.WanI2V(config=cfg, checkpoint_dir=CKPT, checkpoint_dir_lowname="low_noise_model", checkpoint_dir_highname="high_noise_model",
                  device_id=LOCAL, rank=RANK, t5_fsdp=WORLD > 1 and not T5_CPU, dit_fsdp=WORLD > 1, use_sp=WORLD > 1, t5_cpu=T5_CPU)
logging.info(f"模型加载完成 {time.time() - t0:.0f}s, world={WORLD}")


def run_job(job):
    imgs = [Image.open(p).convert("RGB") for p in job["images"]]
    video = pipe.generate(job["prompt"], imgs, job["positions"], max_area=MAX_AREA_CONFIGS[job["size"]], frame_num=job["frame_num"],
                          shift=job["shift"], sample_solver="unipc", sampling_steps=job["steps"], guide_scale=job["guide_scale"],
                          seed=job["seed"], offload_model=False)
    if RANK == 0:
        save_video(tensor=video[None], save_file=job["out"], fps=cfg.sample_fps, nrow=1, normalize=True, value_range=(-1, 1))
    del video
    pipe._lc_last = None
    gc.collect(); torch.cuda.empty_cache()


def bcast(obj):
    box = [obj]
    if WORLD > 1:
        dist.broadcast_object_list(box, src=0, group=CTRL)
    return box[0]


if RANK != 0:  # worker 循环
    while True:
        job = bcast(None)
        if job is None:
            break
        try:
            run_job(job)
        except Exception as e:  # rank0 会记录失败
            print(f"rank{RANK} job error: {e}", flush=True)
    sys.exit(0)

# ---------------- rank0: HTTP ----------------
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse
from typing import List, Optional
import uvicorn

os.makedirs(OUT, exist_ok=True); os.makedirs(IN, exist_ok=True)
JOBS, Q = {}, queue.Queue()
app = FastAPI(title="Index-AniSora V3.2")


def worker():
    while True:
        jid = Q.get(); j = JOBS[jid]
        j["status"] = "running"; j["started"] = time.time()
        try:
            bcast(j["job"]); run_job(j["job"])
            j["status"] = "succeeded"
        except Exception as e:
            logging.exception("job failed"); j["status"] = "failed"; j["error"] = str(e)
        j["elapsed"] = round(time.time() - j["started"], 1)
        logging.info(f"job {jid} {j['status']} {j['elapsed']}s")


threading.Thread(target=worker, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "ok", "model": "Index-AniSora V3.2 i2v-A14B (bf16)", "world_size": WORLD,
            "queue": Q.qsize(), "running": sum(1 for j in JOBS.values() if j["status"] == "running")}


@app.post("/v1/videos")
async def create(prompt: str = Form(...), image: Optional[List[UploadFile]] = File(None), positions: Optional[str] = Form(None),
                 size: str = Form("832*480"), frame_num: int = Form(81), steps: int = Form(8), shift: float = Form(5.0),
                 guide_scale: float = Form(1.0), seed: int = Form(-1)):
    if not image:
        raise HTTPException(400, "AniSora V3.2 只支持图生视频: 请上传 image (首帧/任意帧参考图); 纯文生视频可先用生图服务出首帧再调本接口")
    if size not in SUPPORTED_SIZES["i2v-A14B"]:
        raise HTTPException(400, f"size 只支持 {SUPPORTED_SIZES['i2v-A14B']}")
    if frame_num < 5 or (frame_num - 1) % 4:
        raise HTTPException(400, "frame_num 须为 4n+1 (81 帧≈5 秒 @16fps)")
    jid = uuid.uuid4().hex[:16]
    paths = []
    for i, f in enumerate(image):
        p = os.path.join(IN, f"{jid}_{i}{os.path.splitext(f.filename or '')[1] or '.png'}")
        open(p, "wb").write(await f.read()); paths.append(p)
    pos = [float(x) for x in positions.split(",")] if positions else [0.0] + [1.0] * (len(paths) - 1)
    if len(pos) != len(paths):
        raise HTTPException(400, "positions 个数须与 image 张数一致")
    job = dict(prompt=prompt, images=paths, positions=pos, size=size, frame_num=frame_num, steps=steps, shift=shift,
               guide_scale=guide_scale, seed=seed if seed >= 0 else random.randint(0, 2**31 - 1), out=os.path.join(OUT, f"{jid}.mp4"))
    JOBS[jid] = {"status": "queued", "job": job, "created": time.time()}
    Q.put(jid)
    return {"id": jid, "status": "queued", "queue_position": Q.qsize()}


@app.get("/v1/videos/{jid}")
def get(jid: str):
    j = JOBS.get(jid) or HTTPException(404, "无此任务")
    if isinstance(j, HTTPException):
        raise j
    r = {"id": jid, "status": j["status"], "elapsed": j.get("elapsed"), "error": j.get("error"), "seed": j["job"]["seed"]}
    if j["status"] == "succeeded":
        r["url"] = f"/v1/videos/{jid}/content"
    return r


@app.get("/v1/videos/{jid}/content")
def content(jid: str):
    j = JOBS.get(jid)
    if not j or j["status"] != "succeeded":
        raise HTTPException(404, "视频未就绪")
    return FileResponse(j["job"]["out"], media_type="video/mp4", filename=f"{jid}.mp4")


uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)), log_level="info")
