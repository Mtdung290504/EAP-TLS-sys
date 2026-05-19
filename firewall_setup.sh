#!/usr/bin/env bash
# firewall_setup.sh — Chạy 1 lần sau khi deploy để setup network isolation
# Đọc IP từ tham số, không hardcode
#
# Cú pháp:
#   ./firewall_setup.sh <RADIUS_IP> <IT_MACHINE_IP> <INTERNAL_SUBNET>
# Ví dụ:
#   ./firewall_setup.sh 192.168.153.128 192.168.153.1 192.168.153.0/24

set -eo pipefail

if [ $# -ne 3 ]; then
    echo "Usage: $0 <RADIUS_IP> <IT_MACHINE_IP> <INTERNAL_SUBNET>" >&2
    exit 1
fi

RADIUS_IP="$1"
IT_MACHINE_IP="$2"
INTERNAL_SUBNET="$3"

echo "=== EAP-TLS Firewall Setup ==="
echo "RADIUS_IP      : ${RADIUS_IP}"
echo "IT_MACHINE_IP  : ${IT_MACHINE_IP}"
echo "INTERNAL_SUBNET: ${INTERNAL_SUBNET}"
echo ""

# ── Port 5000 — RADIUS API ────────────────────────────────────────────────────
# Chỉ RADIUS_IP được kết nối, DROP tất cả còn lại
iptables -A INPUT -p tcp --dport 5000 -s "${RADIUS_IP}" -j ACCEPT
iptables -A INPUT -p tcp --dport 5000 -j DROP
echo "[OK] Port 5000: ACCEPT from ${RADIUS_IP}, DROP others"

# ── Port 8080 — IT Portal ─────────────────────────────────────────────────────
# Chỉ IT_MACHINE_IP được kết nối, DROP tất cả còn lại
iptables -A INPUT -p tcp --dport 8080 -s "${IT_MACHINE_IP}" -j ACCEPT
iptables -A INPUT -p tcp --dport 8080 -j DROP
echo "[OK] Port 8080: ACCEPT from ${IT_MACHINE_IP}, DROP others"

# ── Port 9090 — Employee Portal ───────────────────────────────────────────────
# ACCEPT từ INTERNAL_SUBNET
iptables -A INPUT -p tcp --dport 9090 -s "${INTERNAL_SUBNET}" -j ACCEPT
echo "[OK] Port 9090: ACCEPT from ${INTERNAL_SUBNET}"

echo ""
echo "=== Hiện tại INPUT chain ==="
iptables -L INPUT -n --line-numbers

# ── Xóa rules (uncomment khi debug) ──────────────────────────────────────────
# iptables -D INPUT -p tcp --dport 5000 -s "${RADIUS_IP}" -j ACCEPT
# iptables -D INPUT -p tcp --dport 5000 -j DROP
# iptables -D INPUT -p tcp --dport 8080 -s "${IT_MACHINE_IP}" -j ACCEPT
# iptables -D INPUT -p tcp --dport 8080 -j DROP
# iptables -D INPUT -p tcp --dport 9090 -s "${INTERNAL_SUBNET}" -j ACCEPT
