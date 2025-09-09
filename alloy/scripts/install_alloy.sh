#!/usr/bin/env bash
set -euo pipefail

# Grafana Alloy one-click installer for multiple Linux distributions
# Supports: Ubuntu 22/24 LTS, CentOS 7, Debian 12/13, CentOS-stream-10
#
# - Detects Linux distribution and installs required dependencies
# - Installs Alloy binary
# - Writes /etc/default/alloy with LOKI_URL, NGINX_ACCESS_LOG, and NGINX_ERROR_LOG
# - Writes /etc/systemd/system/alloy.service (run as nginx)
# Note: This script no longer writes /etc/alloy/config.river. Please deploy your
#       own config file to /etc/alloy/config.river before starting the service.
# - Enables and starts the service
#
# Usage examples:
#   sudo ./install_alloy.sh --loki-url http://10.11.11.215:3100 \
#                           --access-log "/opt/logs/nginx/*access*.log*" \
#                           --error-log "/opt/logs/nginx/*error*.log"
# or set environment variables:
#   sudo LOKI_URL=http://10.11.11.215:3100 \
#        NGINX_ACCESS_LOG="/opt/logs/nginx/*access*.log*" \
#        NGINX_ERROR_LOG="/opt/logs/nginx/*error*.log" \
#        ./install_alloy.sh

ALLOY_VERSION=${ALLOY_VERSION:-"1.10.2"}
LOKI_URL="${LOKI_URL:-http://10.11.11.215:3100}"  # Default to our Loki server
NGINX_ACCESS_LOG="${NGINX_ACCESS_LOG:-${NGINX_JSON_LOG:-}}"  # For backward compatibility, support old var
NGINX_ERROR_LOG="${NGINX_ERROR_LOG:-}"  # e.g. /opt/logs/nginx/*error*.log

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --loki-url)
      LOKI_URL="$2"; shift 2;;
    --nginx-log)  # For backward compatibility
      NGINX_ACCESS_LOG="$2"; shift 2;;
    --access-log)
      NGINX_ACCESS_LOG="$2"; shift 2;;
    --error-log)
      NGINX_ERROR_LOG="$2"; shift 2;;
    --version)
      ALLOY_VERSION="$2"; shift 2;;
    -h|--help)
      cat <<EOF
Usage: sudo $0 --loki-url <URL> --access-log <PATH|GLOB> --error-log <PATH|GLOB> [--version <v>]
  --loki-url    Loki external URL (required)
  --access-log  NGINX JSON access log path or glob (required), e.g. "/opt/logs/nginx/*access*.log*"
  --error-log   NGINX error log path or glob (required), e.g. "/opt/logs/nginx/*error*.log"
  --version     Alloy version (default: ${ALLOY_VERSION})
Environment alternatives: LOKI_URL, NGINX_ACCESS_LOG, NGINX_ERROR_LOG, ALLOY_VERSION
EOF
      exit 0;;
    *) echo "Unknown arg: $1"; exit 1;;
  esac
done

if [[ -z "${LOKI_URL}" || -z "${NGINX_ACCESS_LOG}" ]]; then
  echo "Error: LOKI_URL and NGINX_ACCESS_LOG are required. Use --help for usage." >&2
  exit 1
fi

# If error log path not set, use a reasonable default based on access log pattern
if [[ -z "${NGINX_ERROR_LOG}" ]]; then
  echo "Warning: NGINX_ERROR_LOG not specified, trying to infer from access log pattern..." >&2
  # Replace 'access' with 'error' in the path if possible, otherwise use generic pattern
  if [[ "${NGINX_ACCESS_LOG}" == *"access"* ]]; then
    NGINX_ERROR_LOG="${NGINX_ACCESS_LOG//access/error}"
  else
    # Extract directory from access log pattern and add error*.log
    LOG_DIR="$(dirname "${NGINX_ACCESS_LOG}")"
    NGINX_ERROR_LOG="${LOG_DIR}/*error*.log"
  fi
  echo "Using NGINX_ERROR_LOG=${NGINX_ERROR_LOG}" >&2
fi

