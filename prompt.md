# EAP-TLS Certificate Management System

## Tổng quan kiến trúc

3 service độc lập, 3 port, cách ly network:

```
Port 5000 — RADIUS API       — chỉ RADIUS server gọi
Port 8080 — IT Portal        — chỉ máy IT truy cập (firewall rule)
Port 9090 — Employee Portal  — toàn bộ mạng nội bộ
```

Mỗi service chạy độc lập, không import chéo nhau.
Chỉ share: config.py và db.json (qua file lock).

## Cấu trúc thư mục

```
eap-tls-lab/
├── config.py                     # Tất cả config tập trung
├── radius_api.py                 # Service 1 — port 5000
├── it_portal.py                  # Service 2 — port 8080
├── employee_portal.py            # Service 3 — port 9090
├── db.py                         # Helper đọc/ghi db.json (file lock)
├── manage_certs.sh               # Bash — được gọi qua subprocess
├── firewall_setup.sh             # Script setup iptables rules
├── users.txt                     # Source of truth cho user
├── CA-data/
│   ├── ca.crt
│   ├── ca.key
│   ├── openssl.cnf
│   ├── db.json                   # State toàn hệ thống
│   └── clients/
│       ├── <userID>/
│       │   └── <deviceName>/     # Unique per user+device
│       │       ├── client.key
│       │       ├── client.crt
│       │       ├── client.p12
│       │       └── password.txt
│       └── revoked/
│           └── <userID>_<deviceName>_<timestamp>/
└── templates/
    ├── it/
    │   ├── login.html
    │   ├── dashboard.html
    │   ├── user_detail.html
    │   └── requests.html
    └── employee/
        ├── login.html
        ├── dashboard.html
        └── request.html
```

## config.py

```python
# config.py
# Tất cả giá trị cần thay khi đổi môi trường đặt ở đây
# Các file khác chỉ import từ đây, không hardcode bất cứ thứ gì

CONFIG = {
    # Network
    "HOST": "0.0.0.0",
    "KALI_IP": "192.168.153.130",
    "RADIUS_API_PORT": 5000,
    "IT_PORTAL_PORT": 8080,
    "EMPLOYEE_PORTAL_PORT": 9090,

    # Paths
    "CA_DIR": "./CA-data",
    "CLIENTS_DIR": "./CA-data/clients",
    "DB_PATH": "./CA-data/db.json",
    "USERS_TXT": "./users.txt",
    "OPENSSL_CNF": "./CA-data/openssl.cnf",
    "MANAGE_CERTS_SH": "./manage_certs.sh",

    # IT Portal auth (hardcode, không dùng Google)
    "IT_USERNAME": "admin",
    "IT_PASSWORD": "changeme",
    "IT_SESSION_SECRET": "changeme-secret-key",

    # Google OAuth (Employee Portal)
    "GOOGLE_CLIENT_ID": "REPLACE_ME",
    "GOOGLE_CLIENT_SECRET": "REPLACE_ME",
    "GOOGLE_REDIRECT_URI": "http://192.168.153.130:9090/auth/callback",
    "EMPLOYEE_SESSION_SECRET": "changeme-employee-secret",

    # Policy
    "DEFAULT_MAX_DEVICES": {
        "Employee": 2,
        "Boss": 3
    },

    # Download token
    "DOWNLOAD_LINK_EXPIRE_MINUTES": 60,

    # Cert
    "CERT_VALIDITY_DAYS": 825,
    "CERT_WARN_DAYS": 30,

    # Org defaults cho cert (đọc từ users.txt, đây là fallback)
    "CERT_COUNTRY": "VN",
    "CERT_STATE": "Da Nang",
    "CERT_CITY": "Da Nang City",
    "CERT_ORG": "VKU-ATMKD",
    "CERT_OU": "Student",
}
```

## db.json schema

```json
{
	"users": {
		"22NS007": {
			"cn": "Mai Tien Dung",
			"email": "dungmt.22ns@vku.udn.vn",
			"group": "Employee",
			"vlan": 10,
			"max_devices": 2,
			"created_at": "2026-05-18 04:00:00"
		}
	},
	"devices": {
		"22NS007": {
			"laptop": {
				"status": "active",
				"serial": "ABC123",
				"issued": "2026-05-18 04:00:00",
				"expires": "2028-10-19 04:00:00"
			},
			"phone": {
				"status": "revoked",
				"serial": "DEF456",
				"issued": "2026-05-18 04:00:00",
				"revoked_at": "2026-06-01 00:00:00",
				"reason": "cessationOfOperation"
			}
		}
	},
	"requests": {
		"req_001": {
			"uid": "22NS007",
			"device_name": "phone",
			"status": "pending",
			"requested_at": "2026-05-18 10:00:00",
			"download_token": null,
			"token_expires": null
		}
	}
}
```

