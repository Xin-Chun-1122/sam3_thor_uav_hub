#!/usr/bin/env python3
"""
hub_server.py  —  Hub Dashboard Server（跑在筆電 10.0.0.7）
====================================================
接收 Thor 傳來的告警事件，儲存圖片，並提供 Web Dashboard 瀏覽。

在筆電上執行：
  pip install flask
  python3 hub_server.py

然後開瀏覽器：http://localhost:8080
"""

import json
import os
import time
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, abort

# ── 設定 ──
AUTH_TOKEN  = "CHANGE_ME_TO_A_LONG_RANDOM_TOKEN"   # 與 Thor 端一致
STORE_DIR   = Path("hub_events")                    # 事件存放目錄
HOST        = "0.0.0.0"
PORT        = 8080

app = Flask(__name__)
STORE_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────
# 工具函式
# ─────────────────────────────────────────────

def _location_label(lat, lon) -> str:
    """已知地標座標對照 → 中文地名"""
    try:
        lat, lon = float(lat), float(lon)
        if 24.78 <= lat <= 24.82 and 120.97 <= lon <= 121.02:
            return "國立清華大學，新竹市東區光復路二段"
        if 24.13 <= lat <= 24.16 and 121.27 <= lon <= 121.30:
            return "合歡山，南投縣仁愛鄉"
        if 24.76 <= lat <= 24.80 and 120.97 <= lon <= 121.00:
            return "新竹市光復路區域"
    except Exception:
        pass
    return ""


def _primary_display_label(event_label: str, note: str) -> str:
    """從 Note 中找最高優先等級標籤顯示（fire > smoke > person）"""
    for priority in ["fire", "smoke", "person"]:
        if f"{priority}:" in note:
            return priority
    return event_label


# ─────────────────────────────────────────────

def check_token():
    token = request.headers.get("X-Auth-Token", "")
    if token != AUTH_TOKEN:
        abort(401, description="Unauthorized: invalid token")


# ─────────────────────────────────────────────
# POST /api/v1/alerts  — 接收告警
# ─────────────────────────────────────────────