if [[ $EUID -ne 0 ]]; then
  echo "Please run as root (use sudo)." >&2
  exit 1
fi

# 0) Detect distribution and install dependencies
detect_distro() {
  if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO="$ID"
    VERSION="$VERSION_ID"
    
    # Handle CentOS Stream separately
    if [ "$DISTRO" = "centos" ] && grep -q "Stream" /etc/centos-release 2>/dev/null; then
      DISTRO="centos-stream"
    fi
  elif type lsb_release >/dev/null 2>&1; then
    DISTRO=$(lsb_release -si | tr '[:upper:]' '[:lower:]')
    VERSION=$(lsb_release -sr)
  elif [ -f /etc/lsb-release ]; then
    . /etc/lsb-release
    DISTRO="$DISTRIB_ID"
    VERSION="$DISTRIB_RELEASE"
  elif [ -f /etc/debian_version ]; then
    DISTRO="debian"
    VERSION=$(cat /etc/debian_version)
  elif [ -f /etc/redhat-release ]; then
    DISTRO="centos"
    VERSION=$(rpm -qa \*-release | grep -Ei "centos|redhat" | cut -d"-" -f3)
  else
    DISTRO="unknown"
    VERSION="unknown"
  fi
  
  echo "Detected Linux distribution: $DISTRO $VERSION"
}

install_dependencies() {
  echo "Installing required dependencies..."
  case "$DISTRO" in
    ubuntu|debian)
      DEBIAN_FRONTEND=noninteractive apt-get update
      DEBIAN_FRONTEND=noninteractive apt-get install -y curl systemd procps unzip
      ;;
    centos|rhel|centos-stream)
      if command -v dnf >/dev/null 2>&1; then
        dnf install -y curl systemd procps-ng unzip
      else
        yum install -y curl systemd procps-ng unzip
      fi
      ;;
    *)
      echo "Warning: Unsupported distribution $DISTRO. Proceeding anyway, but may fail."
      ;;
  esac
}

# Detect distribution
detect_distro

# Install dependencies
# install_dependencies

# 1) 檢查 nginx 使用者是否存在（服務將以 nginx 執行）
if ! id -u nginx >/dev/null 2>&1; then
  echo "⚠️  Warning: user 'nginx' not found. The service will attempt to run as 'nginx' and may fail. Please ensure nginx user/group exist." >&2
fi

# 2) Prepare dirs
mkdir -p /etc/alloy /var/lib/alloy /etc/systemd/system

# 創建並設置 Alloy 數據目錄
mkdir -p /var/lib/alloy/data-alloy
chmod 755 /var/lib/alloy
chmod -R 755 /var/lib/alloy/data-alloy

# 確保資料目錄擁有者是 nginx 用戶（若存在）
if id -u nginx >/dev/null 2>&1; then
  chown -R nginx:nginx /var/lib/alloy || true
  echo "✅ Set ownership of data directory to nginx user"
fi

