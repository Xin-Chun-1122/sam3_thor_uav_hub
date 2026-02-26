#!/usr/bin/env python3
"""
thor_dualcam_event_sender.py
============================
雙 D435 + MAVLink + SAM3 事件式告警回傳（ROS2 版本）

架構：
  ROS2 subscriber (cam_a, cam_b)
    └─► main_loop (每 0.5 秒)
          ├─ infer_sam3(frame_a) + infer_sam3(frame_b)
          ├─ EventStateMachine  (per-label: fire/smoke/person)
          │    Normal → Suspected → Confirmed → Tracking → Lost
          ├─ 合成雙相機拼接圖 + evidence overlay
          ├─ 讀 MAVLink GPS（GLOBAL_POSITION_INT / GPS_RAW_INT）
          └─ thor_send_alert.send_alert()  → Hub

執行：
  # 實際跑（ROS2 相機 + 飛控）
  python3 thor_dualcam_event_sender.py

  # 覆蓋參數
  python3 thor_dualcam_event_sender.py \\
      --hub-url http://10.0.0.7:8080/api/v1/alerts \\
      --token   YOUR_TOKEN \\
      --mavlink /dev/ttyACM1 --baud 115200

  # 假相機模式（不需要 ROS2，用假圖測試事件狀態機 + 送出）
  python3 thor_dualcam_event_sender.py --fake-cam --fake-event fire
"""

from __future__ import annotations

import argparse
import contextlib
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

# ── SAM3 import（TODO: 換成你的實際模型載入）──
# 若 sam3 不在 PYTHONPATH，可在此 sys.path.insert：
# sys.path.insert(0, "/home/alan/xin/sam3")

import thor_send_alert as sender
from thor_send_alert import send_alert

# ─────────────────────────────────────────────
# 設定常數（可被 CLI 覆蓋）
# ─────────────────────────────────────────────
DEFAULT_HUB_URL  = "http://10.0.0.7:8080/api/v1/alerts"
DEFAULT_TOKEN    = "CHANGE_ME_TO_A_LONG_RANDOM_TOKEN"
DEFAULT_MAVLINK  = "/dev/ttyACM1"
DEFAULT_BAUD     = 115200
TOPIC_A          = "/d435_a/d435_a/color/image_raw"
TOPIC_B          = "/d435_b/d435_b/color/image_raw"
LOOP_INTERVAL    = 0.5   # 秒
POST_TIMEOUT     = 15    # 山區建議拉長

# 事件狀態機參數
SM_M1     = 5    # Suspected 視窗（幀數）
SM_N1     = 2    # Suspected 門檻命中數
SM_M2     = 10   # Confirmed 視窗
SM_N2     = 4    # Confirmed 門檻命中數
SM_T_SEND = 2.0  # Tracking 每次最少間隔（秒）
SM_T_LOST = 10.0 # Lost 倒數（秒）

SCORE_THR    = 0.45   # SAM3 信心分數門檻
MASK_AREA_THR = 200   # mask 有效面積（像素，避免雜訊）
LABELS       = ["fire", "smoke", "person"]


# ─────────────────────────────────────────────
# Detection 資料結構
# ─────────────────────────────────────────────
@dataclass
class Detection:
    label: str           # "fire" | "smoke" | "person"
    score: float         # 0~1
    bbox:  List[int]     # [x, y, w, h]（像素）
    mask:  Optional[np.ndarray] = field(default=None, repr=False)  # HxW uint8


# ─────────────────────────────────────────────
# SAM3 推論（可插拔）
# ─────────────────────────────────────────────
# ── 全域 processor（初始化一次，避免每幀重建）──
_sam3_processor = None
_sam3_autocast  = None

def init_sam3(sam3_root: str = "/home/alan/xin/sam3", device: str = "cuda") -> bool:
    """
    初始化 SAM3 模型。
    若失敗（模型不存在/CUDA 不可用）則回傳 False，程式以 stub 模式繼續。
    """
    global _sam3_processor, _sam3_autocast
    import os
    try:
        import torch
        from sam3 import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor

        # HuggingFace 登入（facebook/sam3 為權限模型）
        hf_token = os.environ.get("HF_TOKEN", "")
        if hf_token:
            try:
                from huggingface_hub import login
                login(token=hf_token, add_to_git_credential=False)
                print("[SAM3] HuggingFace 登入成功")
            except Exception as e:
                print(f"[SAM3] HF login 警告: {e}")
        else:
            print("[SAM3] 警告: HF_TOKEN 未設定，facebook/sam3 可能無權存取")

        # bpe 檔在 sam3 套件內部（sam3_root/sam3/assets/）
        bpe_candidates = [
            f"{sam3_root}/sam3/assets/bpe_simple_vocab_16e6.txt.gz",
            f"{sam3_root}/assets/bpe_simple_vocab_16e6.txt.gz",
        ]
        bpe_path = next((p for p in bpe_candidates if os.path.exists(p)), None)
        if bpe_path is None:
            raise FileNotFoundError(f"bpe 檔案找不到，嘗試過：{bpe_candidates}")
        print(f"[SAM3] 載入模型中... device={device}  bpe={bpe_path}")
        model = build_sam3_image_model(bpe_path=bpe_path)
        model = model.to(device)
        _sam3_processor = Sam3Processor(
            model,
            resolution=1008,
            device=device,
            confidence_threshold=SCORE_THR,
        )
        # 記錄 device 供推論時 autocast 使用（不在此全域進入 autocast）
        _sam3_autocast = device  # 用字串儲存 device 名稱
        print("[SAM3] ✅ 模型載入完成")
        return True
    except Exception as e:
        print(f"[SAM3] ⚠️  模型載入失敗（將以 stub 模式運行）: {e}")
        return False


