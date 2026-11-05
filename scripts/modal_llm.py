"""A vLLM server with an OpenAI-compatible API on one Modal GPU, for the room's agents.

This is optional: the agents work with any OpenAI-compatible endpoint (OpenAI, OpenRouter, a local Ollama or
vLLM). Deploy it with `python scripts/modal_llm_setup.py`, which also creates the endpoint key.

The model is Qwen2.5-72B-Instruct (4-bit AWQ, pinned revision), served with vLLM's Hermes tool-call parser so
the agents can call whoseagent's tools. /health is open; /v1 needs the bearer key in the Modal secret
"whose-agent-llm" (env VLLM_API_KEY, read by vLLM). The server shuts down 5 minutes after the last request,
so it costs nothing while idle.
"""
import os

import modal

GPU = os.environ.get("WA_GPU", "H100:1")
MODEL = os.environ.get("WA_MODEL", "Qwen/Qwen2.5-72B-Instruct-AWQ")
REVISION = os.environ.get("WA_REVISION", "698703eae6604af048a3d2f509995dc302088217")  # pinned HF commit
SERVED = os.environ.get("WA_SERVED", "whoseagent-qwen72b")
MAX_LEN = os.environ.get("WA_MAXLEN", "16384")
VLLM_VERSION = "0.21.0"
MINUTES = 60
PORT = 8000

image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install(f"vllm=={VLLM_VERSION}")
    .env({"HF_XET_HIGH_PERFORMANCE": "1"})
)
hf_cache = modal.Volume.from_name("huggingface-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("vllm-cache", create_if_missing=True)
app = modal.App("whose-agent-llm")


@app.server(
    image=image,
    gpu=GPU,
    scaledown_window=5 * MINUTES,  # stops billing 5 minutes after the last request
    startup_timeout=25 * MINUTES,  # a first boot may download the weights into the volume
    volumes={"/root/.cache/huggingface": hf_cache, "/root/.cache/vllm": vllm_cache},
    secrets=[modal.Secret.from_name("whose-agent-llm")],
    env={"WA_MODEL": MODEL, "WA_REVISION": REVISION, "WA_SERVED": SERVED, "WA_MAXLEN": MAX_LEN},
    port=PORT,
    target_concurrency=16,
    unauthenticated=True,  # Modal-level auth off; vLLM itself requires the bearer key on /v1
)
class Server:
    @modal.enter()
    def start(self):
        import subprocess

        cmd = ["vllm", "serve", MODEL, "--revision", REVISION, "--served-model-name", SERVED,
               "--host", "0.0.0.0", "--port", str(PORT), "--max-model-len", MAX_LEN,
               "--gpu-memory-utilization", "0.92", "--max-num-seqs", "16", "--generation-config", "vllm",
               "--enable-auto-tool-choice", "--tool-call-parser", "hermes", "--uvicorn-log-level=warning"]
        print("starting:", " ".join(cmd))  # the key comes from the VLLM_API_KEY env var and is never printed
        self.process = subprocess.Popen(cmd)

    @modal.exit()
    def stop(self):
        self.process.terminate()