@app.route("/api/v1/alerts", methods=["POST"])
def receive_alert():
    check_token()

    # 解析 meta
    meta_str = request.form.get("meta", "{}")
    try:
        meta = json.loads(meta_str)
    except json.JSONDecodeError:
        return jsonify({"status": "error", "msg": "meta JSON parse failed"}), 400

    event_id = meta.get("event_id", f"unknown_{int(time.time()*1000)}")

    # 建事件目錄
    ev_dir = STORE_DIR / event_id
    ev_dir.mkdir(parents=True, exist_ok=True)

    # 儲存 meta.json
    (ev_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 儲存圖片
    for field in ("thumb", "evidence"):
        f = request.files.get(field)
        if f:
            f.save(str(ev_dir / f"{field}.webp"))

    print(f"[HUB] ✅ 收到事件  event_id={event_id}  type={meta.get('type')}  "
          f"level={meta.get('level')}  GPS={meta.get('uav_gps')}")

    return jsonify({
        "status": "ok",
        "event_id": event_id,
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
    }), 200


# ─────────────────────────────────────────────
# GET /api/v1/pull_requests  — Reachback（Thor 定期 poll）
# ─────────────────────────────────────────────

@app.route("/api/v1/pull_requests", methods=["GET"])
def pull_requests():
    check_token()
    # 目前回空 list（沒有補件需求）
    return jsonify({"requests": []}), 200


# ─────────────────────────────────────────────
# Web Dashboard
# ─────────────────────────────────────────────

@app.route("/")
def dashboard():
    events = []
    for ev_dir in sorted(STORE_DIR.iterdir(), reverse=True):
        meta_file = ev_dir / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        meta["_id"] = ev_dir.name
        meta["_has_thumb"]    = (ev_dir / "thumb.webp").exists()
        meta["_has_evidence"] = (ev_dir / "evidence.webp").exists()
        events.append(meta)

    # 簡單 HTML Dashboard
    cards_html = ""
    for e in events[:50]:   # 最多顯示 50 筆
        gps        = e.get("uav_gps", {})
        level      = e.get("level", "")
        label_type    = e.get("type", "")
        note_text     = e.get("note", "")
        # 標題顯示最高優先等級（fire > smoke > person）
        display_label = _primary_display_label(label_type, note_text)
        # Badge 顏色
        label_color = {"fire": "#e74c3c", "smoke": "#7f8c8d", "person": "#27ae60"}.get(display_label, "#2980b9")
        # Badge 文字
        badge_text = {"suspected": "SUSPECTED", "confirmed": "DETECTION",
                      "tracking":  "DETECTION", "critical":  "CRITICAL"}.get(level, "DETECTION")
        # GPS 顯示（home=備援, fix=實際衛星）
        gps_source = gps.get("source", "")
        gps_note   = " 📍 Home（備援）" if gps_source == "home" else (" ✅ GPS Fix" if gps_source == "fix" else "")
        maps_url   = f"https://maps.google.com/?q={gps.get('lat','')},{gps.get('lon','')}"
        location   = _location_label(gps.get("lat", ""), gps.get("lon", ""))
        loc_html   = f"<br><small style='color:#8ab4d4'>🗺️ {location}</small>" if location else ""
        gps_str    = (f"<a href='{maps_url}' target='_blank' "
                      f"style='color:#5dade2;text-decoration:none'>"
                      f"lat={gps.get('lat','?')} lon={gps.get('lon','?')} alt={gps.get('alt','?')}m"
                      f"</a>{gps_note}{loc_html}")
        thumb_src    = f"/events/{e['_id']}/thumb.webp"    if e["_has_thumb"]    else ""
        evidence_src = f"/events/{e['_id']}/evidence.webp" if e["_has_evidence"] else ""

        cards_html += f"""
        <div class="card">
          <div class="card-header">
            <span class="badge" style="background:{label_color}">{badge_text}</span>
            <strong style="font-size:18px;letter-spacing:1px">{display_label.upper()}</strong>
            <span class="ts">{e.get('timestamp','')}</span>
          </div>
          <div class="card-body">
            {"<div class='evid-wrap'><img src='" + evidence_src + "' onclick=\"window.open(this.src)\" title='點擊開啟原圖'></div>" if evidence_src else "<p style='color:#555;font-style:italic'>（無證據圖）</p>"}
            <table>
              <tr><td>Event ID</td><td><code>{e.get('event_id','')}</code></td></tr>
              <tr><td>Confidence</td><td>{e.get('confidence','')}</td></tr>
              <tr><td>GPS</td><td>{gps_str}</td></tr>
              <tr><td>Camera</td><td>{e.get('camera_id','')}</td></tr>
              <tr><td>ROI</td><td>{e.get('roi','')}</td></tr>
              <tr><td>Note</td><td>{e.get('note','')}</td></tr>
            </table>
          </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="10">
<title>UAV Hub Dashboard</title>
<style>
  body {{ font-family: Arial, sans-serif; background: #1a1a2e; color: #eee; margin: 0; padding: 20px; }}
  h1   {{ color: #e94560; text-align: center; margin-bottom: 6px; }}
  .sub {{ text-align:center; color:#aaa; margin-bottom:24px; font-size:13px; }}
  .card {{ background:#16213e; border:1px solid #0f3460; border-radius:10px;
           margin-bottom:20px; overflow:hidden; }}
  .card-header {{ background:#0f3460; padding:10px 16px; display:flex;
                  align-items:center; gap:10px; flex-wrap:wrap; }}
  .badge {{ padding:3px 10px; border-radius:12px; color:#fff;
            font-size:12px; font-weight:bold; }}
  .ts   {{ font-size:12px; color:#aaa; margin-left:auto; }}
  .card-body {{ padding:14px 16px; }}
  .evid-wrap {{ margin-bottom:14px; text-align:center; }}
  .evid-wrap img {{ width:100%; max-width:960px; border-radius:8px;
                    border:2px solid #0f3460; cursor:pointer;
                    display:block; margin:0 auto; }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  td {{ padding:4px 8px; border-bottom:1px solid #0f3460; }}
  td:first-child {{ color:#aaa; width:110px; }}
  code {{ color:#e94560; font-size:12px; }}
  .count {{ text-align:center; color:#aaa; font-size:13px; margin-bottom:20px; }}
</style>
</head>
<body>
<h1>🚁 UAV Hub Dashboard</h1>
<p class="sub">每 10 秒自動更新 · Thor → 10.0.0.7:8080</p>
<p class="count">共 {len(events)} 筆事件（顯示最新 50 筆）</p>
{"<p style='text-align:center;color:#7f8c8d;margin-top:60px'>尚無事件，等待 Thor 傳入...</p>" if not events else cards_html}
</body>
</html>"""
    return html


@app.route("/events/<event_id>/<filename>")
def serve_event_file(event_id, filename):
    """提供事件圖片靜態存取。"""
    safe_dir = STORE_DIR / event_id
    if not safe_dir.exists():
        abort(404)
    return send_from_directory(str(safe_dir), filename)


# ─────────────────────────────────────────────
# 啟動
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[HUB] 🚀 Hub Server 啟動 → http://{HOST}:{PORT}")
    print(f"[HUB] 事件存放目錄: {STORE_DIR.resolve()}")
    print(f"[HUB] Dashboard  → http://localhost:{PORT}")
    print(f"[HUB] Alert API  → POST http://localhost:{PORT}/api/v1/alerts")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
