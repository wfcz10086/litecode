import json, sys, time, urllib.request
import os
BASE = os.environ.get("ACE_URL", "http://127.0.0.1:18012")
def post(path, body):
    r = urllib.request.Request(BASE + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=60))
lyrics = """[verse]
清晨的风吹过小城的街
阳光洒在你微笑的脸
我们走过熟悉的路口
把昨天的烦恼都留在身后

[chorus]
一起唱吧 让心自由飞翔
穿过云层 去看远方的光
不管路有多长 有你在身旁
每一天都是新的希望
"""
body = {"prompt": "华语流行, 温暖明亮, 女声主唱, 钢琴与木吉他, 轻快鼓点", "lyrics": lyrics,
        "vocal_language": "zh", "audio_duration": float(sys.argv[1]) if len(sys.argv) > 1 else 45,
        "thinking": True, "audio_format": "mp3", "batch_size": 1}
t0 = time.time()
r = post("/release_task", body); print("release:", r, flush=True)
tid = r["data"]["task_id"]
while True:
    time.sleep(3)
    q = post("/query_result", {"task_id_list": [tid]})["data"][0]
    if q.get("status") in (1, 2):
        break
el = time.time() - t0
print("status", q.get("status"), "elapsed %.1fs" % el)
res = json.loads(q["result"]) if isinstance(q.get("result"), str) else q.get("result")
print(json.dumps(res, ensure_ascii=False)[:1500])
if q.get("status") == 1:
    url = BASE + res[0]["file"]
    urllib.request.urlretrieve(url, "ace_song.mp3")
    print("saved ace_song.mp3", url)
