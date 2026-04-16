#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

SERVICE_NAME="system-resource-monitor.service"
BIN_PATH="/usr/local/bin/system-resource-monitor"
SUMMARY_BIN_PATH="/usr/local/bin/system-resource-monitor-summary"
ENV_PATH="/etc/default/system-resource-monitor"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
DEFAULT_LOG_DIR="/var/log/system-resource-monitor"

need_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

if [ "$(uname -s)" != "Linux" ]; then
    echo "This installer only supports Linux with systemd." >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root." >&2
    exit 1
fi

need_cmd python3
need_cmd install
need_cmd systemctl

install -m 0755 "${REPO_ROOT}/scripts/resource_monitor.py" "$BIN_PATH"
install -m 0755 "${REPO_ROOT}/scripts/summarize_resource_monitor.py" "$SUMMARY_BIN_PATH"
install -d -m 0755 "$DEFAULT_LOG_DIR"

if [ ! -f "$ENV_PATH" ]; then
    cat > "$ENV_PATH" <<'EOF'
INTERVAL_SECONDS=10
TOP_N=5
RETAIN_DAYS=30
LOG_DIR=/var/log/system-resource-monitor
EOF
    chmod 0644 "$ENV_PATH"
fi

cat > "$SERVICE_PATH" <<'EOF'
[Unit]
Description=System resource monitor
After=multi-user.target

[Service]
Type=simple
EnvironmentFile=-/etc/default/system-resource-monitor
ExecStartPre=/bin/sh -c 'mkdir -p "${LOG_DIR:-/var/log/system-resource-monitor}"'
ExecStart=/bin/sh -c 'exec /usr/local/bin/system-resource-monitor --interval "${INTERVAL_SECONDS:-10}" --top-n "${TOP_N:-5}" --retain-days "${RETAIN_DAYS:-30}" --log-dir "${LOG_DIR:-/var/log/system-resource-monitor}"'
Restart=always
RestartSec=5
Nice=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$SERVICE_PATH"

systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "Installed ${SERVICE_NAME}"
echo "Binary: $BIN_PATH"
echo "Summary: $SUMMARY_BIN_PATH"
echo "Config: $ENV_PATH"
echo "Logs: $DEFAULT_LOG_DIR"
echo "Service status: systemctl status ${SERVICE_NAME} --no-pager"
