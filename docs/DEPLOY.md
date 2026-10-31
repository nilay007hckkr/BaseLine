# Deploying Hindsight

With PS 04 (Incidents) and PS 08 (Tests) frozen, a deployment is small: **one Python process per
instance** (FastAPI, which also serves the built web app), a **SQLite file**, and the **git
clones** it reads. No Kubernetes cluster, no log collector, no browser automation, and Neo4j is
optional (SQLite answers the same queries).

## 1. Where to run it

| Host | Fit | Why |
|---|---|---|
| **A small VM** (e.g. Google Compute Engine, 2 vCPU / 4 GB, 30 GB disk) | **Recommended** | Runs exactly like a laptop: `git` is there, the disk keeps the database and clones, and repository imports (minutes, in a background thread) are never cut off. |
| **Render** web service | Only on a paid instance with a persistent disk | Render's docs: a free web service "spins down" after 15 minutes without traffic, has an "ephemeral filesystem" (wiped on redeploy, restart or spin-down), and cannot attach a persistent disk ([render.com/docs/free](https://render.com/docs/free)). The repository has no Dockerfile yet. |
| **Vercel** | Not for the API | Functions stop after 300 s on the Hobby plan ([vercel.com/docs/functions/limitations](https://vercel.com/docs/functions/limitations)); imports take longer and need a disk for SQLite and clones. Vercel could host only the static web build, but the API already serves it. |

The rest of this page is the VM route.

## 2. Model choice in the cloud

A small VM has no GPU, so a local Ollama model would be very slow there. Use a cloud provider:
put `GEMINI_API_KEY` + `GEMINI_MODEL` and/or `GROQ_API_KEY` + `GROQ_MODEL` in the VM's `.env`.
Leave `OLLAMA_CHAT_MODEL` empty so Ollama is not tried. The offline (Ollama) story stays on the
laptop. Keys live only in `.env` on the VM, never in the repository or in the dashboard.

## 3. VM, step by step (Google Compute Engine)

You need a Google Cloud project with billing, the `gcloud` CLI, and ideally a domain name you can
point at the VM (HTTPS and the password both need it). Replace `HOST` below with that name and
`ZONE` with a zone near you (for example `asia-south1-a`).

### 3.1 Create the VM and open the web ports
```bash
# pick the Ubuntu 24.04 LTS (amd64) image family from this list, then use it below
gcloud compute images list --filter="family~ubuntu-2404"

gcloud compute instances create hindsight --zone=ZONE \
  --machine-type=e2-medium --boot-disk-size=30GB --tags=web \
  --image-project=ubuntu-os-cloud --image-family=<family from the list>

# HTTPS for the dashboard (443), the optional workspace (8443), and 80 for the certificate check
gcloud compute firewall-rules create allow-hindsight-web --network=default \
  --direction=INGRESS --action=allow --rules=tcp:80,tcp:443,tcp:8443 \
  --source-ranges=0.0.0.0/0 --target-tags=web
```
Point your domain's A record at the VM's external IP (shown by `gcloud compute instances list`).

### 3.2 Code, tools and dependencies
```bash
gcloud compute ssh hindsight --zone=ZONE
git clone https://github.com/nilay007hckkr/BaseLine.git hindsight && cd hindsight
make bootstrap    # git, uv, Node 22 and pnpm via apt/official installers (asks before installing)
make setup        # uv sync + pnpm install
```
`make bootstrap` also checks Docker, kind and Go for the frozen live demo; you can skip those.

### 3.3 Settings
```bash
cp .env.example .env && chmod 600 .env
nano .env
```
Set at least:
```bash
LLM_PROVIDER_ORDER=gemini,groq
GEMINI_API_KEY=...            # and GEMINI_MODEL=..., or the GROQ_* pair
OLLAMA_CHAT_MODEL=            # empty: no local model on the VM
GRAPH_BACKEND=sqlite          # no Neo4j
GITHUB_TOKEN=...              # read-only token: higher GitHub API limits for imports
```

### 3.4 Data
Import Online Boutique on the VM (the measured numbers on Home and Proof come from
`eval/results/`, which is in the repository):
```bash
make ingest       # clones the repository and reads its history; takes a while
```
Copying `data/` from the laptop is not recommended: the database stores the clone's absolute path,
which differs on the VM.

### 3.5 Build the web app
```bash
cd web && pnpm build && cd ..
```

### 3.6 Run as services
The demo runs on `127.0.0.1:9000`; the optional workspace (connect your own repository) on
`127.0.0.1:9080`. Caddy (next step) is the only thing facing the internet. Replace `USER` with
your VM user name (`whoami`), and check the path to `uv` with `command -v uv` (the official
installer puts it in `~/.local/bin`).

`/etc/systemd/system/hindsight-demo.service`:
```ini
[Unit]
Description=Hindsight demo dashboard
After=network-online.target

[Service]
User=USER
WorkingDirectory=/home/USER/hindsight/backend
Environment=CORS_ORIGINS=https://HOST:8443
ExecStart=/home/USER/.local/bin/uv run uvicorn hindsight.api.app:app --host 127.0.0.1 --port 9000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/hindsight-workspace.service` (optional):
```ini
[Unit]
Description=Hindsight workspace (connect your own repository)
After=network-online.target

[Service]
User=USER
WorkingDirectory=/home/USER/hindsight/backend
Environment=HINDSIGHT_MODE=workspace
Environment=DATA_DIR=./data/workspace
Environment=DB_PATH=./data/workspace/empty/hindsight.db
Environment=RESULTS_DIR=./data/workspace/empty/results
Environment=GRAPH_BACKEND=sqlite
# the workspace's tour finds the demo at the same host name on this port
Environment=DEMO_PORT=443
ExecStart=/home/USER/.local/bin/uv run uvicorn hindsight.api.app:app --host 127.0.0.1 --port 9080
Restart=on-failure

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now hindsight-demo hindsight-workspace
curl -s http://127.0.0.1:9000/api/health    # prints {"status":"ok"}
```
The backend reads `.env` from the repository root by itself; relative paths such as
`./data/workspace` are resolved from the repository root too.

### 3.7 HTTPS and a password (Caddy)
Install Caddy from its official repository ([caddyserver.com/docs/install](https://caddyserver.com/docs/install)):
```bash
sudo apt install --yes debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg
sudo chmod o+r /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
caddy hash-password          # type a password; copy the hash it prints
```
`/etc/caddy/Caddyfile` (`basic_auth` is the directive name since Caddy 2.8,
[docs](https://caddyserver.com/docs/caddyfile/directives/basic_auth)):
```
HOST {
	basic_auth {
		judges <hash from caddy hash-password>
	}
	reverse_proxy 127.0.0.1:9000
}

HOST:8443 {
	basic_auth {
		judges <hash from caddy hash-password>
	}
	reverse_proxy 127.0.0.1:9080
}
```
```bash
sudo systemctl reload caddy
```
Caddy fetches the HTTPS certificates itself once the domain points at the VM. Open
`https://HOST` (demo) and `https://HOST:8443` (workspace).

Known limit: the workspace's tour calls the demo's API from the browser without the password, so
its live examples fail while the demo is password-protected. The tour still plays and everything
else works. Deploy only the demo if that matters, or drop the demo's `basic_auth` block.

### 3.8 Updating
```bash
cd ~/hindsight && git pull && make setup
cd web && pnpm build && cd ..
sudo systemctl restart hindsight-demo hindsight-workspace
```
The API does not reload code by itself: restart after every update.

## 4. What does not work in the cloud (frozen features)
- **Incidents** shows the incidents recorded in the database; there is no live cluster, so no new
  ones appear. Investigate still works with a cloud model.
- **Tests**: **Run** needs the demo shop and Playwright browsers, which are not deployed, so runs
  fail. The recorded results are still shown.
- The **workspace** instance already hides both pages.

## 5. Security checklist
- [ ] A password in front of everything (Caddy `basic_auth`), over HTTPS only (basic auth is not
      safe over plain HTTP).
- [ ] `.env` only on the VM, `chmod 600`, never committed.
- [ ] Spending limits set at the model provider: `LLM_DAILY_REQUEST_CAP` in `.env` is **not
      enforced** by the code yet.
- [ ] Only ports 80, 443 and 8443 open to the internet (plus SSH through `gcloud compute ssh`);
      uvicorn listens on `127.0.0.1` only.
- [ ] Anyone with the password can start repository imports on the workspace (CPU and disk);
      share it only with the people who need it.
- [ ] `sudo apt update && sudo apt upgrade` now and then.