def infer_sam3(image_bgr: np.ndarray) -> List[Detection]:
    """
    SAM3 text-grounding 推論：偵測 fire / smoke / person。

    輸入：image_bgr  - OpenCV BGR ndarray, HxWx3 uint8
    回傳：List[Detection]

    TODO: 目前為 stub，回傳空 list。
          當 init_sam3() 成功後，此函式會自動切換到實際推論。
          若要手動接入，將 TODO 區塊的 pass 替換為你的模型呼叫。
    """
    # ── 若模型已初始化，使用 SAM3 text grounding ──
    if _sam3_processor is not None:
        return _infer_sam3_real(image_bgr)

    # ──────────────────────────────────────────
    # TODO: 在此接你的 SAM3 模型呼叫
    # 範例格式（根據 sam3_image_predictor_example.ipynb）：
    #
    # from PIL import Image as PILImage
    # pil_img = PILImage.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
    # state = _sam3_processor.set_image(pil_img)
    # results = []
    # for label_text in ["fire", "smoke", "person"]:
    #     _sam3_processor.reset_all_prompts(state)
    #     state = _sam3_processor.set_text_prompt(state=state, prompt=label_text)
    #     if "boxes" in state and len(state["boxes"]) > 0:
    #         boxes  = state["boxes"].cpu().numpy()   # Nx4 xyxy
    #         scores = state["scores"].cpu().numpy()  # N
    #         masks  = state["masks"].cpu().numpy()   # NxHxW bool
    #         for i in range(len(boxes)):
    #             x1,y1,x2,y2 = boxes[i].astype(int)
    #             w,h = x2-x1, y2-y1
    #             m = masks[i].astype(np.uint8)
    #             if scores[i] >= SCORE_THR and m.sum() >= MASK_AREA_THR:
    #                 results.append(Detection(label_text, float(scores[i]),
    #                                          [x1,y1,w,h], m))
    # return results
    # ──────────────────────────────────────────

    return []   # stub：暫時回傳空 list


def _infer_sam3_real(image_bgr: np.ndarray) -> List[Detection]:
    """實際 SAM3 推論（init_sam3 成功後使用）。"""
    import torch
    from PIL import Image as PILImage
    results: List[Detection] = []
    h, w = image_bgr.shape[:2]
    if h == 0 or w == 0:
        return results
    pil_img = PILImage.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))

    # float16 autocast：只在支援 CUDA 時啟用
    use_ac = (isinstance(_sam3_autocast, str) and _sam3_autocast == "cuda"
              and torch.cuda.is_available())

    def _ac():
        return torch.autocast("cuda", dtype=torch.float16) if use_ac else contextlib.nullcontext()

    # set_image 只呼叫一次：提取影像特徵，所有 label 共用
    try:
        with _ac():
            state = _sam3_processor.set_image(pil_img)
    except Exception as e:
        print(f"[SAM3] set_image 錯誤: {e}")
        return results

    for label_text in LABELS:
        try:
            with _ac():
                _sam3_processor.reset_all_prompts(state)
                state = _sam3_processor.set_text_prompt(state=state, prompt=label_text)

            boxes  = state.get("boxes")
            scores = state.get("scores")
            masks  = state.get("masks")
            if boxes is None or len(boxes) == 0:
                continue

            # 離開 autocast 後才轉 numpy，確保型別相容
            boxes_np  = boxes.cpu().float().numpy()
            scores_np = scores.cpu().float().numpy()
            masks_np  = masks.cpu().numpy().astype(np.uint8)

            for i in range(len(boxes_np)):
                sc = float(scores_np[i])
                if sc < SCORE_THR:
                    continue
                x1, y1, x2, y2 = boxes_np[i].astype(int)
                bw, bh = max(1, x2 - x1), max(1, y2 - y1)
                mask_i = masks_np[i]
                # SAM3 可能回傳 (K, H, W) 多候選 mask（K=num_multimask）
                # 或 (1, H, W)、(H, W, 1) 等額外維度 → 壓縮到 2D
                while mask_i.ndim > 2:
                    mask_i = mask_i[0]           # 取第一個（最佳）候選
                if mask_i.ndim != 2 or mask_i.shape[0] == 0 or mask_i.shape[1] == 0:
                    continue                      # 無效 mask，跳過
                if mask_i.shape != (h, w):
                    mask_i = cv2.resize(mask_i, (w, h), interpolation=cv2.INTER_NEAREST)
                if mask_i.sum() < MASK_AREA_THR:
                    continue
                results.append(Detection(label_text, sc, [x1, y1, bw, bh], mask_i))
        except Exception as e:
            print(f"[SAM3] 推論錯誤 label={label_text}: {e}")
    return results


