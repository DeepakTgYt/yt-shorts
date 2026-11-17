"""Run inside the VoiceStudio/OmniVoice venv. Loads the model once, synthesises
a JSON list of {"text","out"} jobs, writes WAVs with soundfile.
usage: python omnivoice_runner.py MODEL_DIR jobs.json [instruct] [num_step]"""
import json, sys, time
import soundfile as sf
import torch
from omnivoice.models.omnivoice import OmniVoice

model_dir, jobs_path = sys.argv[1], sys.argv[2]
instruct = sys.argv[3] if len(sys.argv) > 3 else "female"
steps = int(sys.argv[4]) if len(sys.argv) > 4 else 16
torch.set_num_threads(max(1, torch.get_num_threads()))
t0 = time.time()
model = OmniVoice.from_pretrained(model_dir, device_map="cpu", dtype=torch.float32)
print(f"LOAD {time.time()-t0:.1f}s", flush=True)
for job in json.load(open(jobs_path)):
    t = time.time()
    audios = model.generate(text=job["text"], language="hi", instruct=instruct, num_step=steps)
    a = audios[0]
    a = a.detach().cpu().numpy() if hasattr(a, "detach") else a
    if a.ndim == 2:
        a = a[0] if a.shape[0] < a.shape[1] else a[:, 0]
    sf.write(job["out"], a, model.sampling_rate)
    print(f"GEN {time.time()-t:.1f}s {len(a)/model.sampling_rate:.2f}s audio -> {job['out']}", flush=True)