## db.py — helper đọc/ghi db.json

```python
# Yêu cầu:
# - Dùng fcntl.flock để lock file khi đọc/ghi
# - Không cache bất kỳ thứ gì, đọc file mỗi lần gọi
# - Expose các hàm: load_db(), save_db(data),
#   get_user(uid), get_devices(uid), get_requests()
# - Mọi file khác chỉ import db.py, không tự mở db.json
```

## manage_certs.sh

Được gọi bởi it_portal.py qua subprocess. Không chạy standalone.

```bash
# Cú pháp:
./manage_certs.sh issue  <userID> <deviceName> <cn> <email> \
                         <country> <state> <city> <org> <ou>
./manage_certs.sh revoke <userID> <deviceName> [reason]
./manage_certs.sh gencrl

# Yêu cầu:
# - set -eo pipefail (không dùng -u)
# - Tất cả path đọc từ biến môi trường hoặc tham số, không hardcode
# - CN của cert = userID_deviceName
# - Thư mục cert: CA-data/clients/<userID>/<deviceName>/
# - Sau issue: in ra stdout dòng "SERIAL=<hex>" để Python parse
# - Sau revoke: archive vào CA-data/clients/revoked/<userID>_<deviceName>_<timestamp>/
# - Sau revoke: tự chạy gencrl
# - Xóa .csr sau khi ký xong
# - openssl.cnf cần dùng absolute path (dùng realpath)
# - Đăng ký cert vào index.txt để openssl ca revoke được
```

## radius_api.py — Service 1 (port 5000)

```python
# Yêu cầu:
# - Import config từ config.py
# - Không cache, đọc db.json mỗi request qua db.py
# - Không có route nào khác ngoài /vlan và /health

# GET /vlan?uid=22NS007_laptop
# Logic:
#   1. Tách CN: userID="22NS007", device="laptop"
#   2. Kiểm tra user tồn tại
#   3. Kiểm tra device status == "active"
#   4. Trả về VLAN số nguyên dạng text/plain
#   5. 403 text/plain nếu revoked/inactive
#   6. 404 text/plain nếu không tìm thấy

# GET /health → {"status": "ok"}
```

## it_portal.py — Service 2 (port 8080)

```python
# Authentication: session-based
# Login bằng IT_USERNAME + IT_PASSWORD từ config
# Mọi route /it/* đều check session, redirect /it/login nếu chưa auth

# Routes:
# GET  /it/login
# POST /it/login
# GET  /it/logout
# GET  /it/dashboard
#      — Stats: tổng user, tổng device active, pending requests, sắp hết hạn
#      — Bảng user: uid | cn | group | vlan | devices active | max_devices | actions
# GET  /it/users/add
#      — Dropdown chọn uid từ users.txt (chỉ hiện uid chưa có trong db)
#      — Sau khi chọn: auto-fill cn, email, group từ users.txt
#      — Field max_devices: mặc định theo group, IT override được
# POST /it/users/add
#      — Thêm user vào db.json, chưa tạo cert gì cả
# GET  /it/users/<uid>
#      — Thông tin user
#      — Bảng devices: deviceName | status | serial | issued | expires | days_left | actions
#      — Form thêm device: chỉ nhập deviceName, submit → gọi manage_certs.sh issue
#      — Nút revoke từng device
#      — Nút xóa user (disabled nếu còn device active)
# POST /it/users/<uid>/add-device
#      — Kiểm tra chưa vượt max_devices
#      — Gọi manage_certs.sh issue qua subprocess
#      — Parse SERIAL= từ stdout
#      — Cập nhật db.json
# POST /it/users/<uid>/revoke-device
#      — Gọi manage_certs.sh revoke qua subprocess
#      — Cập nhật db.json
# POST /it/users/<uid>/delete
#      — Từ chối nếu còn device active (flash error)
#      — Xóa user khỏi db.json
# GET  /it/requests
#      — Bảng pending requests: uid | device_name | requested_at | actions
# POST /it/requests/<req_id>/approve
#      — Gọi manage_certs.sh issue
#      — Tạo download token (uuid, lưu vào db.json kèm expires)
#      — Trả về link download cho IT copy gửi nhân viên
# POST /it/requests/<req_id>/reject
#      — Cập nhật status = "rejected" trong db.json
# GET  /download/<token>
#      — Kiểm tra token hợp lệ và chưa hết hạn
#      — Trả về file .p12 (send_file, as_attachment)
#      — Xóa token sau khi download (1 lần dùng)
```