# ─────────────────────────────────────────────
# 事件狀態機
# ─────────────────────────────────────────────
class EventState:
    NORMAL    = "normal"
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"
    TRACKING  = "tracking"
    LOST      = "lost"


class EventStateMachine:
    """
    per-label 事件狀態機：
      Normal → Suspected → Confirmed → Tracking → (Lost → Normal)

    參數（全局常數控制，可改為 config.yaml）：
      SM_M1, SM_N1: Suspected 條件（近 M1 幀內 >= N1 命中）
      SM_M2, SM_N2: Confirmed 條件（近 M2 幀內 >= N2 命中）
      SM_T_SEND   : Tracking 最短傳送間隔（秒）
      SM_T_LOST   : 連續未命中幾秒後 → Lost
    """
    def __init__(self, label: str):
        self.label       = label
        self.state       = EventState.NORMAL
        self.event_id:  Optional[str] = None
        self.hits: deque = deque()   # 每幀命中的 unix timestamp
        self._last_send  = 0.0
        self._last_hit   = 0.0

    def update(self, hit: bool, now: float) -> Tuple[bool, str]:
        """
        推進狀態機，回傳 (should_send, level)。

        hit   : 此幀是否有有效偵測
        now   : 當前 unix timestamp
        回傳  : (should_send, level)
                should_send=True 代表事件成立，請送出告警
                level = "suspected" | "confirmed" | "critical"
        """
        if hit:
            self.hits.append(now)
            self._last_hit = now

        # 清理過期紀錄（只保留 M2 視窗內）
        cutoff_m2 = now - SM_M2 * LOOP_INTERVAL * 1.5
        while self.hits and self.hits[0] < cutoff_m2:
            self.hits.popleft()

        hits_m1 = sum(1 for t in self.hits if t >= now - SM_M1 * LOOP_INTERVAL * 1.5)
        hits_m2 = len(self.hits)

        # ── 狀態轉移 ──
        if self.state == EventState.NORMAL:
            if hits_m1 >= SM_N1:
                self.state = EventState.SUSPECTED
            return False, "suspected"

        elif self.state == EventState.SUSPECTED:
            if hits_m2 >= SM_N2:
                self.state = EventState.CONFIRMED
                self.event_id = f"{self.label}_{int(now*1000)}"
                return True, "confirmed"
            if hits_m1 < 1:   # 視窗內沒命中，降回 Normal
                self.state = EventState.NORMAL
                self.hits.clear()
            return False, "suspected"

        elif self.state == EventState.CONFIRMED:
            self.state = EventState.TRACKING
            return False, "confirmed"

        elif self.state == EventState.TRACKING:
            # Lost 判定
            if (now - self._last_hit) > SM_T_LOST:
                self.state = EventState.LOST
                return False, "tracking"
            # 節流：每 T_SEND 秒最多送一次
            if hit and (now - self._last_send) >= SM_T_SEND:
                self._last_send = now
                return True, "tracking"
            return False, "tracking"

        elif self.state == EventState.LOST:
            # 重置，回 Normal
            self.state    = EventState.NORMAL
            self.event_id = None
            self.hits.clear()
            return False, "normal"

        return False, "normal"

    def reset(self):
        self.state    = EventState.NORMAL
        self.event_id = None
        self.hits.clear()
        self._last_send = 0.0
        self._last_hit  = 0.0


# ─────────────────────────────────────────────
# MAVLink GPS 讀取
# ─────────────────────────────────────────────
_mav_conn = None
_gps_cache: Dict = {"lat": 0.0, "lon": 0.0, "alt": 0.0, "fix_type": None}
_mav_lock  = threading.Lock()

# Home GPS（無 GPS Fix 時的預設位置，可由 --home-lat/lon/alt 覆蓋）
# 預設：國立清華大學 光復路二段101號，新竹市 30013
_home_gps: Dict = {"lat": 24.7968, "lon": 120.9961, "alt": 75.0}


def connect_mavlink(dev: str, baud: int, timeout: float = 5.0):
    from pymavlink import mavutil
    print(f"[MAV] 連線 {dev} baud={baud}...")
    m = mavutil.mavlink_connection(dev, baud=baud)
    hb = m.wait_heartbeat(timeout=timeout)
    if hb is None:
        raise RuntimeError(f"[MAV] 沒有收到 heartbeat（{dev}）")
    print(f"[MAV] ✅ Heartbeat OK  sysid={m.target_system}")
    return m


def _mav_reader_thread(dev: str, baud: int):
    """背景執行緒：持續從飛控讀 GPS 並更新 cache。"""
    global _mav_conn
    from pymavlink import mavutil
    while True:
        try:
            _mav_conn = connect_mavlink(dev, baud, timeout=8.0)
            while True:
                msg = _mav_conn.recv_match(
                    type=["GLOBAL_POSITION_INT", "GPS_RAW_INT"],
                    blocking=True, timeout=2.0
                )
                if msg is None:
                    continue
                t = msg.get_type()
                with _mav_lock:
                    if t == "GLOBAL_POSITION_INT":
                        _gps_cache.update({
                            "lat": float(msg.lat / 1e7),
                            "lon": float(msg.lon / 1e7),
                            "alt": float(msg.alt / 1000.0),
                            "fix_type": None,
                        })
                    else:  # GPS_RAW_INT
                        _gps_cache.update({
                            "lat": float(msg.lat / 1e7),
                            "lon": float(msg.lon / 1e7),
                            "alt": float(msg.alt / 1000.0),
                            "fix_type": getattr(msg, "fix_type", None),
                        })
        except Exception as e:
            print(f"[MAV] 連線斷開，5 秒後重試: {e}")
            time.sleep(5)


