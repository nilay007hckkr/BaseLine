# Ollama on Arch Linux

Ollama is Hindsight's local LLM: the last provider in the chain (Gemini → Groq → Ollama) and the
embedding model behind semantic search (`make embed`). It is optional. Without it, answers and
incident briefs fall back to sentences quoted from the evidence.

Package versions below were checked on archlinux.org on 2026-10-31: `ollama` and `ollama-cuda`
0.40.13-1, `nvidia-open` and `nvidia-utils` 615.78.08, `cuda` 13.4.3.

## 1. Update the system

Arch does not support partial upgrades, so update everything before installing:

```bash
sudo pacman -Syu
```

## 2. NVIDIA driver (skip if `nvidia-smi` already works)

```bash
lspci | grep -iE 'vga|3d'          # which GPU the laptop has
nvidia-smi                         # if this prints the GPU, go to step 3
```

The open kernel modules support Turing and newer GPUs, which includes the RTX 3050 Laptop GPU.
Pick the package that matches your kernel:

```bash
sudo pacman -S --needed nvidia-open nvidia-utils         # stock `linux` kernel
# sudo pacman -S --needed nvidia-open-lts nvidia-utils   # `linux-lts` kernel
# sudo pacman -S --needed nvidia-open-dkms nvidia-utils  # any other kernel (install its -headers too)
sudo reboot
nvidia-smi
```

## 3. Install Ollama from the official repository

```bash
sudo pacman -S --needed ollama-cuda    # NVIDIA; also installs `ollama` and `cuda`
```

This downloads about 3.4 GB (`ollama-cuda` 0.82 GB and `cuda` 2.61 GB, which takes 5.58 GB once
installed). Other variants: `ollama` (CPU only), `ollama-vulkan`, `ollama-rocm` (AMD).
`make bootstrap` runs the same command on Arch.

Make sure no hand-installed copy shadows the package. A partial copy from `install.sh` or a
tarball causes `llama-server binary not found`:

```bash
which -a ollama                        # should print only /usr/bin/ollama
pacman -Ql ollama | grep llama-server  # the package ships /usr/lib/ollama/llama-server
```

If `which -a` also lists `~/.local/bin/ollama` or `/usr/local/bin/ollama`, remove those copies:

```bash
rm -rf ~/.local/bin/ollama ~/.local/lib/ollama
sudo rm -rf /usr/local/bin/ollama /usr/local/lib/ollama
```

## 4. Run it as a service

```bash
sudo systemctl enable --now ollama
systemctl status ollama --no-pager
curl -s http://localhost:11434/api/version
```

The packaged service runs as the `ollama` user and keeps models in `/var/lib/ollama`. Its unit sets
`ProtectHome=yes`, so it cannot read models stored under your home directory. Do not also start
`ollama serve` by hand: both would want port 11434.

## 5. Pull the models named in `.env`

```bash
cd ~/code/hindsight
grep -E '^OLLAMA_(HOST|CHAT_MODEL|EMBED_MODEL)=' .env
ollama pull "$(grep -oP '^OLLAMA_CHAT_MODEL=\K.*' .env)"
ollama pull "$(grep -oP '^OLLAMA_EMBED_MODEL=\K.*' .env)"
ollama list
```

`.env.example` uses `gemma3:4b` for chat and `nomic-embed-text` for embeddings. If your `.env`
names `qwen2.5:7b` instead, that model is a 4.7 GB download (ollama.com/library/qwen2.5). With
6 GB of VRAM, load one model at a time.

## 6. Optional: limit memory on a 6 GB GPU

Do this only if you see out-of-memory errors or `ollama ps` shows a model split across CPU and GPU:

```bash
sudo systemctl edit ollama.service
```

Add these lines, then save:

```ini
[Service]
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

## 7. Check it the way Hindsight calls it

Hindsight sends a non-streaming JSON chat request. This is the same request:

```bash
MODEL="$(grep -oP '^OLLAMA_CHAT_MODEL=\K.*' .env)"
curl -s http://localhost:11434/api/chat -d "{\"model\":\"$MODEL\",\"stream\":false,\"format\":\"json\",\"options\":{\"temperature\":0},\"messages\":[{\"role\":\"user\",\"content\":\"Reply with JSON {\\\"ok\\\": true}\"}]}"
ollama ps      # the PROCESSOR column should say 100% GPU
make doctor    # the ollama row shows the server version
```

Then start Hindsight (`make dev`), open an incident and press **Investigate this incident** on the
Brief tab. Or call the API, replacing `2` with any incident id from the Incidents tab:

```bash
curl -s -X POST http://localhost:8000/api/incidents/2/investigate \
  -H 'content-type: application/json' -d '{}' | python -m json.tool | grep -E '"(status|mode)"'
```

`"mode": "llm"` means the model wrote the claims. `"extractive"` means no model answered.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `llama-server binary not found` | A partial hand-installed copy is running instead of the package. See the `which -a ollama` check in step 3 |
| `bind: address already in use` | The service and a hand-started `ollama serve` are both running. Stop one: `sudo systemctl stop ollama` or `pkill -f 'ollama serve'` |
| `ollama ps` shows `100% CPU` | `nvidia-smi` must work (reboot after installing the driver); check `pacman -Q ollama-cuda`; read `journalctl -u ollama -b --no-pager \| tail -50` |
| Out of memory, or `CPU/GPU` split | Use the smaller chat model (`gemma3:4b`), set `OLLAMA_MAX_LOADED_MODELS=1` (step 6), close other GPU programs |
| Models pulled earlier are missing | The service reads `/var/lib/ollama`, not `~/.ollama`. Pull them again with the service running |
| Hindsight still says "no LLM provider answered" | The model name in `.env` must match `ollama list` exactly, and `OLLAMA_HOST` must be `http://localhost:11434`. Restart `make dev` after editing `.env` |
