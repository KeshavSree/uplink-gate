#!/usr/bin/env bash
# Publish the demo: a Cloudflare quick tunnel to the gate service on this machine, and the
# web UI on Vercel with its /api requests sent through that tunnel.
#
#   ./scripts/publish.sh            start the tunnel if needed, then deploy the UI
#   ./scripts/publish.sh --stop     stop the tunnel
#
# Needs: the compose stack running, `cloudflared` and a logged-in `vercel` CLI.
# A quick tunnel gets a new address whenever it restarts; run this again after that.
set -euo pipefail
cd "$(dirname "$0")/.."

port="$(grep -s '^GATE_PORT=' .env | cut -d= -f2)"
port="${port:-8000}"
mkdir -p logs
pid_file=logs/tunnel.pid
log_file=logs/tunnel.log

if [ "${1:-}" = "--stop" ]; then
    [ -f "${pid_file}" ] && kill "$(cat "${pid_file}")" 2>/dev/null && echo "tunnel stopped"
    rm -f "${pid_file}"
    exit 0
fi

if ! curl -fs "http://localhost:${port}/api/health" >/dev/null; then
    echo "the gate service is not answering on port ${port}; run: docker compose up -d" >&2
    exit 1
fi

if [ ! -f "${pid_file}" ] || ! kill -0 "$(cat "${pid_file}")" 2>/dev/null; then
    : >"${log_file}"
    nohup cloudflared tunnel --no-autoupdate --url "http://localhost:${port}" >"${log_file}" 2>&1 &
    echo $! >"${pid_file}"
    echo "tunnel starting (pid $(cat "${pid_file}"))"
fi

url=""
for _ in $(seq 1 60); do
    url="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "${log_file}" | head -n 1 || true)"
    [ -n "${url}" ] && curl -fs "${url}/api/health" >/dev/null 2>&1 && break
    sleep 1
done
if [ -z "${url}" ]; then
    echo "the tunnel did not come up; see ${log_file}" >&2
    exit 1
fi
echo "tunnel: ${url}"

# vercel.json is generated here because the tunnel address changes. It is not committed.
cat >web/vercel.json <<JSON
{
  "\$schema": "https://openapi.vercel.sh/vercel.json",
  "framework": "vite",
  "buildCommand": "npm run build",
  "outputDirectory": "dist",
  "rewrites": [{ "source": "/api/:path*", "destination": "${url}/api/:path*" }]
}
JSON

cd web
vercel link --yes --project uplink-gate >/dev/null
vercel deploy --prod --yes --build-env "VITE_EVENTS_URL=${url/https:/wss:}/api/events"
