#!/bin/bash
# ============================================================================
# Tautulli Database Safe Backup & Sync Script for Unraid
# ============================================================================
# Run this via Unraid User Scripts (scheduled or on demand).
# It safely copies the live Tautulli DB, starts the sync container,
# waits for the sync to complete, then stops the container again.
# ============================================================================

# ============================================================================
# CONFIGURATION - adjust these to match your setup
# ============================================================================

# Where is your Tautulli database?
SOURCE_DB="/mnt/user/appdata/tautulli/tautulli.db"

# Where should the working copy be placed? (must match the Docker volume mount)
DEST_DB="/mnt/user/appdata/tautull_sync/db/tautulli.db"

# Name of the sync Docker container
SYNC_CONTAINER="tautulli-postgres"

# How long to wait for the sync to complete (seconds). 600 = 10 minutes.
# Increase this if you have many years of data on the first run.
SYNC_DURATION=600

# Log file location
LOG_DIR="/mnt/user/appdata/tautull_sync/logs"
LOG_FILE="${LOG_DIR}/unraid-sync.log"

# ============================================================================
# FUNCTIONS
# ============================================================================

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    if [ -d "$LOG_DIR" ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
    fi
}

error_exit() {
    log "ERROR: $1"
    exit 1
}

# ============================================================================
# STEP 0: SETUP
# ============================================================================

mkdir -p "$LOG_DIR" 2>/dev/null

log "=========================================="
log "Tautulli Safe Sync Script Started"
log "=========================================="

# Sanity checks
[ ! -f "$SOURCE_DB" ] && error_exit "Source database not found: $SOURCE_DB"

DEST_DIR=$(dirname "$DEST_DB")
if [ ! -d "$DEST_DIR" ]; then
    log "Creating destination directory: $DEST_DIR"
    mkdir -p "$DEST_DIR" || error_exit "Failed to create destination directory"
fi

if ! docker ps -a --format '{{.Names}}' | grep -q "^${SYNC_CONTAINER}$"; then
    error_exit "Container '$SYNC_CONTAINER' not found. Is it created in Docker?"
fi

# ============================================================================
# STEP 1: SAFE DATABASE COPY
# ============================================================================

log "Creating safe database backup..."
log "  From: $SOURCE_DB  ($(du -h "$SOURCE_DB" | cut -f1))"
log "  To:   $DEST_DB"

# Use SQLite's built-in BACKUP command — safe to run against a live database
TEMP_BACKUP="/tmp/tautulli_backup_$$.db"
sqlite3 "$SOURCE_DB" ".backup '$TEMP_BACKUP'"

[ $? -ne 0 ] && { rm -f "$TEMP_BACKUP"; error_exit "SQLite backup command failed"; }
[ ! -f "$TEMP_BACKUP" ] && error_exit "Temp backup file was not created"

log "  Backup size: $(du -h "$TEMP_BACKUP" | cut -f1)"

mv "$TEMP_BACKUP" "$DEST_DB" || error_exit "Failed to move backup to destination"

log "Done — DB ready at: $DEST_DB"

# ============================================================================
# STEP 2: STOP CONTAINER (if already running)
# ============================================================================

if docker ps --format '{{.Names}}' | grep -q "^${SYNC_CONTAINER}$"; then
    log "Stopping existing container run: $SYNC_CONTAINER"
    docker stop "$SYNC_CONTAINER" 2>&1 | head -1
    sleep 2
fi

# ============================================================================
# STEP 3: START SYNC CONTAINER
# ============================================================================

log "Starting container: $SYNC_CONTAINER"
docker start "$SYNC_CONTAINER" >/dev/null 2>&1 || error_exit "Failed to start container"
log "Container started — monitoring for ${SYNC_DURATION}s..."

# ============================================================================
# STEP 4: WAIT & MONITOR
# ============================================================================

ELAPSED=0
INTERVAL=30

while [ $ELAPSED -lt $SYNC_DURATION ]; do
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))

    log "  [${ELAPSED}s / ${SYNC_DURATION}s]"

    LAST_LOG=$(docker logs --tail 1 "$SYNC_CONTAINER" 2>&1 | grep -v "^$")
    [ -n "$LAST_LOG" ] && log "    Container: $LAST_LOG"
done

# ============================================================================
# STEP 5: STOP CONTAINER
# ============================================================================

log "Stopping container: $SYNC_CONTAINER"
docker stop "$SYNC_CONTAINER" >/dev/null 2>&1
log "Container stopped"

# ============================================================================
# STEP 6: CLEANUP
# ============================================================================

rm -f /tmp/tautulli_backup_*.db 2>/dev/null
log "Temp files cleaned up"

# ============================================================================
# STEP 7: SUMMARY
# ============================================================================

log "=========================================="
log "Sync completed successfully"
log "  Database: $DEST_DB"
log "  Log:      $LOG_FILE"
log "=========================================="

exit 0
