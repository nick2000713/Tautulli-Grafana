# Tautulli → PostgreSQL Sync + Grafana Dashboard

Syncs your Tautulli Plex watch history from SQLite into PostgreSQL so you can visualize it with Grafana. Runs automatically on a daily cron schedule inside Docker.

---

## Preview

![Dashboard Overview](preview-overview.png)

![Charts Detail](preview-charts.png)

---

## How It Works

The container mounts your Tautulli SQLite database (read-only), connects to your existing PostgreSQL instance, and syncs all watch history into normalized tables. On first run it imports everything; every run after that only fetches new rows using the last synced row ID.

```
Tautulli SQLite DB  →  [this container]  →  PostgreSQL  →  Grafana
```

---

## Requirements

- Docker
- A running PostgreSQL instance (any version 13+)
- Tautulli with its SQLite database accessible on the host
- Grafana (for the included dashboards)

---

## Docker Run

```bash
docker run -d \
  --name tautulli-postgres-sync \
  --restart unless-stopped \
  -e POSTGRES_HOST=192.168.1.100 \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB=tautulli \
  -e POSTGRES_USER=tautulli \
  -e POSTGRES_PASSWORD=your_secure_password \
  -e TAUTULLI_DB=/data/tautulli.db \
  -v /path/to/tautulli/appdata:/data:ro \
  -v /path/to/logs:/logs \
  ghcr.io/yourusername/tautulli-postgres-sync:latest
```

The container syncs immediately on startup, then runs automatically every night at **2:00 AM**.

---

## Build It Yourself

```bash
git clone https://github.com/yourusername/tautulli-postgres-sync.git
cd tautulli-postgres-sync

docker build -t tautulli-postgres-sync .

docker run -d \
  --name tautulli-postgres-sync \
  --restart unless-stopped \
  -e POSTGRES_HOST=192.168.1.100 \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB=tautulli \
  -e POSTGRES_USER=tautulli \
  -e POSTGRES_PASSWORD=your_secure_password \
  -v /mnt/user/appdata/tautulli:/data:ro \
  -v /mnt/user/appdata/tautulli-sync/logs:/logs \
  tautulli-postgres-sync
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TAUTULLI_DB` | `/data/tautulli.db` | Path to the Tautulli SQLite file inside the container |
| `POSTGRES_HOST` | `localhost` | Hostname or IP of your PostgreSQL server |
| `POSTGRES_PORT` | `5432` | PostgreSQL port |
| `POSTGRES_DB` | `tautulli` | Target database name |
| `POSTGRES_USER` | `tautulli` | PostgreSQL username |
| `POSTGRES_PASSWORD` | `change_me` | PostgreSQL password — always set this |
| `LOG_FILE` | `/logs/sync.log` | Log file path inside the container |
| `USER_MAPPING` | *(empty)* | Inline username remapping (see below) |
| `USER_MAPPING_FILE` | `/config/user_mapping.json` | Path to a JSON mapping file |

---

## User Mapping

Plex users sometimes change their usernames. Without mapping, the same person appears under two different names in your statistics — breaking continuity across years of data.

User mapping lets you define `OldName → NewName` so all historical and future plays are attributed to the same identity in PostgreSQL.

### Option A — Inline via environment variable

Pass comma-separated `old:new` pairs:

```
-e USER_MAPPING=JohnDoe2019:JohnDoe,OldName:CurrentName
```

All plays from `JohnDoe2019` will be stored as `JohnDoe` in PostgreSQL.

### Option B — JSON file

Mount a config directory and place a `user_mapping.json` file in it:

```bash
-v /path/to/config:/config
```

`/path/to/config/user_mapping.json`:

```json
{
  "user_mapping": {
    "JohnDoe2019": "JohnDoe",
    "OldPlexName": "CurrentPlexName"
  }
}
```

The JSON file takes priority over the environment variable. If neither is configured, the sync runs without any remapping (which is fine if no one has changed their Plex username).

---

## Tables Synced

The following Tautulli tables are mirrored into PostgreSQL:

| Table | Contents |
|-------|----------|
| `users` | Plex user accounts |
| `library_sections` | Plex library metadata |
| `session_history` | Every individual play session |
| `session_history_metadata` | Title, year, media type, ratings |
| `session_history_media_info` | Codec, resolution, bitrate |

A `sync_metadata` table tracks the last synced row ID per table to enable incremental syncs on subsequent runs.

---

## Grafana Dashboards

Two ready-to-import dashboard files are included:

| File | Description |
|------|-------------|
| `Tautulli 16_9-*.json` | 16:9 optimized layout |
| `dashboard-*.json` | Alternative format for Grafana 10+ |

**To import:** Grafana → Dashboards → Import → Upload JSON file → select your PostgreSQL datasource when prompted.

**PostgreSQL datasource settings:**

- Host: `your-postgres-host:5432`
- Database: `tautulli`
- User/Password: your credentials
- TLS/SSL Mode: `disable` (for local setups)

---

## Unraid

In the Unraid Docker tab, add a new container with these settings:

| Field | Value |
|-------|-------|
| Repository | `yourusername/tautulli-postgres-sync` |
| Network type | `bridge` |
| Variable `POSTGRES_HOST` | IP of your Unraid server |
| Variable `POSTGRES_PASSWORD` | Your PostgreSQL password |
| Path `/data` | Host path to your Tautulli appdata folder |
| Path `/logs` | Where you want sync logs stored |

The first run performs a full import of all historical data. With several years of history this can take a few minutes. Watch progress with:

```bash
docker logs tautulli-postgres-sync -f
```

---

## Manual Sync

To trigger a sync at any time without waiting for the nightly cron:

```bash
docker exec tautulli-postgres-sync python3 /app/sync.py
```

---

## Troubleshooting

**Cannot connect to PostgreSQL**
Make sure `POSTGRES_HOST` is reachable from inside the container. Use the actual IP address, not `localhost` (which resolves to the container itself, not the host).

**Tautulli DB not found**
Confirm the volume mount points to the folder containing `tautulli.db`. Check with:
```bash
docker exec tautulli-postgres-sync ls /data/
```

**First sync is slow**
Normal behavior — a full historical import takes time proportional to how many years of data you have. Subsequent runs are fast since only new rows are fetched.

**"Permission denied" on SQLite**
The volume is mounted `:ro` (read-only), which is intentional and sufficient. If Tautulli has an exclusive write lock on the DB at the exact moment the sync runs, simply retry — the sync will pick up where it left off.