def get_gps() -> Dict:
    """取得 GPS。fix_type < 2（無有效定位）時退回 Home GPS（清華大學）。"""
    with _mav_lock:
        g = dict(_gps_cache)
    ft = g.get("fix_type")
    # fix_type: None=未知, 0=無定位, 1=Dead Reckoning, 2=2D Fix, 3=3D Fix
    if ft is not None and int(ft) >= 2:
        return {**g, "source": "fix"}
    # 無有效 GPS → 回傳 Home GPS，標記 source=home
    return {**_home_gps, "fix_type": ft or 0, "source": "home"}


# ─────────────────────────────────────────────
# 圖像合成（雙相機拼接 + overlay）
# ─────────────────────────────────────────────

def _draw_detections(img: np.ndarray, dets: List[Detection]) -> np.ndarray:
    """在 img 上畫 detection 的 bbox + mask overlay + label/score。"""
    vis = img.copy()
    COLOR = {
        "fire":   (0,  80, 255),   # BGR 橘紅
        "smoke":  (140, 140, 140),
        "person": (50,  200,  50),
    }
    for d in dets:
        c = COLOR.get(d.label, (0, 255, 255))
        # mask overlay
        if d.mask is not None:
            m = d.mask.astype(np.uint8)
            if m.shape[:2] != vis.shape[:2]:
                m = cv2.resize(m, (vis.shape[1], vis.shape[0]),
                               interpolation=cv2.INTER_NEAREST)
            ov = np.zeros_like(vis, dtype=np.uint8)
            ov[m > 0] = c
            vis = cv2.addWeighted(vis, 1.0, ov, 0.4, 0)
        # bbox
        x, y, w, h = d.bbox
        cv2.rectangle(vis, (x, y), (x + w, y + h), c, 2)
        # label text
        txt = f"{d.label} {d.score:.2f}"
        fs = max(0.45, min(img.shape[1], img.shape[0]) / 640)
        th = max(1, int(fs * 1.8))
        (tw, txh), bl = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
        ty = max(y - 4, txh + bl)
        cv2.rectangle(vis, (x, ty - txh - bl), (x + tw + 4, ty + bl), c, -1)
        cv2.putText(vis, txt, (x + 2, ty), cv2.FONT_HERSHEY_SIMPLEX,
                    fs, (255, 255, 255), th, cv2.LINE_AA)
    return vis


def fuse_frames(
    frame_a: np.ndarray,
    frame_b: np.ndarray,
    dets_a:  List[Detection],
    dets_b:  List[Detection],
) -> Tuple[np.ndarray, List[int]]:
    """
    合成雙相機拼接圖（左=A, 右=B），等高 resize 後 hstack。
    回傳 (fused_bgr, bbox_full=[0,0,W,H])
    """
    target_h = 480   # 統一縮到 480p 高
    def _resize_h(img, h):
        oh, ow = img.shape[:2]
        nw = int(ow * h / oh)
        return cv2.resize(img, (nw, h), interpolation=cv2.INTER_LINEAR)

    ra = _resize_h(_draw_detections(frame_a, dets_a), target_h)
    rb = _resize_h(_draw_detections(frame_b, dets_b), target_h)

    # 分隔線
    sep = np.full((target_h, 4, 3), 80, dtype=np.uint8)

    # 標籤
    def _label(img, txt, color):
        out = img.copy()
        cv2.rectangle(out, (0, 0), (len(txt) * 11 + 8, 24), (0, 0, 0), -1)
        cv2.putText(out, txt, (4, 17), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, color, 1, cv2.LINE_AA)
        return out

    ra = _label(ra, "CAM-A (d435_a)", (100, 255, 100))
    rb = _label(rb, "CAM-B (d435_b)", (100, 200, 255))

    fused = np.hstack([ra, sep, rb])
    H, W = fused.shape[:2]
    return fused, [0, 0, W, H]


# ─────────────────────────────────────────────
# 主 ROS2 Node
# ─────────────────────────────────────────────

def _try_import_rclpy():
    try:
        import rclpy
        from rclpy.node import Node
        from sensor_msgs.msg import Image
        from cv_bridge import CvBridge
        return rclpy, Node, Image, CvBridge
    except ImportError as e:
        return None


