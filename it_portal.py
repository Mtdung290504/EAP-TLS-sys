#!/usr/bin/env python3
# it_portal.py — Service 2 (port 8080)
# Chỉ máy IT truy cập (firewall rule)
# Authentication: session-based (IT_USERNAME + IT_PASSWORD)

import os
import subprocess
import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, send_file, abort)

from config import CONFIG
import db

app = Flask(__name__, template_folder="templates/it")
app.secret_key = CONFIG["IT_SESSION_SECRET"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_users_txt() -> dict:
    """Đọc users.txt, trả về dict uid → {cn, email, group, country, state, city, org, ou}."""
    users = {}
    path = CONFIG["USERS_TXT"]
    if not os.path.exists(path):
        return users
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 9:
                continue
            uid, group, cn, country, state, city, org, ou, email = parts[:9]
            users[uid] = {
                "cn": cn, "email": email, "group": group,
                "country": country, "state": state, "city": city,
                "org": org, "ou": ou,
            }
    return users


def run_manage_certs(*args) -> str:
    """Chạy manage_certs.sh với args, trả về stdout. Raise nếu returncode != 0."""
    cmd = ["bash", CONFIG["MANAGE_CERTS_SH"]] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )
    print(f"[manage_certs] cmd={cmd}")
    print(f"[manage_certs] stdout={result.stdout.strip()}")
    if result.stderr.strip():
        print(f"[manage_certs] stderr={result.stderr.strip()}")
    if result.returncode != 0:
        raise RuntimeError(
            f"manage_certs.sh exited {result.returncode}: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def days_until(expires_str: str) -> int | None:
    """Tính số ngày còn lại đến expires_str (format 'YYYY-MM-DD HH:MM:SS')."""
    try:
        exp = datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S")
        delta = exp - datetime.now()
        return max(0, delta.days)
    except Exception:
        return None


def count_active_devices(uid: str) -> int:
    devices = db.get_devices(uid)
    return sum(1 for d in devices.values() if d.get("status") == "active")


# ── Auth decorator ────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("it_logged_in"):
            return redirect(url_for("it_login"))
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/it/login", methods=["GET", "POST"])
def it_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username == CONFIG["IT_USERNAME"] and password == CONFIG["IT_PASSWORD"]:
            session["it_logged_in"] = True
            return redirect(url_for("it_dashboard"))
        flash("Sai tên đăng nhập hoặc mật khẩu.", "error")
    return render_template("login.html")


@app.route("/it/logout")
def it_logout():
    session.clear()
    return redirect(url_for("it_login"))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/it/dashboard")
@login_required
def it_dashboard():
    data = db.load_db()
    users = data.get("users", {})
    devices_all = data.get("devices", {})
    requests_all = data.get("requests", {})

    total_users = len(users)
    total_active = sum(
        1 for uid_devs in devices_all.values()
        for d in uid_devs.values()
        if d.get("status") == "active"
    )
    pending_requests = sum(
        1 for r in requests_all.values()
        if r.get("status") == "pending"
    )
    warn_days = CONFIG["CERT_WARN_DAYS"]
    expiring_soon = sum(
        1 for uid_devs in devices_all.values()
        for d in uid_devs.values()
        if d.get("status") == "active"
        and d.get("expires")
        and days_until(d["expires"]) is not None
        and days_until(d["expires"]) <= warn_days
    )

    # Build bảng user
    user_rows = []
    for uid, u in users.items():
        devs = devices_all.get(uid, {})
        active_count = sum(1 for d in devs.values() if d.get("status") == "active")
        user_rows.append({
            "uid": uid,
            "cn": u.get("cn", ""),
            "group": u.get("group", ""),
            "vlan": u.get("vlan", ""),
            "active_count": active_count,
            "max_devices": u.get("max_devices", 0),
        })

    return render_template(
        "dashboard.html",
        total_users=total_users,
        total_active=total_active,
        pending_requests=pending_requests,
        expiring_soon=expiring_soon,
        user_rows=user_rows,
    )


# ── Users — Add ───────────────────────────────────────────────────────────────

@app.route("/it/users/add", methods=["GET", "POST"])
@login_required
def it_users_add():
    all_users_txt = parse_users_txt()
    data = db.load_db()
    existing_uids = set(data["users"].keys())
    available = {uid: info for uid, info in all_users_txt.items() if uid not in existing_uids}

    if request.method == "POST":
        uid = request.form.get("uid", "").strip()
        if uid not in all_users_txt:
            flash("UID không hợp lệ.", "error")
            return redirect(url_for("it_users_add"))

        info = all_users_txt[uid]
        group = info["group"]
        default_max = CONFIG["DEFAULT_MAX_DEVICES"].get(group, 2)
        try:
            max_devices = int(request.form.get("max_devices", default_max))
        except ValueError:
            max_devices = default_max

        # VLAN: đọc từ config, không hardcode
        vlan = CONFIG["VLAN_MAP"].get(group, 10)

        data = db.load_db()
        if uid in data["users"]:
            flash("User đã tồn tại trong hệ thống.", "error")
            return redirect(url_for("it_users_add"))

        data["users"][uid] = {
            "cn": info["cn"],
            "email": info["email"],
            "group": group,
            "vlan": vlan,
            "max_devices": max_devices,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        db.save_db(data)
        flash(f"Đã thêm user {uid} ({info['cn']}).", "success")
        return redirect(url_for("it_dashboard"))

    return render_template("user_detail.html",
                           mode="add",
                           available=available,
                           default_max_devices=CONFIG["DEFAULT_MAX_DEVICES"])


# ── Users — Sync from users.txt ───────────────────────────────────────────────

@app.route("/it/users/<uid>/sync", methods=["POST"])
@login_required
def it_sync_user(uid):
    """Đọc lại users.txt, cập nhật group/vlan/max_devices cho user trong db."""
    users_txt = parse_users_txt()
    if uid not in users_txt:
        flash(f"UID '{uid}' không tìm thấy trong users.txt.", "error")
        return redirect(url_for("it_user_detail", uid=uid))

    info = users_txt[uid]
    group = info["group"]
    vlan = CONFIG["VLAN_MAP"].get(group, 10)
    default_max = CONFIG["DEFAULT_MAX_DEVICES"].get(group, 2)

    data = db.load_db()
    if uid not in data["users"]:
        flash("User không tồn tại trong hệ thống.", "error")
        return redirect(url_for("it_dashboard"))

    old_group = data["users"][uid].get("group", "")
    data["users"][uid]["cn"] = info["cn"]
    data["users"][uid]["email"] = info["email"]
    data["users"][uid]["group"] = group
    data["users"][uid]["vlan"] = vlan
    # Chỉ cập nhật max_devices nếu max hiện tại bằng default của nhóm cũ
    if data["users"][uid].get("max_devices") == CONFIG["DEFAULT_MAX_DEVICES"].get(old_group, 2):
        data["users"][uid]["max_devices"] = default_max
    db.save_db(data)

    flash(f"Đã sync user '{uid}': group={group}, vlan={vlan}, max_devices={data['users'][uid]['max_devices']}.", "success")
    return redirect(url_for("it_user_detail", uid=uid))


# ── Users — Detail ────────────────────────────────────────────────────────────

@app.route("/it/users/<uid>")
@login_required
def it_user_detail(uid):
    user = db.get_user(uid)
    if user is None:
        abort(404)
    devices = db.get_devices(uid)

    # Kiểm tra users.txt có thông tin khác không
    users_txt = parse_users_txt()
    txt_info = users_txt.get(uid, {})
    sync_needed = (
        txt_info.get("group") != user.get("group") or
        txt_info.get("cn") != user.get("cn") or
        txt_info.get("email") != user.get("email")
    )

    device_rows = []
    for dev_name, dev in devices.items():
        days_left = None
        if dev.get("expires"):
            days_left = days_until(dev["expires"])
        device_rows.append({
            "name": dev_name,
            "status": dev.get("status", ""),
            "serial": dev.get("serial", ""),
            "issued": dev.get("issued", ""),
            "expires": dev.get("expires", ""),
            "days_left": days_left,
        })

    active_count = sum(1 for d in devices.values() if d.get("status") == "active")
    can_delete = active_count == 0

    return render_template(
        "user_detail.html",
        mode="detail",
        uid=uid,
        user=user,
        device_rows=device_rows,
        active_count=active_count,
        can_delete=can_delete,
        sync_needed=sync_needed,
        txt_info=txt_info,
    )


# ── Users — Add Device ────────────────────────────────────────────────────────

@app.route("/it/users/<uid>/add-device", methods=["POST"])
@login_required
def it_add_device(uid):
    user = db.get_user(uid)
    if user is None:
        abort(404)

    device_name = request.form.get("device_name", "").strip().lower()
    if not device_name:
        flash("Tên thiết bị không được trống.", "error")
        return redirect(url_for("it_user_detail", uid=uid))

    data = db.load_db()
    user_data = data["users"].get(uid)
    devices = data["devices"].get(uid, {})

    # Kiểm tra duplicate
    if device_name in devices:
        flash(f"Thiết bị '{device_name}' đã tồn tại.", "error")
        return redirect(url_for("it_user_detail", uid=uid))

    # Kiểm tra max_devices
    active_count = sum(1 for d in devices.values() if d.get("status") == "active")
    max_devices = user_data.get("max_devices", CONFIG["DEFAULT_MAX_DEVICES"].get(user_data.get("group", "Employee"), 2))
    if active_count >= max_devices:
        flash(f"Đã đạt giới hạn {max_devices} thiết bị active.", "error")
        return redirect(url_for("it_user_detail", uid=uid))

    # Parse users.txt để lấy cert info
    users_txt = parse_users_txt()
    info = users_txt.get(uid, {})
    cn = info.get("cn", user_data.get("cn", uid))
    email = info.get("email", user_data.get("email", ""))
    country = info.get("country", CONFIG["CERT_COUNTRY"])
    state = info.get("state", CONFIG["CERT_STATE"])
    city = info.get("city", CONFIG["CERT_CITY"])
    org = info.get("org", CONFIG["CERT_ORG"])
    ou = info.get("ou", CONFIG["CERT_OU"])

    try:
        stdout = run_manage_certs("issue", uid, device_name, cn, email,
                                  country, state, city, org, ou)
    except RuntimeError as e:
        flash(f"Lỗi khi cấp cert: {e}", "error")
        return redirect(url_for("it_user_detail", uid=uid))

    # Parse SERIAL=
    serial = ""
    for line in stdout.splitlines():
        if line.startswith("SERIAL="):
            serial = line.split("=", 1)[1].strip()

    now = datetime.now()
    expires = now + timedelta(days=CONFIG["CERT_VALIDITY_DAYS"])

    import uuid
    token = str(uuid.uuid4())
    token_expires = now + timedelta(minutes=CONFIG["DOWNLOAD_LINK_EXPIRE_MINUTES"])

    data = db.load_db()
    if uid not in data["devices"]:
        data["devices"][uid] = {}
    data["devices"][uid][device_name] = {
        "status": "active",
        "serial": serial,
        "issued": now.strftime("%Y-%m-%d %H:%M:%S"),
        "expires": expires.strftime("%Y-%m-%d %H:%M:%S"),
        "download_token": token,
        "token_expires": token_expires.strftime("%Y-%m-%d %H:%M:%S"),
    }
    db.save_db(data)

    flash(f"Đã cấp cert cho thiết bị '{device_name}'. Nhân viên có thể tải về trên portal (hiệu lực {CONFIG['DOWNLOAD_LINK_EXPIRE_MINUTES']} phút).", "success")
    return redirect(url_for("it_user_detail", uid=uid))


# ── Users — Revoke Device ─────────────────────────────────────────────────────

@app.route("/it/users/<uid>/revoke-device", methods=["POST"])
@login_required
def it_revoke_device(uid):
    device_name = request.form.get("device_name", "").strip()
    reason = request.form.get("reason", "cessationOfOperation").strip()

    if not device_name:
        flash("Tên thiết bị không hợp lệ.", "error")
        return redirect(url_for("it_user_detail", uid=uid))

    try:
        run_manage_certs("revoke", uid, device_name, reason)
    except RuntimeError as e:
        flash(f"Lỗi khi thu hồi cert: {e}", "error")
        return redirect(url_for("it_user_detail", uid=uid))

    data = db.load_db()
    if uid in data["devices"] and device_name in data["devices"][uid]:
        data["devices"][uid][device_name]["status"] = "revoked"
        data["devices"][uid][device_name]["revoked_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data["devices"][uid][device_name]["reason"] = reason
    db.save_db(data)

    flash(f"Đã thu hồi cert thiết bị '{device_name}'.", "success")
    return redirect(url_for("it_user_detail", uid=uid))


# ── Users — Delete ────────────────────────────────────────────────────────────

@app.route("/it/users/<uid>/delete", methods=["POST"])
@login_required
def it_delete_user(uid):
    devices = db.get_devices(uid)
    active_count = sum(1 for d in devices.values() if d.get("status") == "active")
    if active_count > 0:
        flash(f"Không thể xóa user còn {active_count} thiết bị active.", "error")
        return redirect(url_for("it_user_detail", uid=uid))

    data = db.load_db()
    data["users"].pop(uid, None)
    data["devices"].pop(uid, None)
    # Xóa pending requests của user
    data["requests"] = {
        req_id: req for req_id, req in data["requests"].items()
        if req.get("uid") != uid
    }
    db.save_db(data)

    flash(f"Đã xóa user {uid}.", "success")
    return redirect(url_for("it_dashboard"))


# ── Requests ──────────────────────────────────────────────────────────────────

@app.route("/it/requests")
@login_required
def it_requests():
    data = db.load_db()
    requests_all = data.get("requests", {})
    users = data.get("users", {})

    pending = [
        {
            "req_id": req_id,
            "uid": req.get("uid", ""),
            "cn": users.get(req.get("uid", ""), {}).get("cn", req.get("uid", "")),
            "device_name": req.get("device_name", ""),
            "requested_at": req.get("requested_at", ""),
        }
        for req_id, req in requests_all.items()
        if req.get("status") == "pending"
    ]

    return render_template("requests.html", pending=pending)


@app.route("/it/requests/<req_id>/approve", methods=["POST"])
@login_required
def it_approve_request(req_id):
    data = db.load_db()
    req = data["requests"].get(req_id)
    if req is None or req.get("status") != "pending":
        flash("Yêu cầu không tồn tại hoặc đã xử lý.", "error")
        return redirect(url_for("it_requests"))

    uid = req["uid"]
    device_name = req["device_name"]
    user_data = data["users"].get(uid)

    if user_data is None:
        flash(f"User {uid} không tồn tại trong hệ thống.", "error")
        return redirect(url_for("it_requests"))

    # Kiểm tra max_devices
    devices = data["devices"].get(uid, {})
    active_count = sum(1 for d in devices.values() if d.get("status") == "active")
    max_devices = user_data.get("max_devices", 2)
    if active_count >= max_devices:
        flash(f"User {uid} đã đạt giới hạn thiết bị.", "error")
        return redirect(url_for("it_requests"))

    users_txt = parse_users_txt()
    info = users_txt.get(uid, {})
    cn = info.get("cn", user_data.get("cn", uid))
    email = info.get("email", user_data.get("email", ""))
    country = info.get("country", CONFIG["CERT_COUNTRY"])
    state = info.get("state", CONFIG["CERT_STATE"])
    city = info.get("city", CONFIG["CERT_CITY"])
    org = info.get("org", CONFIG["CERT_ORG"])
    ou = info.get("ou", CONFIG["CERT_OU"])

    try:
        stdout = run_manage_certs("issue", uid, device_name, cn, email,
                                  country, state, city, org, ou)
    except RuntimeError as e:
        flash(f"Lỗi khi cấp cert: {e}", "error")
        return redirect(url_for("it_requests"))

    serial = ""
    for line in stdout.splitlines():
        if line.startswith("SERIAL="):
            serial = line.split("=", 1)[1].strip()

    now = datetime.now()
    expires = now + timedelta(days=CONFIG["CERT_VALIDITY_DAYS"])

    # Tạo download token
    token = str(uuid.uuid4())
    token_expires = now + timedelta(minutes=CONFIG["DOWNLOAD_LINK_EXPIRE_MINUTES"])

    data = db.load_db()
    if uid not in data["devices"]:
        data["devices"][uid] = {}
    data["devices"][uid][device_name] = {
        "status": "active",
        "serial": serial,
        "issued": now.strftime("%Y-%m-%d %H:%M:%S"),
        "expires": expires.strftime("%Y-%m-%d %H:%M:%S"),
        "download_token": token,
        "token_expires": token_expires.strftime("%Y-%m-%d %H:%M:%S"),
    }
    data["requests"][req_id]["status"] = "approved"
    db.save_db(data)

    flash(f"Đã duyệt. Nhân viên có thể tải cert và mật khẩu trên Employee Portal (hiệu lực {CONFIG['DOWNLOAD_LINK_EXPIRE_MINUTES']} phút).", "success")
    return redirect(url_for("it_requests"))


@app.route("/it/requests/<req_id>/reject", methods=["POST"])
@login_required
def it_reject_request(req_id):
    data = db.load_db()
    if req_id not in data["requests"]:
        flash("Yêu cầu không tồn tại.", "error")
        return redirect(url_for("it_requests"))
    data["requests"][req_id]["status"] = "rejected"
    db.save_db(data)
    flash("Đã từ chối yêu cầu.", "success")
    return redirect(url_for("it_requests"))


# ── Root redirect ─────────────────────────────────────────────────────────────

@app.route("/")
def root():
    return redirect(url_for("it_dashboard"))


if __name__ == "__main__":
    app.run(
        host=CONFIG["HOST"],
        port=CONFIG["IT_PORTAL_PORT"],
        debug=False
    )
