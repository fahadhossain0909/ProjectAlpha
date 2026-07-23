# GitHub Secrets & Environment Configuration Guide

## Overview
এই গাইডটি ProjectAlpha রিপোজিটরিতে CI/CD পাইপলাইন সেটআপ করার জন্য প্রয়োজনীয় GitHub Secrets এবং Environments কনফিগার করার পদ্ধতি ব্যাখ্যা করে।

## Required GitHub Secrets

GitHub এ যেসব সিক্রেটস সেট করা প্রয়োজন তার তালিকা:

### 1. **DEPLOY_HOST**
- **Purpose**: ডিপ্লয়মেন্ট সার্ভারের হোস্টনেম বা IP এড্রেস
- **Example**: `192.168.1.100` বা `deploy.example.com`
- **Where to set**: Settings > Secrets and variables > Actions

### 2. **DEPLOY_USER**
- **Purpose**: সার্ভারে SSH লগইন এর জন্য ব্যবহারকারী
- **Example**: `deploy-user` বা `ubuntu`
- **Where to set**: Settings > Secrets and variables > Actions

### 3. **DEPLOY_SSH_KEY**
- **Purpose**: ডিপ্লয়মেন্ট সার্ভারে SSH কানেক্ট করার জন্য প্রাইভেট কী
- **How to generate**:
  ```bash
  ssh-keygen -t rsa -b 4096 -f deploy_key -N ""
  # যোগ করুন পাবলিক কী সার্ভারে:
  cat deploy_key.pub >> ~/.ssh/authorized_keys
  ```
- **Value**: `deploy_key` ফাইলের সম্পূর্ণ কন্টেন্ট (-----BEGIN PRIVATE KEY----- থেকে -----END PRIVATE KEY----- পর্যন্ত)
- **Where to set**: Settings > Secrets and variables > Actions

### 4. **DATABASE_URL** (Optional)
- **Purpose**: ডাটাবেস কানেকশন স্ট্রিং
- **Example**: `postgresql://user:password@localhost:5432/projectalpha`
- **Where to set**: Settings > Secrets and variables > Actions

### 5. **API_KEY** (Optional)
- **Purpose**: থার্ড-পার্টি API এর জন্য অথেন্টিকেশন কী
- **Where to set**: Settings > Secrets and variables > Actions

### 6. **SECRET_KEY** (Optional)
- **Purpose**: অ্যাপ্লিকেশন সিক্রেট কী (যেমন Django SECRET_KEY)
- **How to generate**:
  ```bash
  python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
  ```
- **Where to set**: Settings > Secrets and variables > Actions

### 7. **DEBUG** (Optional)
- **Purpose**: ডিবাগ মোড চালু/বন্দ করা
- **Values**: `true` বা `false`
- **Default**: `false`
- **Where to set**: Settings > Secrets and variables > Actions

## How to Set GitHub Secrets

### ধাপে ধাপে নির্দেশনা:

1. **রিপোজিটরিতে যান**:
   ```
   https://github.com/fahadhossain0909/ProjectAlpha
   ```

2. **Settings ট্যাবে ক্লিক করুন**:
   ```
   Repository > Settings
   ```

3. **Secrets and variables > Actions এ যান**:
   ```
   Left sidebar > Secrets and variables > Actions
   ```

4. **"New repository secret" বাটনে ক্লিক করুন**:
   ```
   সবুজ বাটন: New repository secret
   ```

5. **প্রতিটি সিক্রেট যোগ করুন**:
   ```
   Name: DEPLOY_HOST
   Secret: আপনার মান
   ```

6. **"Add secret" ক্লিক করুন**

## .env File Auto-Generation

### কীভাবে কাজ করে:

যখন ওয়ার্কফ্লো রান হয়, এটি স্বয়ংক্রিয়ভাবে `.env` ফাইল তৈরি করে:

```yaml
# CI পর্যায়ে (.env তৈরি হয় টেস্টিংয়ের জন্য)
- name: Create .env file from secrets for tests
  run: |
    cat > .env << EOF
    DATABASE_URL=${{ secrets.DATABASE_URL }}
    API_KEY=${{ secrets.API_KEY }}
    SECRET_KEY=${{ secrets.SECRET_KEY }}
    DEBUG=true
    TESTING=true
    EOF

# CD পর্যায়ে (সার্ভারে .env তৈরি হয়)
- name: Deploy to remote host over SSH
  script: |
    cat > .env << 'ENVEOF'
    DATABASE_URL=${{ secrets.DATABASE_URL }}
    ...
    ENVEOF
```

## Environment Setup (উন্নত)