class DualCamEventNode:
    """ROS2 node：訂閱雙相機 topic，主迴圈做推論與事件判定。"""

    def __init__(self, args):
        rclpy_mod = _try_import_rclpy()
        if rclpy_mod is None:
            raise ImportError("rclpy / cv_bridge 未安裝，請使用 --fake-cam 模式")
        rclpy, NodeBase, ImageMsg, CvBridge = rclpy_mod

        import rclpy
        from rclpy.node import Node
        from sensor_msgs.msg import Image as ImageMsg
        from cv_bridge import CvBridge

        class _Inner(Node):
            def __init__(inner_self):
                super().__init__("dual_cam_event_sender")
                inner_self.bridge = CvBridge()
                inner_self.frame_a: Optional[np.ndarray] = None
                inner_self.frame_b: Optional[np.ndarray] = None
                inner_self.lock_a  = threading.Lock()
                inner_self.lock_b  = threading.Lock()

                inner_self.create_subscription(
                    ImageMsg, TOPIC_A, inner_self._cb_a, 10)
                inner_self.create_subscription(
                    ImageMsg, TOPIC_B, inner_self._cb_b, 10)
                inner_self.get_logger().info(
                    f"Subscribed:\n  A={TOPIC_A}\n  B={TOPIC_B}")

                inner_self.sms: Dict[str, EventStateMachine] = {
                    lbl: EventStateMachine(lbl) for lbl in LABELS
                }
                inner_self.timer = inner_self.create_timer(
                    LOOP_INTERVAL, inner_self._tick)

            def _cb_a(inner_self, msg):
                try:
                    f = inner_self.bridge.imgmsg_to_cv2(msg, "bgr8")
                    with inner_self.lock_a:
                        inner_self.frame_a = f
                except Exception as e:
                    inner_self.get_logger().warn(f"cam_a convert err: {e}")

            def _cb_b(inner_self, msg):
                try:
                    f = inner_self.bridge.imgmsg_to_cv2(msg, "bgr8")
                    with inner_self.lock_b:
                        inner_self.frame_b = f
                except Exception as e:
                    inner_self.get_logger().warn(f"cam_b convert err: {e}")

            def _tick(inner_self):
                _main_loop_body(
                    get_frame_a=lambda: _copy_locked(inner_self.frame_a, inner_self.lock_a),
                    get_frame_b=lambda: _copy_locked(inner_self.frame_b, inner_self.lock_b),
                    sms=inner_self.sms,
                    log=inner_self.get_logger().info,
                    warn=inner_self.get_logger().warn,
                )

        self._rclpy  = rclpy
        self._node   = _Inner()

    def spin(self):
        self._rclpy.spin(self._node)

    def destroy(self):
        self._node.destroy_node()
        self._rclpy.shutdown()


def _copy_locked(frame, lock) -> Optional[np.ndarray]:
    with lock:
        return None if frame is None else frame.copy()


# ─────────────────────────────────────────────
# 核心主迴圈（ROS2 / fake-cam 共用）
# ─────────────────────────────────────────────

def _main_loop_body(
    get_frame_a,
    get_frame_b,
    sms: Dict[str, EventStateMachine],
    log=print,
    warn=print,
):
    t_capture = time.time()
    frame_a = get_frame_a()
    frame_b = get_frame_b()

    # ── 1. 任一相機沒圖則跳過 ──
    if frame_a is None or frame_b is None:
        warn("[loop] 等待雙相機畫面...")
        return

    # ── 2. SAM3 推論 ──
    try:
        dets_a = infer_sam3(frame_a)
        dets_b = infer_sam3(frame_b)
    except Exception as e:
        warn(f"[loop] infer_sam3 錯誤: {e}")
        dets_a, dets_b = [], []

    # 合併所有偵測，依 label 分組取最高分
    best: Dict[str, Detection] = {}
    for d in dets_a + dets_b:
        if d.label not in best or d.score > best[d.label].score:
            best[d.label] = d

    # ── 3. 事件狀態機推進 ──
    now = time.time()
    for lbl in LABELS:
        hit = lbl in best and best[lbl].score >= SCORE_THR
        should_send, level = sms[lbl].update(hit, now)

        if not should_send:
            continue

        # ── 4. 合成雙相機拼接圖 ──
        fused, bbox_full = fuse_frames(frame_a, frame_b, dets_a, dets_b)

        # ── 4b. 將最佳偵測 bbox 對應到 fused 座標（避免整張圖都是大框）──
        # fuse_frames 統一縮到 target_h=480；D435 本身是 480p，scale=1.0
        # cam_b 在 fused 中的 x 偏移 = cam_a_disp_w + 4（分隔線）
        if lbl in best:
            _det  = best[lbl]
            _sh   = 480.0 / frame_a.shape[0]             # height scale
            _sw   = 480.0 / frame_a.shape[0]             # same for width（等比）
            _caw  = int(frame_a.shape[1] * _sh)          # cam_a 顯示寬度
            _from_a = any(d.label == lbl for d in dets_a)
            _xoff = 0 if _from_a else (_caw + 4)         # cam_b x 偏移
            bx, by, bw, bh = _det.bbox
            send_bbox = [
                int(bx * _sw) + _xoff,
                int(by * _sh),
                max(1, int(bw * _sw)),
                max(1, int(bh * _sh)),
            ]
        else:
            send_bbox = bbox_full

        # ── 5. 讀 GPS ──
        gps = get_gps()

        # ── 6. 組裝 note（各相機最高分偵測摘要）──
        summary_a = ", ".join(
            f"{d.label}:{d.score:.2f}" for d in sorted(dets_a, key=lambda x: -x.score)
        ) or "none"
        summary_b = ", ".join(
            f"{d.label}:{d.score:.2f}" for d in sorted(dets_b, key=lambda x: -x.score)
        ) or "none"
        note = f"camA=[{summary_a}] camB=[{summary_b}] sm={sms[lbl].state}"

        # mask：若有，用最高分那一幀的 mask（需 resize 到 fused 尺寸）
        fused_mask = None
        if lbl in best and best[lbl].mask is not None:
            fh, fw = fused.shape[:2]
            # mask 原本是單相機尺寸，貼到 fused 左半或右半
            m = best[lbl].mask.astype(np.uint8)
            fused_mask_tmp = np.zeros((fh, fw), dtype=np.uint8)
            # 判斷來自哪個相機，決定貼左半(A)或右半(B)
            cam_w_a = int(fw * frame_a.shape[1] /
                          (frame_a.shape[1] + frame_b.shape[1] + 4))
            target_section = fused_mask_tmp[:, :cam_w_a] if any(
                d.label == lbl for d in dets_a) else fused_mask_tmp[:, cam_w_a + 4:]
            resized_m = cv2.resize(
                m, (target_section.shape[1], fh),
                interpolation=cv2.INTER_NEAREST
            )
            target_section[:] = resized_m
            fused_mask = fused_mask_tmp

        event_id = sms[lbl].event_id or f"{lbl}_{int(now*1000)}"

        log(f"[EVENT] 🚨 label={lbl} level={level} "
            f"event_id={event_id} state={sms[lbl].state} "
            f"GPS=({gps['lat']:.5f},{gps['lon']:.5f},{gps['alt']:.1f}m)")

        # ── 7. 送出 ──
        result = send_alert(
            label=lbl,
            frame_bgr=fused,
            bbox=bbox_full,
            gps=gps,
            score=best[lbl].score if lbl in best else 0.5,
            mask=fused_mask,
            camera_id="dual",
            level=level,
            note=note,
            event_id=event_id,
            t_capture=t_capture,
        )

        if not result["ok"]:
            warn(f"[EVENT] ⚠️  送出失敗，存入 queue: {result['error']}")
            # 本地 queue（離線重送）
            try:
                sender.save_to_queue(
                    meta=result["meta"],
                    thumb_bytes=sender.make_thumb(fused),
                    evid_bytes=sender.make_evidence(
                        fused, bbox_full, lbl,
                        best[lbl].score if lbl in best else 0.5,
                        fused_mask
                    ),
                    queue_dir="queue",
                )
            except Exception as qe:
                warn(f"[queue] 存 queue 失敗: {qe}")


