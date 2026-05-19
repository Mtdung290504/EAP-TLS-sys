#!/usr/bin/env python3
# radius_api.py — Service 1 (port 5000)
# Chỉ RADIUS server gọi — không expose ra ngoài
# Routes: GET /vlan, GET /health

from flask import Flask, request, jsonify, Response
from config import CONFIG
import db

app = Flask(__name__)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/vlan", methods=["GET"])
def vlan():
    uid_param = request.args.get("uid", "").strip()
    if not uid_param:
        return Response("Missing uid parameter", status=400, mimetype="text/plain")

    # CN format: userID_deviceName
    if "_" not in uid_param:
        return Response("Invalid uid format, expected userID_deviceName", status=400, mimetype="text/plain")

    # Tách từ phải để hỗ trợ userID có dấu _
    parts = uid_param.rsplit("_", 1)
    user_id, device_name = parts[0], parts[1]

    user = db.get_user(user_id)
    if user is None:
        return Response("User not found", status=404, mimetype="text/plain")

    devices = db.get_devices(user_id)
    device = devices.get(device_name)
    if device is None:
        return Response("Device not found", status=404, mimetype="text/plain")

    if device.get("status") != "active":
        return Response("Device is not active", status=403, mimetype="text/plain")

    vlan_id = user.get("vlan")
    if vlan_id is None:
        return Response("VLAN not configured for user", status=500, mimetype="text/plain")

    return Response(str(vlan_id), status=200, mimetype="text/plain")


if __name__ == "__main__":
    app.run(
        host=CONFIG["HOST"],
        port=CONFIG["RADIUS_API_PORT"],
        debug=False
    )