আপনি বিভিন্ন পরিবেশের জন্য আলাদা সিক্রেটস সেট করতে পারেন:

### Production Environment তৈরি করুন:

1. **Settings > Environments এ যান**
2. **"New environment" ক্লিক করুন**
3. **নাম দিন**: `production`
4. **সিক্রেটস যোগ করুন** (শুধুমাত্র production এর জন্য)

### CD ওয়ার্কফ্লোতে ব্যবহার করুন:

```yaml
deploy:
  runs-on: ubuntu-latest
  environment: production
  needs: publish-image
```

## Docker & Docker Compose Setup

### .env ফাইল উদাহরণ:

```env
# Database
DATABASE_URL=postgresql://user:password@db:5432/projectalpha
DB_USER=projectalpha
DB_PASSWORD=your_secure_password

# API Configuration
API_KEY=your_api_key_here
SECRET_KEY=your_django_secret_key

# Application
DEBUG=false
TESTING=false
ENVIRONMENT=production

# Docker Compose
COMPOSE_PROJECT_NAME=projectalpha
```

### docker-compose.yml এ রেফারেন্স:

```yaml
services:
  web:
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - API_KEY=${API_KEY}
      - SECRET_KEY=${SECRET_KEY}
      - DEBUG=${DEBUG}
```

## CI/CD Workflow ফ্লো

```mermaid
graph TD
    A[Push Code] --> B[CI Workflow Triggers]
    B --> C[Linting]
    B --> D[Testing]
    B --> E[Security Scan]
    B --> F[Docker Build]
    C --> G{All Pass?}
    D --> G
    E --> G
    F --> G
    G -->|Yes| H[CD Workflow Triggers]
    G -->|No| I[Notify Failure]
    H --> J[Build & Push Image]
    J --> K[Deploy via SSH]
    K --> L[Create .env on Server]
    L --> M[Pull & Run Docker]
    M --> N{Deployment Success?}
    N -->|Yes| O[Deployment Complete ✅]
    N -->|No| P[Rollback/Alert ❌]
```

## ট্রাবলশুটিং

### সমস্যা: "secrets.DEPLOY_HOST: Secret not found"

**সমাধান**:
- সিক্রেট সঠিক নামে সেট করা আছে কি চেক করুন
- Repository settings > Secrets এ যান
- সিক্রেটটি যুক্ত করুন

### সমস্যা: SSH ডিপ্লয়মেন্ট ব্যর্থ হয়েছে

**সমাধান**:
```bash
# সার্ভারে public key যুক্ত করুন:
ssh-copy-id -i deploy_key.pub user@host

# বা ম্যানুয়ালি:
cat deploy_key.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

### সমস্যা: .env ফাইল নির্মিত হচ্ছে না

**সমাধান**:
- CI/CD ওয়ার্কফ্লো লগ চেক করুন
- সকল প্রয়োজনীয় সিক্রেটস যোগ করা আছে কি যাচাই করুন
- ফাইল পারমিশন চেক করুন: `chmod 600 .env`

## নিরাপত্তা সেরা অনুশীলন

✅ **করুন**:
- সিক্রেটস গিট রিপোজিটরিতে কখনও চেক করবেন না
- `.env` ফাইল `.gitignore` এ যোগ করুন
- নিয়মিত সিক্রেটস রোটেট করুন
- শক্তিশালী পাসওয়ার্ড ব্যবহার করুন

❌ **করবেন না**:
- সিক্রেটস লগ মেসেজে প্রিন্ট করবেন না
- পাবলিক রিপোজিটরিতে সংবেদনশীল তথ্য রাখবেন না
- অপ্রয়োজনীয় অ্যাক্সেস দেবেন না

## সংহতকরণ চেকলিস্ট

- [ ] GitHub Secrets সেটআপ করেছেন (DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY)
- [ ] ডাটাবেস সিক্রেটস যোগ করেছেন (ঐচ্ছিক)
- [ ] API কী এবং সিক্রেট কী যোগ করেছেন (ঐচ্ছিক)
- [ ] ডিপ্লয়মেন্ট সার্ভারে public key ইনস্টল করেছেন
- [ ] CI ওয়ার্কফ্লো টেস্ট করেছেন
- [ ] CD ওয়ার্কফ্লো টেস্ট করেছেন
- [ ] `.env` ফাইল `.gitignore` এ আছে
- [ ] docker-compose.yml সঠিকভাবে কনফিগার করেছেন

## উপযোগী লিংক

- [GitHub Secrets ডকুমেন্টেশন](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
- [GitHub Environments](https://docs.github.com/en/actions/deployment/targeting-different-environments)
- [Docker Compose Documentation](https://docs.docker.com/compose/)
