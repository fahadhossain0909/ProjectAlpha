# Deploying AITOS from GitHub to a VPS via Docker

This assumes you've pushed this project to a GitHub repo and have a fresh
VPS (GCP, Oracle Cloud, or anywhere else) with SSH access.

## 1. Install Docker on the VPS

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker   # or log out/in so the group change takes effect
docker compose version   # confirms the Compose plugin is present
```

(Ubuntu 22.04/24.04 tested; Docker's convenience script supports Debian/
Ubuntu/most distros. On Oracle Cloud's ARM (Ampere A1) instances this
installs the arm64 build automatically -- no special steps needed.)

## 2. Clone the repo

```bash
git clone https://github.com/<your-username>/<your-repo>.git aitos
cd aitos
```

## 3. Configure

```bash
cp .env.example .env
nano .env   # or vim/whatever -- see below for what to change
```

For **paper trading**, the defaults in `.env.example` are fine as-is --
you don't need Binance credentials for anything except live trading.

For **live trading**, you'll additionally need:
```
BINANCE_API_KEY=<your key>
BINANCE_API_SECRET=<your secret>
BINANCE_TESTNET=true   # leave true until you deliberately mean mainnet
```

Do **not** commit `.env` to git -- it's already covered by a typical
`.gitignore`; double-check yours excludes it before pushing anything.

## 4. Start paper trading

```bash
docker compose up -d --build
```

This builds the app image from `Dockerfile`, starts Redis/ClickHouse/
Neo4j, waits for their healthchecks, then starts `aitos-paper`
automatically (it has no profile gate, unlike `aitos-live`).

Check it's running:

```bash
docker compose ps
docker compose logs -f aitos-paper
curl http://localhost:8090/health
```

## 5. Live trading (only when you actually mean it)

Live trading is **not** started by `docker compose up -d` -- it's behind
a Compose profile and needs an interactive terminal for its startup
confirmation (`aitos/live_trading.py`'s typed approval phrase). Run it
in the foreground, attached:

```bash
docker compose --profile live run --rm aitos-live
```

Read `run_live_trading.py`'s module docstring and the README's "Live
trading" section before this step -- it places real orders.

## 6. Updating after a code change

```bash
git pull
docker compose up -d --build   # rebuilds only what changed
```

## 7. Stopping / cleaning up

```bash
docker compose down            # stops containers, keeps volumes (your data)
docker compose down -v         # also deletes volumes -- irreversible
```

## Notes

- **Firewall**: only expose ports you actually need externally. `8090`/
  `8091` (health/metrics) are useful to check from your own machine but
  don't need to be public -- consider binding them to `127.0.0.1` in
  `docker-compose.yml` (change `"8090:8090"` to `"127.0.0.1:8090:8090"`)
  and using SSH port-forwarding (`ssh -L 8090:localhost:8090 your-vps`)
  instead, especially once you're live trading.
- **Resource sizing**: see the README's hardware guidance -- 4 vCPU/8GB
  RAM comfortably runs the full stack (Redis+ClickHouse+Neo4j+app);
  ClickHouse and Neo4j are the two services that actually want real
  memory, not the Python app itself.
- **ARM (Oracle Cloud Ampere)**: the Dockerfile includes `build-essential`
  specifically because `shap` doesn't always have a prebuilt `aarch64`
  wheel and may compile from source on first build -- expect the initial
  `docker compose build` to take noticeably longer on ARM than on x86.
- **Systemd instead of `restart: unless-stopped`**: if you'd rather manage
  this with systemd directly (journald logging, `systemctl restart`,
  etc.) instead of Compose's own restart policy, see
  `deploy/aitos-paper.service` / `deploy/aitos-live.service` -- those
  assume a venv rather than Docker, so adapt `ExecStart` to
  `docker compose up` / `docker compose --profile live run --rm
  aitos-live` if you want systemd to be what's supervising Compose
  itself, rather than running two different mechanisms.
