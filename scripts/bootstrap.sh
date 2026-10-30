#!/usr/bin/env bash
# Installs or verifies the toolchain. Idempotent. Asks before every sudo action.
# Supported: Ubuntu/Debian (incl. WSL2) and Arch/CachyOS.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIND_VERSION="${KIND_VERSION:-v0.23.0}"   # known-good; newer is fine

info(){ printf '\033[36m[info]\033[0m %s\n' "$*"; }
ok(){   printf '\033[32m[ ok ]\033[0m %s\n' "$*"; }
warn(){ printf '\033[33m[warn]\033[0m %s\n' "$*"; }
have(){ command -v "$1" >/dev/null 2>&1; }
confirm(){ read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[Yy]$ ]]; }
ver_ge(){ [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }  # ver_ge have need

if have pacman; then OS=arch; elif have apt-get; then OS=debian; else OS=unknown; fi
grep -qi microsoft /proc/version 2>/dev/null && WSL=1 || WSL=0
info "platform: $OS (wsl=$WSL)"
case "$ROOT" in /mnt/*) warn "repo is under $ROOT. On WSL, move it to ~/code/hindsight (much faster, avoids corruption).";; esac

# ---------- base packages ----------
if [[ $OS == arch ]]; then
  pkgs=(base-devel git make curl jq unzip go python uv nodejs npm kubectl)
  missing=(); for p in "${pkgs[@]}"; do pacman -Qi "$p" >/dev/null 2>&1 || missing+=("$p"); done
  if ((${#missing[@]})); then
    if confirm "Install with pacman: ${missing[*]}?"; then sudo pacman -S --needed "${missing[@]}"; else warn "skipped: ${missing[*]}"; fi
  else ok "base packages present"; fi
elif [[ $OS == debian ]]; then
  # zstd: the Ollama installer needs it to unpack its release archive
  pkgs=(build-essential git make curl jq unzip zstd ca-certificates golang-go)
  missing=(); for p in "${pkgs[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
  if ((${#missing[@]})); then
    if confirm "Install with apt: ${missing[*]}?"; then sudo apt-get update && sudo apt-get install -y "${missing[@]}"; else warn "skipped: ${missing[*]}"; fi
  else ok "base packages present"; fi
fi

# ---------- uv ----------
if ! have uv && confirm "Install uv (official installer)?"; then curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; fi
have uv && ok "uv $(uv --version | awk '{print $2}')"

# ---------- node >= 20 + pnpm ----------
need_node=1
if have node; then nv="$(node -v | tr -d v)"; ver_ge "$nv" "20.0.0" && need_node=0; fi
if (( need_node )); then
  if [[ $OS == debian ]] && confirm "Install Node.js 22 LTS from NodeSource?"; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs
  else warn "Node >= 20 required (install via your package manager or nvm)"; fi
fi
# Node 25+ no longer ships Corepack; fall back to installing pnpm with npm (no sudo)
if have node && ! have pnpm; then corepack enable 2>/dev/null || npm install --global --prefix "$HOME/.local" pnpm@12 || true; export PATH="$HOME/.local/bin:$PATH"; fi
have pnpm && ok "pnpm $(pnpm -v)"

# ---------- go >= 1.22 ----------
if have go; then gv="$(go env GOVERSION | tr -d go)"; if ver_ge "$gv" "1.22"; then ok "go $gv"; else warn "go $gv < 1.22: install a newer Go from go.dev/dl"; fi; fi

# ---------- docker (detect first; Docker Desktop on WSL counts) ----------
if have docker && docker info >/dev/null 2>&1; then
  ok "docker reachable ($(docker version --format '{{.Server.Version}}' 2>/dev/null || echo ?)); skipping engine install"
elif (( WSL )); then
  warn "No working docker. Recommended on WSL: Docker Desktop with WSL integration enabled for this distro."
  warn "Alternative: enable systemd in /etc/wsl.conf, then install docker.io."
  if [[ $OS == debian ]] && confirm "Install docker.io + compose plugin inside WSL instead?"; then
    sudo apt-get install -y docker.io docker-compose-v2 && sudo usermod -aG docker "$USER" && warn "log out/in (or wsl --shutdown) for group change"
  fi
else
  if confirm "Install Docker engine with the package manager?"; then
    if [[ $OS == arch ]]; then sudo pacman -S --needed docker docker-compose docker-buildx
    else sudo apt-get install -y docker.io docker-compose-v2; fi
    sudo systemctl enable --now docker && sudo usermod -aG docker "$USER" && warn "log out/in for docker group"
  fi
fi

# ---------- kubectl ----------
if ! have kubectl && [[ $OS == debian ]] && confirm "Install kubectl (official binary)?"; then
  v="$(curl -fsSL https://dl.k8s.io/release/stable.txt)"
  curl -fsSLo /tmp/kubectl "https://dl.k8s.io/release/${v}/bin/linux/amd64/kubectl" && sudo install -m 0755 /tmp/kubectl /usr/local/bin/kubectl
fi
have kubectl && ok "kubectl present"

# ---------- kind ----------
if ! have kind && have go; then info "go install kind@$KIND_VERSION"; go install "sigs.k8s.io/kind@$KIND_VERSION"; fi
have go && export PATH="$(go env GOPATH)/bin:$PATH"
if have kind; then ok "kind present"; else warn "kind missing (needs Go; then add \$(go env GOPATH)/bin to PATH)"; fi

# ---------- skaffold ----------
if ! have skaffold && confirm "Install skaffold to ~/.local/bin?"; then
  mkdir -p "$HOME/.local/bin"
  curl -fsSLo "$HOME/.local/bin/skaffold" https://storage.googleapis.com/skaffold/releases/latest/skaffold-linux-amd64 && chmod +x "$HOME/.local/bin/skaffold"
fi
have skaffold && ok "skaffold present"

# ---------- ollama (optional) ----------
if ! have ollama && confirm "Install Ollama (optional local LLM fallback)?"; then
  if [[ $OS == arch ]]; then sudo pacman -S --needed "$(have nvidia-smi && echo ollama-cuda || echo ollama)"
  else curl -fsSL https://ollama.com/install.sh | sh || warn "Ollama install failed; continuing without local models"; fi
fi
if have ollama && curl -fsS "${OLLAMA_HOST:-http://localhost:11434}/api/version" >/dev/null 2>&1 && confirm "Pull local models from .env (several GB)?"; then
  set -a; source "$ROOT/.env" 2>/dev/null || source "$ROOT/.env.example"; set +a
  ollama pull "$OLLAMA_EMBED_MODEL"; ollama pull "$OLLAMA_CHAT_MODEL"
fi

# ---------- project deps (only once B0 created them) ----------
if [[ -f "$ROOT/backend/pyproject.toml" ]]; then
  (cd "$ROOT/backend" && uv sync)
  if (cd "$ROOT/backend" && uv run python -c "import playwright" >/dev/null 2>&1); then
    if [[ $OS == debian ]]; then (cd "$ROOT/backend" && uv run playwright install --with-deps chromium)
    else (cd "$ROOT/backend" && uv run playwright install chromium); fi
  else info "playwright not yet a backend dependency; skipping browser install"; fi
fi
[[ -f "$ROOT/.env" ]] || { cp "$ROOT/.env.example" "$ROOT/.env"; info "created .env; add your keys"; }
ok "bootstrap done; run: make doctor"
