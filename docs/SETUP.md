# Hindsight: Setup

**Supported platforms:** WSL2 Ubuntu 24.04 (Windows) or CachyOS/Arch.
- **On Windows, work inside the WSL filesystem** (`~/code/hindsight`), never under `/mnt/c` or `/mnt/d`: it is far slower, and Windows/WSL tool mixing corrupts virtual environments.
- **Docker on WSL:** Docker Desktop with WSL integration is recommended. Bootstrap detects a working `docker` and skips installing an engine.

## Quick start
```bash
make bootstrap    # installs or verifies tools; asks before sudo
cp .env.example .env && $EDITOR .env
make doctor
make setup        # after block B0 creates backend/, web/, collector/
```

## Toolchain
| Tool | Purpose |
|---|---|
| uv + Python 3.12 | Backend |
| Node ≥ 20 + pnpm | Web |
| Go ≥ 1.22 | Collector |
| Docker, kind, kubectl, skaffold | Target cluster |
| Ollama (optional) | Local LLM fallback. With 6 GB of VRAM, run one 7B model at 4-bit at a time. Arch Linux steps: [OLLAMA_SETUP.md](OLLAMA_SETUP.md) |
| Playwright Chromium | Installed after the backend declares the dependency |

## Keys (`.env`)
| Key | Where to get it |
|---|---|
| `GEMINI_API_KEY` | Google AI Studio. Free-tier limits change, so check the console |
| `GROQ_API_KEY` | Groq console |
| `GITHUB_TOKEN` | Fine-grained, read-only |
| `ALERT_WEBHOOK_URL` | Discord channel → Integrations → Webhooks |

## Target system
1. Fork `GoogleCloudPlatform/microservices-demo` and set `TARGET_REPO_URL`.
2. Run `make target-up`. This creates the kind cluster and deploys the fork with skaffold. The first build is slow, so pre-pull the images before the event.
3. The cluster needs at least 4 CPUs and 4–6 GB of RAM. Close other heavy applications.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `docker: permission denied` | Add yourself to the docker group and log in again, or enable Docker Desktop WSL integration |
| kind fails on WSL | Enable systemd in `/etc/wsl.conf` (`[boot] systemd=true`), then run `wsl --shutdown` |
| Ollama uses the CPU | Check that `nvidia-smi` works inside WSL/Linux; reinstall the CUDA-enabled Ollama |
| Ollama: `llama-server binary not found` | The install is incomplete (a partial download). On Arch, use the packaged `ollama-cuda` and remove hand-installed copies: [OLLAMA_SETUP.md](OLLAMA_SETUP.md) |
