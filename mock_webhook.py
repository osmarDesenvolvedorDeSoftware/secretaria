from __future__ import annotations

from flask import Flask, jsonify, request

mock_app = Flask(__name__)


@mock_app.route("/mock/whatsapp", methods=["POST"])
def mock_whatsapp():
    data = request.get_json(silent=True)
    print("📩 Mensagem recebida no mock:", data, flush=True)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    mock_app.run(host="0.0.0.0", port=5006)
