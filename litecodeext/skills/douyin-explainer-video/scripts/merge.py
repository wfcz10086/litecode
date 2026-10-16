#!/usr/bin/env python3
"""step4b/5: 旁白按时间轴拼接 + BGM 混音 (旁白时 BGM 自动闪避) + ffmpeg 编码 final.mp4
   用法 (在项目目录): python3 merge.py mix     # 仅音频 → mix.wav (可和逐帧渲染并行)
                      python3 merge.py final   # frames/ + mix.wav → final.mp4"""
import json, os, subprocess, sys, wave
import numpy as np

SR = 44100
FPS = int(os.environ.get("FPS", 24))
TL = json.load(open("timeline.json"))

def decode(mp3):                      # mp3 → mono float32 @SR
    raw = subprocess.run(["ffmpeg","-v","error","-i",mp3,"-f","f32le","-ac","1",
                          "-ar",str(SR),"-"],capture_output=True,check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)

def build_narration():
    n = int((TL["total"]+1.0)*SR)
    track = np.zeros(n)
    for s in TL["scenes"]:
        a = decode(f"audio/{s['id']}.mp3")
        i = int(s["start"]*SR)
        track[i:i+len(a)] += a[:max(0, n-i)]
    return track

def mix():
    vox = build_narration()
    with wave.open("bgm.wav") as w:
        bgm = np.frombuffer(w.readframes(w.getnframes()),
                            dtype=np.int16).astype(np.float32)/32768
    m = min(len(vox), len(bgm))
    out = vox[:m] + bgm[:m]*np.minimum(1.0, 0.55+0.45*np.exp(-np.abs(vox[:m])*6))  # 旁白闪避
    peak = np.max(np.abs(out))
    if peak > .95: out *= .95/peak                       # 砖限保护
    with wave.open("mix.wav","w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((out*32767).astype(np.int16).tobytes())
    print("mix.wav", round(len(out)/SR,1),"s peak", round(float(peak),3))

def final():
    # render.py 默认出 jpg; 老流程 / FRAME_FMT=png 出 png —— 有哪个用哪个
    FRAMES = "frames/f%05d.jpg" if os.path.exists("frames/f00000.jpg") else "frames/f%05d.png"
    subprocess.run(["ffmpeg","-y","-v","error","-framerate",str(FPS),
        "-i",FRAMES,"-i","mix.wav",
        "-c:v","libx264","-preset","medium","-crf","18","-pix_fmt","yuv420p",
        "-r",str(FPS),"-c:a","aac","-b:a","160k","-shortest","-movflags","+faststart",
        "final.mp4"], check=True)
    d = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration,size",
        "-of","csv=p=0","final.mp4"],capture_output=True,text=True).stdout.strip()
    print("final.mp4:", d)

if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv)>1 else "mix"
    (mix if step=="mix" else final)()
