#!/usr/bin/env bash
set -euo pipefail

# Grafana Alloy one-click installer for GCE VM
# - Installs Alloy binary
# - Creates system user
# - Writes /etc/default/alloy with LOKI_URL and NGINX_JSON_LOG
# - Writes /etc/alloy/config.river (embedded below)
# - Writes /etc/systemd/system/alloy.service
# - Enables and starts the service
#
# Usage examples:
#   sudo ./install_alloy.sh --loki-url https://loki.example.com \
#                           --nginx-log "/opt/logs/nginx/*.log"   # supports glob
# or set environment variables:
#   sudo LOKI_URL=https://loki.example.com \
#        NGINX_JSON_LOG="/opt/logs/nginx/*.log" \
#        ./install_alloy.sh

ALLOY_VERSION=${ALLOY_VERSION:-"1.4.1"}
LOKI_URL="${LOKI_URL:-}"        # e.g. https://loki.example.com
NGINX_JSON_LOG="${NGINX_JSON_LOG:-}"  # e.g. /opt/logs/nginx/*.log (glob supported)

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --loki-url)
      LOKI_URL="$2"; shift 2;;
    --nginx-log)
      NGINX_JSON_LOG="$2"; shift 2;;
    --version)
      ALLOY_VERSION="$2"; shift 2;;
    -h|--help)
      cat <<EOF
Usage: sudo $0 --loki-url <URL> --nginx-log <PATH|GLOB> [--version <v>]
  --loki-url    Loki external URL (required)
  --nginx-log   NGINX JSON access log path or glob (required), e.g. "/opt/logs/nginx/*.log"
  --version     Alloy version (default: ${ALLOY_VERSION})
Environment alternatives: LOKI_URL, NGINX_JSON_LOG, ALLOY_VERSION
EOF
      exit 0;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

if [[ -z "${LOKI_URL}" || -z "${NGINX_JSON_LOG}" ]]; then
  echo "Error: LOKI_URL and NGINX_JSON_LOG are required. Use --help for usage." >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (use sudo)." >&2
  exit 1
fi

# 1) Prepare dirs
mkdir -p /etc/alloy /var/lib/alloy /etc/systemd/system

# 2) Install Alloy binary if missing
if ! command -v alloy >/dev/null 2>&1; then
  echo "Installing Alloy v${ALLOY_VERSION}..."
  curl -fsSL -o /usr/local/bin/alloy \
    "https://github.com/grafana/alloy/releases/download/v${ALLOY_VERSION}/alloy-linux-amd64"
  chmod +x /usr/local/bin/alloy
else
  echo "Alloy already installed at $(command -v alloy)"
fi

# 3) Create user if not exists
if ! id -u alloy >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin alloy
fi
chown -R alloy:alloy /etc/alloy /var/lib/alloy || true

# 4) Write /etc/default/alloy
cat >/etc/default/alloy <<ENVEOF
LOKI_URL=${LOKI_URL}
NGINX_JSON_LOG=${NGINX_JSON_LOG}
ENVEOF

# 5) Write /etc/alloy/config.river (embedded)
cat >/etc/alloy/config.river <<'RIVEREOF'
// Grafana Alloy River config: read NGINX JSON logs from a file and push to Loki
// Env vars used:
// - LOKI_URL (e.g. https://loki.example.com or http://<LB-IP>:3100)
// - NGINX_JSON_LOG (e.g. /var/log/nginx/access_json.log)

loki.source.file "nginx_json" {
  // Tail NGINX JSON access log
  targets = [{ __path__ = env("NGINX_JSON_LOG") }]
  forward_to = [loki.process.nginx_json.receiver]
}

loki.process "nginx_json" {
  stage.json {}

  // Promote common fields to labels for filtering in LogQL/Grafana Explore.
  stage.labels {
    values = {
      status            = "status",
      http_host         = "http_host",
      request_url_path  = "request_url_path",
      method            = "method",
      upstream_status   = "upstream_status",
      upstream_host     = "upstream_host",
      pro               = "pro"
    }
  }

  // Optional: convert status to numeric
  // stage.template {
  //   source = "status"
  //   template = "{{ ToInt .Value }}"
  // }

  // Optional: parse timestamp from log
  // stage.timestamp {
  //   source = "access_time"
  //   format = "2006-01-02T15:04:05-07:00"
  // }

  forward_to = [loki.write.default.receiver]
}

loki.write "default" {
  endpoint {
    url = env("LOKI_URL")
    // If Loki requires basic auth or token, set one of the below via environment variables:
    // basic_auth {
    //   username = env("LOKI_USERNAME")
    //   password = env("LOKI_PASSWORD")
    // }
    // bearer_token = env("LOKI_BEARER_TOKEN")

    // For testing self-signed certs (not recommended in prod):
    // tls_insecure_skip_verify = true
  }
}
RIVEREOF

# 6) Write /etc/systemd/system/alloy.service
cat >/etc/systemd/system/alloy.service <<'UNITEOF'
[Unit]
Description=Grafana Alloy Service
Wants=network-online.target
After=network-online.target

[Service]
User=alloy
Group=alloy
Type=simple
EnvironmentFile=-/etc/default/alloy
ExecStart=/usr/local/bin/alloy run /etc/alloy/config.river
Restart=always
RestartSec=5
LimitNOFILE=65536

# Hardening (optional)
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNITEOF

# 7) Reload and start
systemctl daemon-reload
systemctl enable alloy
systemctl restart alloy

# 8) Show status summary
sleep 1
systemctl --no-pager --full status alloy || true

cat <<DONE

Alloy install completed.
- Config:        /etc/alloy/config.river
- Env file:      /etc/default/alloy
- Service:       systemctl status alloy
- Logs (follow): journalctl -u alloy -f -n 200

Verify in Grafana Explore that logs arrive to Loki.
DONE
