#!/usr/bin/env bash
# manage_certs.sh — Được gọi bởi it_portal.py qua subprocess.
# Không chạy standalone.
#
# Cú pháp:
#   ./manage_certs.sh issue  <userID> <deviceName> <cn> <email> \
#                            <country> <state> <city> <org> <ou>
#   ./manage_certs.sh revoke <userID> <deviceName> [reason]
#   ./manage_certs.sh gencrl

set -eo pipefail

# ── Resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_DIR="${CA_DIR:-${SCRIPT_DIR}/CA-data}"
CLIENTS_DIR="${CLIENTS_DIR:-${CA_DIR}/clients}"
OPENSSL_CNF="$(realpath "${OPENSSL_CNF:-${CA_DIR}/openssl.cnf}")"

# openssl ca cần CWD = CA_DIR để các path tương đối trong openssl.cnf hoạt động
cd "${CA_DIR}"

# Đảm bảo các file/thư mục cần thiết tồn tại
mkdir -p newcerts
[ -f index.txt ]  || touch index.txt
[ -f index.txt.attr ] || echo "unique_subject = yes" > index.txt.attr
if [ ! -f crlnumber ]; then
    echo "01" > crlnumber
fi
if [ ! -f ca.srl ]; then
    echo "01" > ca.srl
fi

# ── Sub-commands ──────────────────────────────────────────────────────────────

cmd_issue() {
    local userID="$1"
    local deviceName="$2"
    local cn="$3"
    local email="$4"
    local country="$5"
    local state="$6"
    local city="$7"
    local org="$8"
    local ou="$9"

    if [ $# -lt 9 ]; then
        echo "ERROR: issue requires 9 arguments" >&2
        exit 1
    fi

    local cert_cn="${userID}_${deviceName}"
    local out_dir="${CLIENTS_DIR}/${userID}/${deviceName}"
    mkdir -p "${out_dir}"

    local key_file="${out_dir}/client.key"
    local csr_file="${out_dir}/client.csr"
    local crt_file="${out_dir}/client.crt"
    local p12_file="${out_dir}/client.p12"
    local pass_file="${out_dir}/password.txt"

    # Tạo passphrase ngẫu nhiên cho .p12
    local p12_pass
    p12_pass="$(openssl rand -hex 16)"
    echo "${p12_pass}" > "${pass_file}"
    chmod 600 "${pass_file}"

    # Tạo private key
    openssl genrsa -out "${key_file}" 2048 2>/dev/null

    # Tạo CSR với subject đầy đủ
    openssl req -new \
        -key "${key_file}" \
        -out "${csr_file}" \
        -subj "/C=${country}/ST=${state}/L=${city}/O=${org}/OU=${ou}/CN=${cert_cn}/emailAddress=${email}" \
        -config "${OPENSSL_CNF}" 2>/dev/null

    # Ký cert bằng CA
    openssl ca \
        -config "${OPENSSL_CNF}" \
        -extensions v3_client \
        -days 825 \
        -notext \
        -batch \
        -in "${csr_file}" \
        -out "${crt_file}" 2>/dev/null

    # Xóa CSR
    rm -f "${csr_file}"

    # Đóng gói .p12
    openssl pkcs12 -export \
        -in "${crt_file}" \
        -inkey "${key_file}" \
        -certfile "${CA_DIR}/ca.crt" \
        -out "${p12_file}" \
        -passout "pass:${p12_pass}" 2>/dev/null

    # In serial để Python parse
    local serial
    serial="$(openssl x509 -in "${crt_file}" -noout -serial 2>/dev/null | cut -d= -f2)"
    echo "SERIAL=${serial}"
}

cmd_revoke() {
    local userID="$1"
    local deviceName="$2"
    local reason="${3:-cessationOfOperation}"

    local cert_dir="${CLIENTS_DIR}/${userID}/${deviceName}"
    local crt_file="${cert_dir}/client.crt"

    if [ ! -f "${crt_file}" ]; then
        echo "ERROR: cert not found: ${crt_file}" >&2
        exit 1
    fi

    # Thu hồi cert
    openssl ca \
        -config "${OPENSSL_CNF}" \
        -revoke "${crt_file}" \
        -crl_reason "${reason}" \
        -batch 2>/dev/null

    # Archive vào revoked/
    local ts
    ts="$(date +%Y%m%d_%H%M%S)"
    local revoked_dir="${CLIENTS_DIR}/revoked/${userID}_${deviceName}_${ts}"
    mkdir -p "${revoked_dir}"
    mv "${cert_dir}"/* "${revoked_dir}/"
    rmdir "${cert_dir}" 2>/dev/null || true
    # Xóa thư mục user nếu trống
    rmdir "${CLIENTS_DIR}/${userID}" 2>/dev/null || true

    echo "REVOKED=${userID}_${deviceName}_${ts}"

    # Tái tạo CRL sau khi revoke
    cmd_gencrl
}

cmd_gencrl() {
    openssl ca \
        -config "${OPENSSL_CNF}" \
        -gencrl \
        -out "${CA_DIR}/crl.pem" \
        -batch 2>/dev/null
    echo "CRL_UPDATED=1"
}

# ── Dispatch ──────────────────────────────────────────────────────────────────
case "${1:-}" in
    issue)
        shift
        cmd_issue "$@"
        ;;
    revoke)
        shift
        cmd_revoke "$@"
        ;;
    gencrl)
        cmd_gencrl
        ;;
    *)
        echo "Usage: $0 {issue|revoke|gencrl} ..." >&2
        exit 1
        ;;
esac
