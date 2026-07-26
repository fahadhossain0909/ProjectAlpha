# ProjectAlpha - AI Trading System with CI/CD

> An AI agents-based trading project with comprehensive CI/CD pipelines

## 📋 Table of Contents

1. [Quick Start](#quick-start)
2. [GitHub Actions Workflows](#github-actions-workflows)
3. [Environment Configuration](#environment-configuration)
4. [GitHub Secrets Setup](#github-secrets-setup)
5. [Deployment](#deployment)
6. [Troubleshooting](#troubleshooting)

---

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.9+
- Redis, ClickHouse, Neo4j (provided via Docker Compose)
- GitHub Account with repository access

### Local Development Setup

```bash
# 1. Clone the repository
git clone https://github.com/fahadhossain0909/ProjectAlpha.git
cd ProjectAlpha

# 2. Start infrastructure
docker compose up -d

# 3. Create .env file
cp .env.example .env

# 4. Install dependencies
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 5. Run tests
PYTHONPATH=. pytest -v

# 6. Run the application
python3 run_paper_trading.py
```

---

## 🔄 GitHub Actions Workflows

### CI Pipeline (`.github/workflows/ci.yml`)

Automatically runs on every push and pull request to `main`, `master`, or `develop`.

#### Jobs:

1. **Linting & Code Quality** (`lint`)
   - Black formatter check
   - isort import checker
   - Flake8 linter
   - Status: `continue-on-error: true` (warnings don't block)

2. **Unit Tests** (`test`)
   - Runs on Python 3.9, 3.10, 3.11, 3.12
   - Test coverage reporting
   - Codecov integration
   - Creates `.env` from GitHub Secrets automatically

3. **Security Scan** (`security`)
   - Bandit vulnerability scanner
   - Safety dependency checker
   - Artifacts uploaded for review

4. **Docker Validation** (`docker-validate`)
   - Validates `docker-compose.yml`
   - Builds Docker image without pushing
   - Uses GitHub Actions cache for speed

5. **Results Summary** (`results`)
   - Aggregates all job statuses
   - Comments on PRs with results table
   - Fails if any job fails

#### Triggers:

```yaml
on:
  push:
    branches: [main, master, develop]
  pull_request:
    branches: [main, master, develop]
```

#### Check CI Status:

View all runs: https://github.com/fahadhossain0909/ProjectAlpha/actions

---

### CD Pipeline (`.github/workflows/cd.yml`)

Automatically runs on push to `main`/`master` and on version tags.

#### Jobs:

1. **Publish Docker Image** (`publish-image`)
   - Builds Docker image
   - Pushes to GitHub Container Registry (GHCR)
   - Creates `.env` from GitHub Secrets
   - Tags: `branch`, `tag`, `sha`, `latest`

2. **Deploy to Server** (`deploy`)
   - Requires `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`
   - Automatically creates `.env` on server
   - Pulls latest Docker image
   - Runs `docker compose up -d`

#### Triggers:

```yaml
on:
  push:
    branches: [main, master]
    tags: ['v*']
  workflow_dispatch:  # Manual trigger
```

#### Auto .env Creation:

The CD workflow automatically creates `.env` file on the deployment server using GitHub Secrets:

```bash
cat > .env << 'EOF'
DEPLOY_HOST=${{ secrets.DEPLOY_HOST }}
DEPLOY_USER=${{ secrets.DEPLOY_USER }}
DATABASE_URL=${{ secrets.DATABASE_URL }}
API_KEY=${{ secrets.API_KEY }}
SECRET_KEY=${{ secrets.SECRET_KEY }}
DEBUG=${{ secrets.DEBUG || false }}
EOF
```

---

## 🔐 Environment Configuration

### .env File Structure

See `.env.example` for all available variables. Key sections:

```env
# Application
ENVIRONMENT=production
DEBUG=false

# Database
DATABASE_URL=postgresql://user:password@db:5432/projectalpha

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Binance Trading
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here
BINANCE_TESTNET=true
```

### .gitignore

The `.env` file is in `.gitignore` and should **never** be committed:

```bash
# Verify .env is not tracked
git check-ignore .env  # Should return .env
```

---

## 🔑 GitHub Secrets Setup

### Required Secrets for CD Pipeline

#### 1. **DEPLOY_HOST** (Required for CD)
- **What**: Deployment server IP or hostname
- **Example**: `192.168.1.100` or `deploy.example.com`
- **Set**: Settings > Secrets and variables > Actions > New repository secret

#### 2. **DEPLOY_USER** (Required for CD)
- **What**: SSH username on deployment server
- **Example**: `ubuntu` or `deploy`

#### 3. **DEPLOY_SSH_KEY** (Required for CD)
- **What**: Private SSH key for authentication
- **How to generate**:
  ```bash
  ssh-keygen -t rsa -b 4096 -f deploy_key -N ""
  cat deploy_key  # Copy this entire content
  ```
- **Add to server**:
  ```bash
  ssh-copy-id -i deploy_key.pub user@host
  # OR manually:
  cat deploy_key.pub >> ~/.ssh/authorized_keys
  ```

#### 4. **DATABASE_URL** (Optional for CI/CD)
- **What**: Database connection string
- **Example**: `postgresql://user:pass@localhost:5432/projectalpha`

#### 5. **API_KEY** (Optional for CI/CD)
- **What**: Third-party API authentication key

#### 6. **SECRET_KEY** (Optional for CI/CD)
- **What**: Application secret key (Django, Flask, etc.)
- **Generate**:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(50))"
  ```

#### 7. **DEBUG** (Optional for CI/CD)
- **What**: Debug mode flag
- **Values**: `true` or `false`

### How to Add GitHub Secrets

1. Go to: https://github.com/fahadhossain0909/ProjectAlpha/settings/secrets/actions
2. Click **"New repository secret"**
3. Enter **Name**: `DEPLOY_HOST`
4. Enter **Secret**: Your value
5. Click **"Add secret"**
6. Repeat for each secret

### Verify Secrets Are Set

```bash
# In a workflow, GitHub shows masked values:
# *** = secret is set
# (empty) = secret not set
```

---

## 🚢 Deployment

### Automatic Deployment (CD Pipeline)

1. **Push to main/master**:
   ```bash
   git push origin main
   ```
   - Docker image builds and pushes
   - Deployment triggers automatically (if secrets are set)

2. **Deploy with tags**:
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```
   - Same flow, plus image tagged as `v1.0.0`

3. **Manual trigger**:
   - Go to Actions tab
   - Select "CD" workflow
   - Click "Run workflow" button

### Manual Deployment Steps

If you want to deploy without GitHub Actions:

```bash
# 1. SSH into server
ssh deploy@your-server

# 2. Navigate to app directory
cd ~/aitos

# 3. Clone or pull repo
if [ ! -d .git ]; then
  git clone https://github.com/fahadhossain0909/ProjectAlpha.git .
else
  git pull origin main
fi

# 4. Create .env file
cat > .env << EOF
DATABASE_URL=...
API_KEY=...
EOF

# 5. Run with Docker Compose
docker compose pull
docker compose up -d --build
```

---

## 📊 Monitoring & Logs

### Check Workflow Status

- **CI Runs**: https://github.com/fahadhossain0909/ProjectAlpha/actions/workflows/ci.yml
- **CD Runs**: https://github.com/fahadhossain0909/ProjectAlpha/actions/workflows/cd.yml

### View Workflow Logs

1. Click the workflow run
2. Click a failed job
3. Expand any step to see full logs

### Common Log Locations

```bash
# Docker logs
docker logs <container-id>

# Application logs
tail -f logs/app.log

# System logs (if using systemd)
journalctl -u aitos-live.service -f
```

---

## 🔧 Troubleshooting

### "secrets.DEPLOY_HOST: Secret not found"

**Problem**: Workflow says secret doesn't exist

**Solution**:
1. Go to Settings > Secrets and variables > Actions
2. Verify `DEPLOY_HOST` is listed
3. Trigger workflow again: Actions > CD > Run workflow

### SSH Deployment Fails

**Problem**: "Permission denied (publickey)"

**Solution**:
```bash
# On deployment server, check authorized keys:
cat ~/.ssh/authorized_keys | grep "$(cat deploy_key.pub)"

# If not there, add it:
cat deploy_key.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# Test locally:
ssh -i deploy_key deploy-user@your-server echo "Success"
```

### Docker Image Push Fails

**Problem**: "authentication required"

**Solution**:
- GitHub Actions uses `GITHUB_TOKEN` automatically
- No manual setup needed
- Verify repository is accessible

### Tests Fail in CI

**Problem**: "pytest: command not found" or test failures

**Solution**:
1. Check requirements.txt has pytest: `grep pytest requirements.txt`
2. View CI logs for detailed error
3. Run locally: `PYTHONPATH=. pytest -v`

### .env Not Created in CI

**Problem**: Tests fail saying `.env` doesn't exist

**Solution**:
- CI creates `.env` automatically from secrets
- If secrets are empty, `.env` will have empty values
- Check GitHub Secrets are set correctly

### Docker Compose Validation Fails

**Problem**: "invalid docker-compose.yml"

**Solution**:
```bash
# Validate locally:
docker compose config

# Check for syntax errors:
cat docker-compose.yml | grep -n "  "  # Look for indent issues
```

---

## 📚 Additional Resources

- **GitHub Secrets Documentation**: https://docs.github.com/en/actions/security-guides/encrypted-secrets
- **GitHub Actions**: https://docs.github.com/en/actions
- **Docker Compose**: https://docs.docker.com/compose/
- **Setup Guide**: See [SETUP_GUIDE.md](SETUP_GUIDE.md)

---

## ✅ Checklist: Before First Deployment

- [ ] GitHub Secrets added (at minimum: DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY)
- [ ] SSH key deployed to server
- [ ] Deployment server has Docker & Docker Compose installed
- [ ] `.env` is in `.gitignore`
- [ ] CI workflow runs green on main branch
- [ ] docker-compose.yml is valid (`docker compose config`)
- [ ] Test push to main branch to verify CD pipeline

---

## 📝 CI/CD Workflow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    Push Code to GitHub                      │
└────────────────────────┬────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   ┌────▼────┐      ┌────▼────┐    ┌────▼────┐
   │ Linting │      │ Testing │    │Security │
   └────┬────┘      └────┬────┘    └────┬────┘
        │                │              │
        └────────────────┼──────────────┘
                         │
                    ┌────▼────┐
                    │ Docker  │
                    │ Validate│
                    └────┬────┘
                         │
                    ┌────▼──────────┐
                    │ All Pass? ✅  │
                    └────┬──────────┘
                         │
           ┌─────────────┬────────────────┐
           │ main/master │ other branch  │
           │   pushed?   │ (CI stops)    │
           └─────┬───────┘               │
                 │                       │
            ┌────▼────────────────────┐  │
            │  CD Pipeline Triggers   │  │
            │  1. Build Docker Image  │  │
            │  2. Create .env on      │  │
            │     deployment server   │  │
            │  3. Deploy via SSH      │  │
            └────┬────────────────────┘  │
                 │                       │
            ┌────▼─────────────────────┐ │
            │ Deployment Complete ✅   │ │
            └──────────────────────────┘ │
                                         │
                                    ✋ Stop
```

---

**Last Updated**: 2026-07-23  
**Status**: CI/CD Setup Complete ✅
