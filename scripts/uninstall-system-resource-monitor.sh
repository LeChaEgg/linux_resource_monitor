#!/bin/sh

set -eu

SERVICE_NAME="system-resource-monitor.service"
BIN_PATH="/usr/local/bin/system-resource-monitor"
SUMMARY_BIN_PATH="/usr/local/bin/system-resource-monitor-summary"
ENV_PATH="/etc/default/system-resource-monitor"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
DEFAULT_LOG_DIR="/var/log/system-resource-monitor"

PURGE=0

usage() {
    cat <<'EOF'
Usage: uninstall-system-resource-monitor.sh [--purge]

Options:
  --purge    Also remove /etc/default/system-resource-monitor and log files
  -h, --help Show this help message
EOF
}

need_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Missing required command: $1" >&2
        exit 1
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --purge)
            PURGE=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

if [ "$(uname -s)" != "Linux" ]; then
    echo "This uninstaller only supports Linux with systemd." >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this uninstaller as root." >&2
    exit 1
fi

need_cmd rm
need_cmd systemctl

if [ -f "$SERVICE_PATH" ]; then
    systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
    rm -f "$SERVICE_PATH"
    systemctl daemon-reload
    systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
else
    systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
fi

rm -f "$BIN_PATH" "$SUMMARY_BIN_PATH"

if [ "$PURGE" -eq 1 ]; then
    rm -f "$ENV_PATH"
    rm -rf "$DEFAULT_LOG_DIR"
fi

echo "Uninstalled ${SERVICE_NAME}"
echo "Removed: $BIN_PATH"
echo "Removed: $SUMMARY_BIN_PATH"

if [ "$PURGE" -eq 1 ]; then
    echo "Removed: $ENV_PATH"
    echo "Removed: $DEFAULT_LOG_DIR"
else
    echo "Preserved config: $ENV_PATH"
    echo "Preserved logs: $DEFAULT_LOG_DIR"
    echo "Use --purge to remove preserved files."
fi
