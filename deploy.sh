#!/usr/bin/env bash
# Build this checkout and (re)start the container on the host. Run it from the
# VM after `git pull`:
#
#   git pull --ff-only && ./deploy.sh
#
# Env overrides: MYIP_IMAGE, MYIP_CONTAINER, MYIP_HOST_PORT.
set -euo pipefail

cd "$(dirname "$0")"

IMAGE="${MYIP_IMAGE:-myip:latest}"
CONTAINER="${MYIP_CONTAINER:-myip}"
PORT="${MYIP_HOST_PORT:-8500}"

echo "deploying $(git rev-parse --short HEAD) as $IMAGE"

# Build before touching the running container so a bad build cannot take the
# site down. Caddy reaches it on 127.0.0.1:$PORT, so publish on loopback only.
docker build -t "$IMAGE" .
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" --restart unless-stopped \
    -p "${PORT}:8500" "$IMAGE"
docker image prune -f >/dev/null

# Wait for the image's HEALTHCHECK instead of guessing at a sleep.
for _ in $(seq 1 15); do
    if [ "$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null)" = healthy ]; then
        echo "healthy: http://127.0.0.1:${PORT}/"
        exit 0
    fi
    sleep 4
done

echo "container '$CONTAINER' did not become healthy" >&2
docker logs --tail 50 "$CONTAINER" >&2
exit 1