# 3) Download and install Alloy binary if missing
if ! command -v alloy >/dev/null 2>&1; then
  echo "Installing Alloy v${ALLOY_VERSION}..."
  # Determine architecture
  ARCH=$(uname -m)
  BINARY_ARCH="amd64"  # Default
  
  if [ "$ARCH" = "x86_64" ]; then
    BINARY_ARCH="amd64"
  elif [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
    BINARY_ARCH="arm64"
  elif [ "$ARCH" = "armv7l" ]; then
    BINARY_ARCH="arm"
  fi
  
  TEMP_DIR=$(mktemp -d)
  ALLOY_ZIP="${TEMP_DIR}/alloy.zip"
  
  echo "Downloading Alloy v${ALLOY_VERSION} (${BINARY_ARCH})..."
  curl -fsSL -o "${ALLOY_ZIP}" \
    "https://github.com/grafana/alloy/releases/download/v${ALLOY_VERSION}/alloy-linux-${BINARY_ARCH}.zip"
  
  if [ $? -ne 0 ]; then
    echo "❌ Failed to download Alloy. Please check the version and network connection."
    exit 1
  fi
  
  echo "Extracting Alloy binary..."
  unzip -q "${ALLOY_ZIP}" -d "${TEMP_DIR}"
  
  # 檢查解壓後的二進位檔案名稱可能性
  if [ -f "${TEMP_DIR}/alloy-linux-${BINARY_ARCH}" ]; then
    echo "Moving alloy-linux-${BINARY_ARCH} to /usr/local/bin/alloy"
    mv "${TEMP_DIR}/alloy-linux-${BINARY_ARCH}" /usr/local/bin/alloy
    chmod +x /usr/local/bin/alloy
    echo "✅ Alloy binary installed successfully"
  elif [ -f "${TEMP_DIR}/alloy" ]; then
    echo "Moving alloy to /usr/local/bin/alloy"
    mv "${TEMP_DIR}/alloy" /usr/local/bin/alloy
    chmod +x /usr/local/bin/alloy
    echo "✅ Alloy binary installed successfully"
  else
    echo "❌ Could not find extracted Alloy binary"
    ls -la "${TEMP_DIR}"  # 顯示解壓目錄內容以便診斷
    exit 1
  fi
  
  # Clean up
  rm -rf "${TEMP_DIR}"
  
  # Set executable permissions (ownership will be set after user creation)
  chmod 755 /usr/local/bin/alloy
  
  # For SELinux systems, set proper context
  if command -v restorecon >/dev/null 2>&1; then
    restorecon -v /usr/local/bin/alloy
  fi
else
  echo "Alloy already installed at $(command -v alloy)"
  
  # Update permissions even if already installed
  chmod 755 /usr/local/bin/alloy
  
  # For SELinux systems, set proper context
  if command -v restorecon >/dev/null 2>&1; then
    restorecon -v /usr/local/bin/alloy
  fi
fi

# Update permissions on directories
echo "Setting permissions on Alloy directories and files..."
if id -u nginx >/dev/null 2>&1; then
  chown -R nginx:nginx /etc/alloy /var/lib/alloy 2>/dev/null || echo "⚠️ Could not set ownership of Alloy directories to nginx"
else
  echo "⚠️ Skipping chown to nginx for /etc/alloy and /var/lib/alloy because nginx user not found"
fi

# Set binary permissions again to ensure they're correct after user creation
if [ -f "/usr/local/bin/alloy" ]; then
  chmod 755 /usr/local/bin/alloy
  
  # 創建符號連結使命令在系統路徑中可用
  if [ ! -f "/usr/bin/alloy" ]; then
    ln -sf /usr/local/bin/alloy /usr/bin/alloy
    echo "✅ Created symlink to Alloy binary in /usr/bin"
  fi
fi

# 4) Write /etc/default/alloy
cat >/etc/default/alloy <<ENVEOF
LOKI_URL=${LOKI_URL}
NGINX_ACCESS_LOG=${NGINX_ACCESS_LOG}
NGINX_ERROR_LOG=${NGINX_ERROR_LOG}
ENVEOF

# 5) Skipped writing /etc/alloy/config.river
#    Provide your own config at /etc/alloy/config.river before starting the service.

# 6) Write /etc/systemd/system/alloy.service
cat >/etc/systemd/system/alloy.service <<'UNITEOF'
[Unit]
Description=Grafana Alloy Service
Wants=network-online.target
After=network-online.target

[Service]
User=nginx
Group=nginx
Type=simple
EnvironmentFile=-/etc/default/alloy

# 指定數據目錄
WorkingDirectory=/var/lib/alloy

# 使用完整路徑執行二進制文件
ExecStart=/usr/local/bin/alloy run /etc/alloy/config.river

# 設置重啟參數
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

# 8) Verify connectivity to Loki
echo "Verifying connectivity to Loki server at $LOKI_URL..."
if command -v curl >/dev/null 2>&1; then
  if curl -s --connect-timeout 5 "$LOKI_URL/ready" | grep -q "ready"; then
    echo "✅ Successfully connected to Loki server"
  else
    echo "⚠️  Warning: Unable to verify Loki connection. Please check manually."
  fi
fi

# 9) Show status summary
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