#!/usr/bin/env bash
# reset_db.sh — Xóa sạch db.json về trạng thái rỗng ban đầu.
# Chỉ dùng trong lab, KHÔNG dùng trong production.
#
# Usage:
#   bash reset_db.sh           # xóa db, giữ lại certs
#   bash reset_db.sh --full    # xóa db + toàn bộ certs đã cấp

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DB_FILE="${SCRIPT_DIR}/CA-data/db.json"
CLIENTS_DIR="${SCRIPT_DIR}/CA-data/clients"

echo "=== Reset DB EAP-TLS ==="

# Dừng service nếu đang chạy
if [ -f "${SCRIPT_DIR}/.pids" ]; then
    echo "[STOP] Đang dừng các service..."
    bash "${SCRIPT_DIR}/start.sh" stop 2>/dev/null || true
    sleep 1
fi

# Backup db cũ
if [ -f "${DB_FILE}" ]; then
    BACKUP="${DB_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
    cp "${DB_FILE}" "${BACKUP}"
    echo "[BACKUP] Đã lưu db cũ → ${BACKUP}"
fi

# Ghi db trống
cat > "${DB_FILE}" << 'EOF'
{
    "users": {},
    "devices": {},
    "requests": {}
}
EOF
echo "[RESET] db.json đã được xóa về trạng thái rỗng."

# Xóa certs nếu --full
if [ "$1" = "--full" ]; then
    if [ -d "${CLIENTS_DIR}" ]; then
        rm -rf "${CLIENTS_DIR}"
        echo "[RESET] Đã xóa toàn bộ certs trong ${CLIENTS_DIR}"
    fi

    # Reset OpenSSL CA index (giữ CA cert & key, xóa issued certs index)
    CA_DIR="${SCRIPT_DIR}/CA-data"
    > "${CA_DIR}/index.txt"
    echo "01" > "${CA_DIR}/ca.srl"
    echo "01" > "${CA_DIR}/crlnumber"
    echo "[RESET] Đã reset CA index (index.txt, ca.srl, crlnumber)."
fi

echo ""
echo "=== Hoàn tất ==="
echo "Khởi động lại: ./start.sh"
if [ "$1" != "--full" ]; then
    echo "Lưu ý: File cert cũ trong CA-data/clients/ vẫn còn."
    echo "Dùng --full để xóa luôn: bash reset_db.sh --full"
fi
