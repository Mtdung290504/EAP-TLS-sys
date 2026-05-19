#!/usr/bin/env python3
# employee_portal.py — Service 3 (port 9090)
# Authentication: Google OAuth via authlib
# Toàn bộ mạng nội bộ có thể truy cập

import os
from datetime import datetime

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    abort,
)
from authlib.integrations.flask_client import OAuth

from config import CONFIG
import db

app = Flask(__name__, template_folder="templates/employee")
app.secret_key = CONFIG["EMPLOYEE_SESSION_SECRET"]

# ── Google OAuth setup ────────────────────────────────────────────────────────

oauth = OAuth(app)
google = oauth.register(
    name="google",
    client_id=CONFIG["GOOGLE_CLIENT_ID"],
    client_secret=CONFIG["GOOGLE_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def parse_users_txt() -> dict:
    """Đọc users.txt, trả về dict email → uid."""
    email_to_uid: dict[str, str] = {}
    path = CONFIG["USERS_TXT"]
    if not os.path.exists(path):
        return email_to_uid
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 9:
                continue
            uid, group, cn, country, state, city, org, ou, email = parts[:9]
            email_to_uid[email.lower()] = uid
    return email_to_uid


def days_until(expires_str: str):
    try:
        exp = datetime.strptime(expires_str, "%Y-%m-%d %H:%M:%S")
        delta = exp - datetime.now()
        return max(0, delta.days)
    except Exception:
        return None


def login_required_employee(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("employee_uid"):
            return redirect(url_for("auth_login"))
        return f(*args, **kwargs)

    return decorated


# ── Auth routes ───────────────────────────────────────────────────────────────


@app.route("/auth/login")
def auth_login():
    return render_template("login.html")


@app.route("/auth/google")
def auth_google():
    redirect_uri = CONFIG["GOOGLE_REDIRECT_URI"]
    # Thêm prompt='select_account' để Google luôn hỏi chọn tài khoản,
    # giúp nhân viên có thể đổi tài khoản khác sau khi đăng xuất.
    return google.authorize_redirect(redirect_uri, prompt="select_account")


@app.route("/auth/callback")
def auth_callback():
    try:
        token = google.authorize_access_token()
    except Exception as e:
        flash(f"Xác thực Google thất bại: {e}", "error")
        return redirect(url_for("auth_login"))

    user_info = token.get("userinfo") or google.userinfo()
    email = (user_info.get("email") or "").lower()

    email_map = parse_users_txt()
    uid = email_map.get(email)
    if uid is None:
        return render_template(
            "login.html",
            error="Bạn chưa được đăng ký trong hệ thống. Liên hệ IT để được cấp quyền.",
        )

    # Kiểm tra uid có trong db không
    user = db.get_user(uid)
    if user is None:
        return render_template(
            "login.html",
            error="Tài khoản của bạn chưa được IT kích hoạt trong hệ thống.",
        )

    session["employee_uid"] = uid
    session["employee_email"] = email
    session["employee_name"] = user_info.get("name", uid)
    return redirect(url_for("employee_dashboard"))


@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect(url_for("auth_login"))


# ── Employee Dashboard ────────────────────────────────────────────────────────


@app.route("/employee/dashboard")
@login_required_employee
def employee_dashboard():
    uid = session["employee_uid"]
    user = db.get_user(uid)
    if user is None:
        session.clear()
        return redirect(url_for("auth_login"))

    devices = db.get_devices(uid)
    all_requests = db.get_requests()

    device_rows = []
    for dev_name, dev in devices.items():
        days_left = None
        if dev.get("expires"):
            days_left = days_until(dev["expires"])
        device_rows.append(
            {
                "name": dev_name,
                "status": dev.get("status", ""),
                "issued": dev.get("issued", ""),
                "expires": dev.get("expires", ""),
                "days_left": days_left,
            }
        )

    # Requests của user này
    now = datetime.now()
    my_requests = []
    for req in all_requests.values():
        if req.get("uid") != uid:
            continue
        download_url = None
        token = req.get("download_token")
        token_expires_str = req.get("token_expires")
        if token and token_expires_str:
            try:
                token_expires = datetime.strptime(
                    token_expires_str, "%Y-%m-%d %H:%M:%S"
                )
                if now < token_expires:
                    download_url = url_for("download_cert", token=token, _external=True)
            except ValueError:
                pass
        password_url = (
            url_for("get_password", token=token, _external=True)
            if download_url
            else None
        )
        my_requests.append(
            {
                "device_name": req.get("device_name", ""),
                "status": req.get("status", ""),
                "requested_at": req.get("requested_at", ""),
                "download_url": download_url,
                "password_url": password_url,
            }
        )

    active_count = sum(1 for d in devices.values() if d.get("status") == "active")
    max_devices = user.get("max_devices", 2)
    slots_left = max(0, max_devices - active_count)

    return render_template(
        "dashboard.html",
        uid=uid,
        user=user,
        device_rows=device_rows,
        my_requests=my_requests,
        active_count=active_count,
        max_devices=max_devices,
        slots_left=slots_left,
    )


# ── Employee Request ──────────────────────────────────────────────────────────


@app.route("/employee/request", methods=["GET", "POST"])
@login_required_employee
def employee_request():
    uid = session["employee_uid"]
    user = db.get_user(uid)
    if user is None:
        session.clear()
        return redirect(url_for("auth_login"))

    if request.method == "POST":
        device_name = request.form.get("device_name", "").strip().lower()
        if not device_name:
            flash("Tên thiết bị không được trống.", "error")
            return redirect(url_for("employee_request"))

        data = db.load_db()
        devices = data["devices"].get(uid, {})

        # Validate: chưa vượt max_devices
        active_count = sum(1 for d in devices.values() if d.get("status") == "active")
        max_devices = data["users"].get(uid, {}).get("max_devices", 2)
        if active_count >= max_devices:
            flash(f"Bạn đã đạt giới hạn {max_devices} thiết bị.", "error")
            return redirect(url_for("employee_dashboard"))

        # Validate: device_name chưa tồn tại
        if device_name in devices:
            flash(f"Thiết bị '{device_name}' đã tồn tại.", "error")
            return redirect(url_for("employee_request"))

        # Kiểm tra đã có pending request cho device_name này chưa
        for req in data["requests"].values():
            if (
                req.get("uid") == uid
                and req.get("device_name") == device_name
                and req.get("status") == "pending"
            ):
                flash(
                    f"Đã có yêu cầu đang chờ duyệt cho thiết bị '{device_name}'.",
                    "error",
                )
                return redirect(url_for("employee_dashboard"))

        import uuid

        req_id = f"req_{uuid.uuid4().hex[:8]}"
        data["requests"][req_id] = {
            "uid": uid,
            "device_name": device_name,
            "status": "pending",
            "requested_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "download_token": None,
            "token_expires": None,
        }
        db.save_db(data)

        flash("Yêu cầu đã gửi, chờ IT duyệt.", "success")
        return redirect(url_for("employee_dashboard"))

    return render_template("request.html", user=user, uid=uid)


# ── Download cert (employee side) ────────────────────────────────────────────


def _find_request_by_token(token: str):
    """Tìm request theo download_token, kiểm tra còn hạn. Trả về (req_id, req) hoặc (None, None)."""
    data = db.load_db()
    for req_id, req in data.get("requests", {}).items():
        if req.get("download_token") == token:
            token_expires_str = req.get("token_expires")
            if token_expires_str:
                try:
                    if datetime.now() > datetime.strptime(
                        token_expires_str, "%Y-%m-%d %H:%M:%S"
                    ):
                        abort(410)  # Expired
                except ValueError:
                    abort(500)
            return req_id, req
    abort(404)


@app.route("/download/<token>")
def download_cert(token):
    from flask import send_file

    req_id, req = _find_request_by_token(token)

    uid = req["uid"]
    device_name = req["device_name"]
    p12_path = os.path.join(CONFIG["CLIENTS_DIR"], uid, device_name, "client.p12")

    if not os.path.exists(p12_path):
        abort(404)

    # Xóa token sau khi tải (one-time)
    data = db.load_db()
    if req_id in data["requests"]:
        data["requests"][req_id]["download_token"] = None
        data["requests"][req_id]["token_expires"] = None
    db.save_db(data)

    return send_file(
        p12_path,
        as_attachment=True,
        download_name=f"{uid}_{device_name}.p12",
        mimetype="application/x-pkcs12",
    )


@app.route("/password/<token>")
def get_password(token):
    """Trả về mật khẩu .p12 dạng plain text. Không tiêu thụ token."""
    from flask import Response

    _req_id, req = _find_request_by_token(token)

    uid = req["uid"]
    device_name = req["device_name"]
    pass_path = os.path.join(CONFIG["CLIENTS_DIR"], uid, device_name, "password.txt")

    if not os.path.exists(pass_path):
        abort(404)

    with open(pass_path, "r", encoding="utf-8") as f:
        password = f.read().strip()

    return Response(password, mimetype="text/plain")


# ── Root redirect ─────────────────────────────────────────────────────────────


@app.route("/")
def root():
    return redirect(url_for("employee_dashboard"))


if __name__ == "__main__":
    app.run(host=CONFIG["HOST"], port=CONFIG["EMPLOYEE_PORTAL_PORT"], debug=False)
