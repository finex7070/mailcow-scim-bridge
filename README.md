# SCIM Bridge Docker

A lightweight SCIM 2.0 bridge built with FastAPI and Docker.

**Created for Authentik ➔ Mailcow mailbox provisioning.**  
Created by Jan Hüls "finex7070" StickyStoneStudio GmbH 🚀
Originally created by: William Grzybowski "MacJediWizard" MacJediWizard Consulting, Inc. 🤝

---

## Features

- 🔐 Secure SCIM 2.0 Server using FastAPI
- 📬 Automatic mailbox provisioning in Mailcow
- 📈 Built-in `/metrics` endpoint for Prometheus / Grafana monitoring
- 🐳 Dockerized for fast, reproducible deployment
- ✅ SCIM standard support: `GET`, `POST`, `PUT`, `DELETE`
- 🔄 Sync-ready with Authentik SCIM provider

---

## Planned Features

- 🧠 Add SCIM Group to mailbox tags and create and alias which sends to all members (`groups`)

---

## Getting Started

### 1. Clone and Deploy

```bash
git clone https://github.com/finex7070/mailcow-scim-bridge.git
cd mailcow-scim-bridge
# Edit the docker-compose.yml file
docker compose up -d
```

---

## Environment Variables

| Variable                  | Description                                     | Default                          |
|---------------------------|-------------------------------------------------|----------------------------------|
| `SCIM_TOKEN`              | Bearer token used to authenticate SCIM requests | changeme                         |
| `MAILCOW_API_URL`         | Base URL of the Mailcow Admin API               | https://mail.example.com/api/v1/ |
| `MAILCOW_API_KEY`         | Mailcow API Key with read + write access        | changeme                         |
| `SKIP_VERIFY_CERTIFICATE` | Skip ssl certificate verification               | false                            |
| `ALLOW_DELETE`            | Allow DELETE operations                         | true                             |
| `MAILCOW_DELETE_MAILBOX`  | Delete mailbox on DELETE                        | false                            |

---

## API Endpoints

| Path                      | Method(s)                      | Description                                |
|---------------------------|--------------------------------|--------------------------------------------|
| `/healthz`                | `GET`                          | Healthcheck endpoint                       |
| `/metrics`                | `GET`                          | Prometheus metrics (for Grafana)           |
| `/ServiceProviderConfig`  | `GET`                          | SCIM metadata                              |
| `/Users`                  | `GET`, `POST`, `PUT`, `DELETE` | Sync and provision Mailcow users/mailboxes |
| `/Groups`                 | `GET`, `POST`, `PUT`, `DELETE` | Only placeholder for now                   |

---

## How It Works

1. Authentik SCIM sends a sync request to the FastAPI SCIM server.
2. The server authenticates via the provided SCIM bearer token.
3. SCIM `/Users` → Mailcow mailbox creation (with authsource generic-oidc).

---

## Requirements

- 🐳 Docker + Docker Compose
- 🧠 Basic knowledge of SCIM and Mailcow API
- 🔐 A valid Mailcow API key with read + write access
- ⚙️ Authentik instance or SCIM-compatible identity provider

---

## Monitoring

Export metrics to Prometheus:

```text
GET /metrics
```

Sample output:
```text
# HELP users_created SCIM metric for users_created
# TYPE users_created counter
users_created 10
# HELP users_updated SCIM metric for users_updated
# TYPE users_updated counter
users_updated 10
# HELP users_deleted SCIM metric for users_deleted
# TYPE users_deleted counter
users_deleted 0
```

---

## License

MIT License

---

> Made with ❤️ by [StickyStoneStudio GmbH](https://www.stickystonestudio.com) and [MacJediWizard Consulting, Inc.](https://macjediwizard.com)