## employee_portal.py — Service 3 (port 9090)

```python
# Authentication: Google OAuth
# Sau khi Google callback: kiểm tra email có trong users.txt không
# Nếu không có → hiện trang "Bạn chưa được đăng ký trong hệ thống"
# Nếu có → tạo session với uid tương ứng

# Routes:
# GET  /auth/login      — redirect Google OAuth consent screen
# GET  /auth/callback   — xử lý Google callback, tạo session
# GET  /auth/logout
# GET  /employee/dashboard
#      — Thông tin user: cn, group, vlan
#      — Bảng devices của mình: deviceName | status | issued | expires
#      — Danh sách requests đã gửi: device_name | status | requested_at
#      — Số device còn được cấp thêm (max_devices - active_count)
# POST /employee/request
#      — Form: nhập device_name
#      — Validate: chưa vượt max_devices, device_name chưa tồn tại
#      — Tạo request mới trong db.json với status="pending"
#      — Redirect dashboard với flash "Yêu cầu đã gửi, chờ IT duyệt"
```

## firewall_setup.sh

```bash
#!/usr/bin/env bash
# Chạy 1 lần sau khi deploy để setup network isolation
# Đọc IP từ tham số, không hardcode

# Cú pháp:
# ./firewall_setup.sh <RADIUS_IP> <IT_MACHINE_IP> <INTERNAL_SUBNET>
# Ví dụ:
# ./firewall_setup.sh 192.168.153.128 192.168.153.1 192.168.153.0/24

# Rules:
# - Port 5000: chỉ RADIUS_IP được kết nối, DROP tất cả còn lại
# - Port 8080: chỉ IT_MACHINE_IP được kết nối, DROP tất cả còn lại
# - Port 9090: ACCEPT từ INTERNAL_SUBNET
# - In ra các rule đã áp dụng để verify
# - Cuối file: lệnh để xóa rules (comment lại, dùng khi debug)
```

## UI Requirements

- CSS thuần, không framework
- Layout IT portal: sidebar trái cố định + content phải
- Sidebar: logo, menu items (Dashboard, Users, Requests, Logout)
- Màu: trắng nền, xám border, đen text, không màu mè
- Bảng: thead xám nhạt, border rõ ràng, hover highlight nhẹ
- Form: label trên input, full width, submit button phải
- Flash message: dải ngang trên content, xanh lá success / đỏ error
- Empty state: text căn giữa khi bảng không có dữ liệu
- Không animation, không JS framework, không icon library

## Yêu cầu kỹ thuật

1. **Không cache db.json** — mọi đọc/ghi đều qua db.py với file lock
2. **Subprocess** — capture stdout + stderr, log ra console, raise exception nếu returncode != 0
3. **Thread safety** — fcntl.flock LOCK_EX khi ghi, LOCK_SH khi đọc
4. **Tất cả config** — chỉ nằm trong config.py, không hardcode ở file khác
5. **Error handling** — try/except quanh subprocess và file I/O, flash message lỗi rõ ràng
6. **3 service độc lập** — mỗi file chạy được standalone với `python3 <file>.py`
7. **Google OAuth** — dùng thư viện `requests-oauthlib` hoặc `authlib`, không tự implement flow
8. **users.txt parsing** — bỏ qua dòng comment (#) và dòng trống, trim whitespace

## users.txt format

```
# UserID,Group,CommonName,Country,State,City,Org,OrgUnit,Email
22NS007,Employee,Mai Tien Dung,VN,Da Nang,Da Nang City,VKU-ATMKD,Student,dungmt.22ns@vku.udn.vn
22NS008,Employee,Nguyen Huu Dung,VN,Da Nang,Da Nang City,VKU-ATMKD,Student,dungnh.22ns@vku.udn.vn
0003,Boss,Hoang Van Em,VN,Da Nang,Da Nang City,VKU-ATMKD,Lecturer,em.hv@gmail.com
0004,Boss,Vo Thi Phuong,VN,Ha Noi,Ha Noi City,VKU-ATMKD,Lecturer,phuong.vt@gmail.com
```

## Thứ tự implement

1. `config.py`
2. `db.py`
3. `manage_certs.sh`
4. `radius_api.py`
5. `it_portal.py` (không có Google Auth)
6. `employee_portal.py` + Google Auth
7. `firewall_setup.sh`

## Không làm

- Không database (SQLite, Postgres) — chỉ JSON file
- Không Docker
- Không HTTPS
- Không bulk import device
- Không pagination
- Không cache bất kỳ thứ gì
