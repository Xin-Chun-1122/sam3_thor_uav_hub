#!/usr/bin/env python3
import argparse
import time
import threading

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from cv_bridge import CvBridge

from pymavlink import mavutil

import thor_send_alert as sender
from thor_send_alert import send_alert


def connect_mavlink(dev: str, baud: int, timeout: float = 5.0):
    m = mavutil.mavlink_connection(dev, baud=baud)
    hb = m.wait_heartbeat(timeout=timeout)
    if hb is None:
        raise RuntimeError(f"No heartbeat on {dev} (baud={baud})")
    return m


def read_gps(m, timeout: float = 1.0):
    msg = m.recv_match(type=["GLOBAL_POSITION_INT", "GPS_RAW_INT"], blocking=True, timeout=timeout)
    if msg is None:
        return {"lat": 0.0, "lon": 0.0, "alt": 0.0, "fix_type": None}

    t = msg.get_type()
    if t == "GLOBAL_POSITION_INT":
        return {
            "lat": float(msg.lat / 1e7),
            "lon": float(msg.lon / 1e7),
            "alt": float(msg.alt / 1000.0),
            "fix_type": None
        }

    return {
        "lat": float(msg.lat / 1e7),
        "lon": float(msg.lon / 1e7),
        "alt": float(msg.alt / 1000.0),
        "fix_type": getattr(msg, "fix_type", None)
    }


class D435Sender(Node):
    def __init__(self, args):
        super().__init__("d435_fc_sender")
        self.args = args
        self.bridge = CvBridge()

        self.latest = None
        self.lock = threading.Lock()

        self.create_subscription(Image, args.image_topic, self.cb_img, 10)
        self.get_logger().info(f"Subscribed: {args.image_topic}")

        self.mav = connect_mavlink(args.mavlink, args.baud, timeout=5.0)
        self.get_logger().info("MAVLink heartbeat OK")

        self.sent_once = False
        self.timer = self.create_timer(args.interval, self.tick)

    def cb_img(self, msg: Image):
        frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        with self.lock:
            self.latest = frame

    def tick(self):
        with self.lock:
            frame = None if self.latest is None else self.latest.copy()

        if frame is None:
            self.get_logger().warn("No image yet...")
            return

        gps = read_gps(self.mav, timeout=0.2)

        h, w = frame.shape[:2]
        bbox = [0, 0, w, h]  # 先用整張圖當 ROI，接 SAM3 再換 bbox/mask

        gps_valid = not (abs(gps["lat"]) < 1e-6 and abs(gps["lon"]) < 1e-6)
        note = f"[ROS2] topic={self.args.image_topic} gps_valid={gps_valid} fix_type={gps.get('fix_type')}"

        r = send_alert(
            label=self.args.label,
            frame_bgr=frame,
            bbox=bbox,
            gps=gps,
            score=0.90,
            mask=None,
            camera_id=self.args.camera_id,
            level="confirmed",
            note=note,
            t_capture=time.time(),
        )
        self.get_logger().info(f"Send: ok={r.get('ok')} code={r.get('status_code')} err={r.get('error')}")

        if self.args.once and not self.sent_once:
            self.sent_once = True
            # 讓 log 有時間刷出來
            time.sleep(0.2)
            rclpy.shutdown()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub-url", default="http://10.0.0.7:8080/api/v1/alerts")
    ap.add_argument("--token", default="CHANGE_ME_TO_A_LONG_RANDOM_TOKEN")
    ap.add_argument("--image-topic", required=True)
    ap.add_argument("--camera-id", default="d435_a")
    ap.add_argument("--mavlink", default="/dev/ttyACM1")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--label", default="fire", choices=["fire", "smoke", "person"])
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    sender.HUB_URL = args.hub_url
    sender.AUTH_TOKEN = args.token
    sender.POST_TIMEOUT = 15  # 山區建議拉長

    rclpy.init()
    node = D435Sender(args)
    rclpy.spin(node)


if __name__ == "__main__":
    main()
