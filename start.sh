#!/usr/bin/env bash
# start.sh — Khởi động 3 service EAP-TLS song song
# Dùng: bash start.sh
# Dừng: bash start.sh stop   hoặc  Ctrl+C

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/logs"
mkdir -p "${LOG_DIR}"

PID_FILE="${SCRIPT_DIR}/.pids"

stop_services() {
    if [ ! -f "${PID_FILE}" ]; then
        echo "Không có service nào đang chạy."
        return
    fi
    echo "=== Dừng các service ==="
    while read -r name pid; do
        if kill -0 "${pid}" 2>/dev/null; then
            kill "${pid}"
            echo "[STOP] ${name} (PID ${pid})"
        fi
    done < "${PID_FILE}"
    rm -f "${PID_FILE}"
}

if [ "${1:-}" = "stop" ]; then
    stop_services
    exit 0
fi

# Dừng service cũ nếu còn sót
[ -f "${PID_FILE}" ] && stop_services

echo "=== EAP-TLS — Khởi động 3 service ==="

cd "${SCRIPT_DIR}"

python3 radius_api.py      > "${LOG_DIR}/radius_api.log"      2>&1 &
echo "radius_api $!" >> "${PID_FILE}"
echo "[START] radius_api.py      → port 5000  (log: logs/radius_api.log)"

python3 it_portal.py       > "${LOG_DIR}/it_portal.log"       2>&1 &
echo "it_portal $!" >> "${PID_FILE}"
echo "[START] it_portal.py       → port 8080  (log: logs/it_portal.log)"

python3 employee_portal.py > "${LOG_DIR}/employee_portal.log" 2>&1 &
echo "employee_portal $!" >> "${PID_FILE}"
echo "[START] employee_portal.py → port 9090  (log: logs/employee_portal.log)"

echo ""
echo "PIDs lưu tại: ${PID_FILE}"
echo "Dừng tất cả : bash start.sh stop"
echo ""

# Đợi — Ctrl+C sẽ kill hết
trap stop_services INT TERM
wait
