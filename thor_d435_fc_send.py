#!/usr/bin/env python3
"""
thor_d435_fc_send.py
====================
抓取 D435 影像 + 讀取 Pixhawk MAVLink GPS/高度 + 呼叫 thor_send_alert.send_alert()
把事件（thumb/evidence/meta）回傳到筆電 Hub。

使用方式（單次送一筆）：
  python3 thor_d435_fc_send.py --once --d435-serial <SERIAL> --camera-id d435_a --mavlink /dev/ttyACM1

連續送（測試用，每2秒送一次）：
  python3 thor_d435_fc_send.py --d435-serial <SERIAL> --camera-id d435_a --mavlink /dev/ttyACM1 --interval 2
"""

import argparse
import time
import numpy as np
from pymavlink import mavutil

# 直接復用你現成的事件回傳封包與 POST 實作
import thor_send_alert as sender
from thor_send_alert import send_alert


def open_realsense_color(serial: str | None, width=640, height=480, fps=15):
    """
    使用 pyrealsense2 直接開 D435 RGB (BGR8)。
    你有雙相機時一定要指定 serial，避免抓錯。
    """
    import pyrealsense2 as rs

    pipeline = rs.pipeline()
    cfg = rs.config()
    if serial:
        cfg.enable_device(serial)

    cfg.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
    profile = pipeline.start(cfg)
    return pipeline, profile


def grab_color_frame_bgr(pipeline, warmup_frames=5):
    """
    取得一張 BGR 影像 + unix time (t_capture)。
    warmup 幾幀讓曝光穩定。
    """
    for _ in range(warmup_frames):
        pipeline.wait_for_frames()

    frames = pipeline.wait_for_frames()
    cf = frames.get_color_frame()
    if not cf:
        return None, None

    img = np.asanyarray(cf.get_data())  # BGR uint8
    t_capture = time.time()             # 用系統時間（論文做延遲分解最方便）
    return img, t_capture


def connect_mavlink(dev: str, baud: int, timeout: float = 5.0):
    """
    建立 MAVLink 連線並等待 heartbeat。
    dev 例：
      /dev/ttyACM1
      /dev/ttyACM0
      udp:127.0.0.1:14550
    """
    m = mavutil.mavlink_connection(dev, baud=baud)
    hb = m.wait_heartbeat(timeout=timeout)
    if hb is None:
        raise RuntimeError(f"No heartbeat on {dev} (baud={baud})")
    return m


def read_gps(m, timeout: float = 2.0):
    """
    讀 GPS 位置資訊。
    優先 GLOBAL_POSITION_INT，其次 GPS_RAW_INT。
    室內沒 fix 時 lat/lon 可能是 0（正常）。
    """
    msg = m.recv_match(type=["GLOBAL_POSITION_INT", "GPS_RAW_INT"], blocking=True, timeout=timeout)
    if msg is None:
        # 沒拿到就回 0
        return {"lat": 0.0, "lon": 0.0, "alt": 0.0, "fix_type": None}

    t = msg.get_type()
    if t == "GLOBAL_POSITION_INT":
        lat = msg.lat / 1e7
        lon = msg.lon / 1e7
        alt = msg.alt / 1000.0  # mm -> m
        return {"lat": float(lat), "lon": float(lon), "alt": float(alt), "fix_type": None}

    # GPS_RAW_INT
    lat = msg.lat / 1e7
    lon = msg.lon / 1e7
    alt = msg.alt / 1000.0
    fix_type = getattr(msg, "fix_type", None)
    return {"lat": float(lat), "lon": float(lon), "alt": float(alt), "fix_type": fix_type}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub-url", default="http://10.0.0.7:8080/api/v1/alerts")
    ap.add_argument("--token", default="CHANGE_ME_TO_A_LONG_RANDOM_TOKEN")
    ap.add_argument("--d435-serial", required=True, help="D435 序號（雙相機一定要填）")
    ap.add_argument("--camera-id", default="d435_a")
    ap.add_argument("--mavlink", default="/dev/ttyACM1")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--label", default="fire", choices=["fire", "smoke", "person"])
    ap.add_argument("--once", action="store_true", help="只送一筆就結束")
    ap.add_argument("--interval", type=float, default=2.0, help="連續送出間隔（秒），測試用")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--fps", type=int, default=15)
    args = ap.parse_args()

    # 把 Hub URL / Token 套入 sender 模組（避免你去改 thor_send_alert.py 常數）
    sender.HUB_URL = args.hub_url
    sender.AUTH_TOKEN = args.token
    sender.POST_TIMEOUT = 15  # 山區建議拉長一點

    print("[INFO] Hub URL:", sender.HUB_URL)
    print("[INFO] Camera serial:", args.d435_serial, "camera_id:", args.camera_id)
    print("[INFO] MAVLink:", args.mavlink, "baud:", args.baud)

    # 連 MAVLink
    m = connect_mavlink(args.mavlink, args.baud, timeout=5.0)
    print("[INFO] MAVLink heartbeat OK")

    # 開 D435
    pipeline, _ = open_realsense_color(args.d435_serial, width=args.width, height=args.height, fps=args.fps)
    print("[INFO] D435 started")

    try:
        while True:
            frame_bgr, t_cap = grab_color_frame_bgr(pipeline)
            if frame_bgr is None:
                print("[WARN] No color frame, retry...")
                time.sleep(0.5)
                continue

            gps = read_gps(m, timeout=1.0)

            # 目前先用「整張畫面」當 bbox（之後接 SAM3 再換成偵測 ROI）
            h, w = frame_bgr.shape[:2]
            bbox = [0, 0, w, h]

            # 可選：如果想要室內也能區分 GPS 有沒有 valid
            gps_valid = not (abs(gps["lat"]) < 1e-6 and abs(gps["lon"]) < 1e-6)
            note = f"[LIVE] D435+MAVLink gps_valid={gps_valid} fix_type={gps.get('fix_type')}"

            # 送事件（目前用固定 score；之後接 SAM3 用模型 score/mask/bbox）
            r = send_alert(
                label=args.label,
                frame_bgr=frame_bgr,
                bbox=bbox,
                gps=gps,
                score=0.90,
                mask=None,
                camera_id=args.camera_id,
                level="confirmed",
                note=note,
                t_capture=t_cap,
            )
            print("[RESULT]", r.get("ok"), r.get("status_code"), r.get("error"))

            if args.once:
                break

            time.sleep(args.interval)

    finally:
        pipeline.stop()
        print("[INFO] D435 stopped")


if __name__ == "__main__":
    main()