# ─────────────────────────────────────────────
# fake-cam 模式（無 ROS2 也能測試）
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# pyrealsense2 雙相機直讀模式（--no-ros2 / Docker）
# ─────────────────────────────────────────────

def _open_realsense(serial: str = "", width: int = 640, height: int = 480, fps: int = 15):
    """
    開啟 RealSense D435，回傳 (pipeline, align)。
    serial="" 表示自動選擇（第一顆可用）。
    """
    import pyrealsense2 as rs
    pipe = rs.pipeline()
    cfg  = rs.config()
    if serial:
        cfg.enable_device(serial)
    cfg.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
    pipe.start(cfg)
    align = rs.align(rs.stream.color)
    return pipe, align


def _grab_frame(pipe, align) -> Optional[np.ndarray]:
    """從 RealSense pipeline 取最新一幀 BGR ndarray。"""
    import pyrealsense2 as rs
    try:
        frames = pipe.wait_for_frames(timeout_ms=3000)
    except RuntimeError:
        return None
    aligned = align.process(frames)
    color_frame = aligned.get_color_frame()
    if not color_frame:
        return None
    return np.asanyarray(color_frame.get_data())


def _list_realsense_serials() -> List[str]:
    """列出所有已連接的 RealSense 裝置序號。"""
    try:
        import pyrealsense2 as rs
        ctx = rs.context()
        return [d.get_info(rs.camera_info.serial_number)
                for d in ctx.query_devices()]
    except Exception:
        return []


