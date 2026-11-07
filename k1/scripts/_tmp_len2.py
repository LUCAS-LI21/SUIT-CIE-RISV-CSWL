import csv, time, wave
import numpy as np, onnxruntime as ort
BASE="/root/k1-ai/models/panns"; SR=16000
rows=list(csv.DictReader(open(f"{BASE}/labels.csv",encoding="utf-8"))); rows.sort(key=lambda r:int(r["index"]))
names=[r["display_name"] for r in rows]; kd={n:i for i,n in enumerate(names)}
def rd(p):
    with wave.open(p,"rb") as w: sr=w.getframerate(); raw=w.readframes(w.getnframes())
    return np.frombuffer(raw,dtype="<i2").astype("float32")/32768.0, sr
def tile(x,d):
    need=int(SR*d)
    if len(x)<need: x=np.tile(x,int(np.ceil(need/max(1,len(x)))))
    return x[:need]
w,sr=rd("/tmp/real_dog.wav"); seg=w[int(2.0*sr):int(2.6*sr)]
n=int(round(len(seg)*SR/sr)); seg=np.interp(np.linspace(0,len(seg)-1,n),np.arange(len(seg)),seg).astype("float32")
so=ort.SessionOptions(); so.intra_op_num_threads=4; so.inter_op_num_threads=1
s=ort.InferenceSession(f"{BASE}/Cnn14_16k.onnx",so,providers=["CPUExecutionProvider"])
print("4线程 | 窗口  耗时    Dog  Bark  Growl  top1")
for d in (0.8,1.0,1.5,2.0,3.0):
    x=tile(seg,d)[None,:]; s.run(None,{"input_audio":x})
    t=time.time(); sc=s.run(None,{"input_audio":x})[0][0]; ms=(time.time()-t)*1000
    print(f"        {d:>4.1f}s {ms:>6.0f}ms {sc[kd['Dog']]:.2f}  {sc[kd['Bark']]:.2f}  {sc[kd['Growling']]:.2f}   {names[int(np.argmax(sc))]}")
