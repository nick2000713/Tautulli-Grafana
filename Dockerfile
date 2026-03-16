FROM python:3.11-slim

# Install dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    cron \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip install --no-cache-dir psycopg2-binary

# Create directories
# /tautulli  — mount your Tautulli appdata folder here (read-only)
# /data      — used by the optional unraid-sync.sh pre-copy approach
# /logs      — sync logs
# /config    — optional user_mapping.json
RUN mkdir -p /app /tautulli /data /logs /config

# Copy sync script
COPY tautulli_postgres_sync.py /app/sync.py

RUN chmod +x /app/sync.py

# Create cron job (runs daily at 2 AM)
RUN echo "0 2 * * * /usr/local/bin/python3 /app/sync.py >> /logs/sync.log 2>&1" > /etc/cron.d/tautulli-sync && \
    chmod 0644 /etc/cron.d/tautulli-sync && \
    crontab /etc/cron.d/tautulli-sync

# Create entrypoint script
RUN echo '#!/bin/bash\n\
echo "Tautulli PostgreSQL Sync Container Started"\n\
echo "Running initial sync..."\n\
/usr/local/bin/python3 /app/sync.py\n\
echo "Initial sync complete. Starting cron daemon..."\n\
cron && tail -f /logs/sync.log' > /entrypoint.sh && \
    chmod +x /entrypoint.sh

WORKDIR /app

CMD ["/entrypoint.sh"]