def run_realsense_mode(args):
    """
    pyrealsense2 雙相機直讀模式。
    無需 ROS2，直接用 USB 讀兩顆 D435。
    """
    try:
        import pyrealsense2 as rs
    except ImportError:
        print("❌ pyrealsense2 未安裝。請在容器內執行：pip install pyrealsense2")
        sys.exit(1)

    serials = _list_realsense_serials()
    print(f"[RS] 偵測到 {len(serials)} 顆 RealSense: {serials}")

    if len(serials) < 2 and not (args.serial_a and args.serial_b):
        if len(serials) == 0:
            print("❌ 找不到 RealSense 裝置，請確認 USB 連接與 --privileged 模式")
            sys.exit(1)
        elif len(serials) == 1:
            print("⚠️  只找到 1 顆 D435，用同一顆模擬雙相機（測試用）")
            serial_a = serials[0]
            serial_b = serials[0]
        else:
            serial_a, serial_b = serials[0], serials[1]
    else:
        serial_a = args.serial_a or (serials[0] if serials else "")
        serial_b = args.serial_b or (serials[1] if len(serials) > 1 else serials[0] if serials else "")

    print(f"[RS] 開啟相機 A (serial={serial_a or 'auto'})...")
    pipe_a, align_a = _open_realsense(serial_a)
    # 若兩顆序號相同就共用同一個 pipeline
    if serial_a == serial_b:
        pipe_b, align_b = pipe_a, align_a
        print("[RS] 相機 B 與 A 共用同一 pipeline（單相機測試模式）")
    else:
        print(f"[RS] 開啟相機 B (serial={serial_b or 'auto'})...")
        pipe_b, align_b = _open_realsense(serial_b)

    print("[RS] ✅ 相機開啟完成，開始推論迴圈（Ctrl+C 停止）")

    sms: Dict[str, EventStateMachine] = {lbl: EventStateMachine(lbl) for lbl in LABELS}
    frame_a_cache = [None]
    frame_b_cache = [None]

    try:
        while True:
            t0 = time.time()

            fa = _grab_frame(pipe_a, align_a)
            fb = _grab_frame(pipe_b, align_b) if (pipe_b is not pipe_a) else fa.copy() if fa is not None else None

            if fa is not None:
                frame_a_cache[0] = fa
            if fb is not None:
                frame_b_cache[0] = fb

            _main_loop_body(
                get_frame_a=lambda: frame_a_cache[0],
                get_frame_b=lambda: frame_b_cache[0],
                sms=sms,
            )

            elapsed = time.time() - t0
            sleep_t = max(0.0, LOOP_INTERVAL - elapsed)
            time.sleep(sleep_t)
    except KeyboardInterrupt:
        print("\n[RS] 使用者中斷")
    finally:
        pipe_a.stop()
        if pipe_b is not pipe_a:
            pipe_b.stop()
        print("[RS] 相機已關閉")


def _make_fake_frame(label: str = "fire", seed: int = 0) -> np.ndarray:
    """產生帶有指定事件特徵的假 640×480 BGR 圖。"""
    np.random.seed(seed)
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    for i in range(480):
        img[i, :, 0] = int(i / 480 * 180) + 40
        img[i, :, 2] = int((480 - i) / 480 * 180) + 40
    for j in range(0, 640, 80):
        cv2.line(img, (j, 0), (j, 480), (60, 60, 60), 1)
    for i in range(0, 480, 60):
        cv2.line(img, (0, i), (640, i), (60, 60, 60), 1)

    cx, cy = 280 + np.random.randint(-20, 20), 210 + np.random.randint(-15, 15)
    if label == "fire":
        pts = np.array([[cx, cy+70],[cx-30, cy+40],[cx-18, cy+20],
                        [cx-45, cy],[cx-8, cy+8],[cx, cy-35],
                        [cx+8, cy+8],[cx+45, cy],[cx+18, cy+20],[cx+30, cy+40]], np.int32)
        cv2.fillPoly(img, [pts], (0, 90, 255))
        cv2.putText(img, "FIRE SIM", (cx - 50, cy - 50),
                    cv2.FONT_HERSHEY_DUPLEX, 0.8, (0, 200, 255), 2)
    elif label == "smoke":
        for r in range(30, 90, 15):
            cv2.circle(img, (cx, cy), r, (130, 130, 130), -1)
        cv2.putText(img, "SMOKE SIM", (cx - 60, cy - 100),
                    cv2.FONT_HERSHEY_DUPLEX, 0.8, (200, 200, 200), 2)
    elif label == "person":
        cv2.rectangle(img, (cx - 20, cy - 60), (cx + 20, cy + 60), (50, 200, 50), -1)
        cv2.circle(img, (cx, cy - 75), 18, (50, 200, 50), -1)
        cv2.putText(img, "PERSON SIM", (cx - 60, cy - 105),
                    cv2.FONT_HERSHEY_DUPLEX, 0.8, (50, 255, 50), 2)
    return img


def _fake_infer(frame_bgr: np.ndarray, label: str, score: float) -> List[Detection]:
    """假推論：在圖像中央回傳一個 detection。"""
    h, w = frame_bgr.shape[:2]
    bx, by = w // 2 - 80, h // 2 - 70
    bw, bh = 160, 140
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.ellipse(mask, (w // 2, h // 2), (bw // 2, bh // 2), 0, 0, 360, 1, -1)
    return [Detection(label=label, score=score, bbox=[bx, by, bw, bh], mask=mask)]


def run_fake_cam(args):
    """
    --fake-cam 模式：模擬雙相機 + 強制假偵測，
    讓事件狀態機快速達到 Confirmed 並送出。
    """
    print(f"[FAKE-CAM] 假相機模式  label={args.fake_event}")
    print(f"[FAKE-CAM] 持續送 {SM_N2 + 2} 幀讓狀態機到 Confirmed...")

    sms: Dict[str, EventStateMachine] = {lbl: EventStateMachine(lbl) for lbl in LABELS}
    frame_counter = [0]

    def get_a():
        frame_counter[0] += 1
        return _make_fake_frame(args.fake_event, seed=frame_counter[0])

    def get_b():
        return _make_fake_frame(args.fake_event, seed=frame_counter[0] + 100)

    # monkey-patch infer_sam3 → 假偵測
    import thor_dualcam_event_sender as _self
    _orig_infer = _self.infer_sam3

    def _fake_infer_patch(img):
        score = 0.88 + np.random.uniform(-0.05, 0.05)
        return _fake_infer(img, args.fake_event, score)

    _self.infer_sam3 = _fake_infer_patch

    max_loops = SM_M2 * 3   # 跑足夠多幀讓狀態機推進
    for i in range(max_loops):
        print(f"\n[FAKE-CAM] === loop {i+1}/{max_loops} ===")
        _main_loop_body(get_a, get_b, sms)
        time.sleep(LOOP_INTERVAL)

    _self.infer_sam3 = _orig_infer
    print("[FAKE-CAM] 完成，請到 Hub Dashboard 確認事件卡片。")


# ─────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────

def parse_args():
    ap = argparse.ArgumentParser(
        description="Thor 雙相機事件式告警回傳（ROS2 + MAVLink + SAM3）"
    )
    ap.add_argument("--hub-url",    default=DEFAULT_HUB_URL)
    ap.add_argument("--token",      default=DEFAULT_TOKEN)
    ap.add_argument("--mavlink",    default=DEFAULT_MAVLINK,
                    help="MAVLink 連線（/dev/ttyACM1 或 udp:127.0.0.1:14550）")
    ap.add_argument("--baud",       type=int, default=DEFAULT_BAUD)
    ap.add_argument("--no-mavlink", action="store_true",
                    help="不連飛控，GPS 用假值（純測試用）")
    ap.add_argument("--fake-cam",   action="store_true",
                    help="不啟動 ROS2，用假圖測試事件狀態機與 POST")
    ap.add_argument("--fake-event", default="fire",
                    choices=LABELS,
                    help="[--fake-cam] 要模擬的事件類型")
    ap.add_argument("--sam3-root",  default="/home/alan/xin/sam3",
                    help="SAM3 repo 根目錄（用於 init_sam3）")
    ap.add_argument("--no-sam3",    action="store_true",
                    help="不載入 SAM3，全程用 stub 推論（適合純通訊測試）")
    ap.add_argument("--no-ros2",    action="store_true",
                    help="不啟動 ROS2，改用 pyrealsense2 直讀 D435（Docker 模式預設）")
    ap.add_argument("--serial-a",   default="",
                    help="[no-ros2] D435_A 序號（空=自動選第一顆）")
    ap.add_argument("--serial-b",   default="",
                    help="[no-ros2] D435_B 序號（空=自動選第二顆）")
    ap.add_argument("--home-lat",   type=float, default=24.7968,
                    help="無 GPS Fix 時的預設緯度（預設：清華大學 24.7968）")
    ap.add_argument("--home-lon",   type=float, default=120.9961,
                    help="無 GPS Fix 時的預設經度（預設：清華大學 120.9961）")
    ap.add_argument("--home-alt",   type=float, default=75.0,
                    help="無 GPS Fix 時的預設高度 m（預設：75m）")
    return ap.parse_args()


def main():
    global _home_gps
    args = parse_args()

    # ── Home GPS 更新（無 Fix 時的備援位置）──
    _home_gps = {"lat": args.home_lat, "lon": args.home_lon, "alt": args.home_alt}

    # ── 套用全域設定 ──
    sender.HUB_URL      = args.hub_url
    sender.AUTH_TOKEN   = args.token
    sender.POST_TIMEOUT = POST_TIMEOUT

    print("=" * 60)
    print("  Thor 雙相機事件式告警回傳 v1.0")
    print(f"  Hub    : {args.hub_url}")
    print(f"  Token  : {args.token[:12]}...")
    print(f"  MAVLink: {args.mavlink} (baud={args.baud})")
    print(f"  Home GPS: {args.home_lat:.4f}, {args.home_lon:.4f}, {args.home_alt:.0f}m")
    print("=" * 60)

    # ── SAM3 初始化 ──
    if not args.no_sam3:
        sys.path.insert(0, args.sam3_root)
        init_sam3(sam3_root=args.sam3_root)
    else:
        print("[SAM3] 已跳過（--no-sam3），使用 stub 推論")

    # ── MAVLink 背景執行緒 ──
    if not args.no_mavlink:
        t = threading.Thread(
            target=_mav_reader_thread,
            args=(args.mavlink, args.baud),
            daemon=True
        )
        t.start()
        time.sleep(1.5)   # 等待第一筆 GPS
    else:
        print(f"[MAV] 已跳過（--no-mavlink），GPS 使用 Home 位置（{args.home_lat:.4f}, {args.home_lon:.4f}, {args.home_alt:.0f}m）")
        with _mav_lock:
            _gps_cache.update({**_home_gps, "fix_type": 2})

    # ── 執行主邏輯 ──
    if args.fake_cam:
        run_fake_cam(args)
    elif args.no_ros2:
        # pyrealsense2 直讀模式（Docker / 無 ROS2 環境）
        run_realsense_mode(args)
    else:
        # ROS2 模式
        rclpy_mod = _try_import_rclpy()
        if rclpy_mod is None:
            print("❌ rclpy 未安裝，請改用 --fake-cam 或 --no-ros2 模式")
            sys.exit(1)
        rclpy = rclpy_mod[0]
        rclpy.init()
        try:
            node = DualCamEventNode(args)
            print("[ROS2] 開始訂閱相機 topic，等待畫面...")
            node.spin()
        except KeyboardInterrupt:
            print("\n[ROS2] 使用者中斷")
        finally:
            try:
                node.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    main()
