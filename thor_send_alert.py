#!/usr/bin/env python3
"""
thor_send_alert.py
==================
Thor(Ubuntu/GPU) -> Hub(10.0.0.7) 事件式告警回傳
不串流影像，只在「事件成立」時上傳：證據圖片 + GPS位置 + 時間戳

用法：
  # 正常呼叫（由主程式呼叫）
  from thor_send_alert import send_alert
  result = send_alert(label="fire", frame_bgr=img, bbox=[x,y,w,h], gps={"lat":...,"lon":...,"alt":...})

  # 假資料測試（確認網路與格式100%通）
  python3 thor_send_alert.py --test

  # 指定測試圖片
  python3 thor_send_alert.py --test --image /path/to/test.jpg
"""

import argparse
import io
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont

# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────
HUB_URL       = "http://10.0.0.7:8080/api/v1/alerts"
AUTH_TOKEN    = "CHANGE_ME_TO_A_LONG_RANDOM_TOKEN"
POST_TIMEOUT  = 10          # seconds
THUMB_SIZE    = (320, 240)  # px
WEBP_THUMB_Q  = 60
WEBP_EVID_Q   = 75
ROI_PADDING   = 0.30        # bbox 外擴 30%
MASK_ALPHA    = 0.40        # mask 半透明強度
TZ_TAIPEI     = timezone(timedelta(hours=8))

# 標籤顏色映射（BGR → RGB 之後轉）
LABEL_COLOR = {
    "fire":   (255,  80,  30),   # 橘紅
    "smoke":  (140, 140, 140),   # 灰
    "person": ( 50, 200,  50),   # 綠
}
DEFAULT_COLOR = (255, 255,   0)  # 黃

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("thor_alert")


# ─────────────────────────────────────────────
# 工具函式
# ─────────────────────────────────────────────

def _now_taipei() -> datetime:
    """回傳台北時區的當前時間（+08:00）。"""
    return datetime.now(tz=TZ_TAIPEI)


def _iso8601_taipei(dt: Optional[datetime] = None) -> str:
    """格式化為 ISO8601 含時區偏移字串，例如：2026-02-24T15:30:00+08:00"""
    dt = dt or _now_taipei()
    return dt.isoformat(timespec="seconds")


def _pad_bbox(bbox: list, img_w: int, img_h: int, pad: float = ROI_PADDING) -> list:
    """
    將 bbox=[x,y,w,h] 向外擴展 pad 比例，並限制在圖像範圍內。
    回傳 [x1, y1, x2, y2]（切片用）。
    """
    x, y, w, h = bbox
    pw = int(w * pad)
    ph = int(h * pad)
    x1 = max(0, x - pw)
    y1 = max(0, y - ph)
    x2 = min(img_w, x + w + pw)
    y2 = min(img_h, y + h + ph)
    return [x1, y1, x2, y2]


def _bgr_to_pil(img_bgr: np.ndarray) -> Image.Image:
    """OpenCV BGR ndarray → Pillow RGB Image。"""
    return Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))


def _encode_webp(pil_img: Image.Image, quality: int) -> bytes:
    """Pillow Image → WebP bytes。"""
    buf = io.BytesIO()
    pil_img.save(buf, format="WEBP", quality=quality, method=4)
    return buf.getvalue()


# ─────────────────────────────────────────────
# 圖像生成
# ─────────────────────────────────────────────

def make_thumb(frame_bgr: np.ndarray) -> bytes:
    """
    產生縮圖 thumb.webp（320×240, WebP Q60）。
    """
    pil = _bgr_to_pil(frame_bgr)
    pil = pil.resize(THUMB_SIZE, Image.LANCZOS)
    return _encode_webp(pil, WEBP_THUMB_Q)


def make_evidence(
    frame_bgr: np.ndarray,
    bbox: list,
    label: str,
    score: float,
    mask: Optional[np.ndarray] = None,
) -> bytes:
    """
    產生證據圖 evidence.webp：
      - ROI 裁切（bbox 外擴 30%）
      - 若有 mask 則半透明覆蓋
      - 畫 bbox 框 + label/score 字樣
    """
    img_h, img_w = frame_bgr.shape[:2]

    # ── 1. 在原圖上畫 bbox + overlay ──
    vis = frame_bgr.copy()

    # mask overlay（可選）
    if mask is not None:
        m = mask.astype(np.uint8)
        if m.shape[:2] != (img_h, img_w):
            m = cv2.resize(m, (img_w, img_h), interpolation=cv2.INTER_NEAREST)
        color = LABEL_COLOR.get(label, DEFAULT_COLOR)
        overlay = np.zeros_like(vis, dtype=np.uint8)
        overlay[m > 0] = color
        vis = cv2.addWeighted(vis, 1.0, overlay, MASK_ALPHA, 0)

    x, y, w, h = bbox
    # bbox 已覆蓋 85% 以上圖面時（雙相機全景圖），跳過重複繪製外框
    # fuse_frames() 已在圖上畫好所有偵測框，不需再畫
    _full_frame = (w * h) >= (img_w * img_h * 0.85)
    if not _full_frame:
        color_rgb = LABEL_COLOR.get(label, DEFAULT_COLOR)
        color_bgr = (color_rgb[2], color_rgb[1], color_rgb[0])
        cv2.rectangle(vis, (x, y), (x + w, y + h), color_bgr, thickness=2)
        text = f"{label} {score:.2f}"
        font_scale = max(0.5, min(img_w, img_h) / 640)
        thickness = max(1, int(font_scale * 1.8))
        (tw, th), baseline = cv2.getTextSize(
            text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
        )
        ty = max(y - 6, th + baseline)
        cv2.rectangle(vis, (x, ty - th - baseline), (x + tw + 4, ty + baseline), color_bgr, -1)
        cv2.putText(vis, text, (x + 2, ty),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), thickness,
                    lineType=cv2.LINE_AA)

    # ── 2. 裁切 ROI ──
    x1, y1, x2, y2 = _pad_bbox(bbox, img_w, img_h)
    roi = vis[y1:y2, x1:x2]

    # 防呆：若 ROI 為空
    if roi.size == 0:
        roi = vis

    pil_roi = _bgr_to_pil(roi)
    return _encode_webp(pil_roi, WEBP_EVID_Q)


# ─────────────────────────────────────────────
# META 組裝
# ─────────────────────────────────────────────

def build_meta(
    label: str,
    bbox: list,
    gps: dict,
    score: float = 0.9,
    camera_id: str = "d435_a",
    level: str = "confirmed",
    note: str = "",
    event_id: Optional[str] = None,
    t_capture: Optional[float] = None,
) -> dict:
    """
    組裝符合 Hub 規格的 meta dict。
    timestamp 使用台北時區 +08:00。
    """
    now_ts = time.time()
    meta = {
        "event_id":   event_id or f"{label}_{int(now_ts * 1000)}",
        "timestamp":  _iso8601_taipei(),
        "type":       label,
        "level":      level,
        "confidence": round(float(score), 4),
        "uav_gps": {
            "lat": float(gps.get("lat", 0.0)),
            "lon": float(gps.get("lon", 0.0)),
            "alt": float(gps.get("alt", 0.0)),
        },
        "camera_id":  camera_id,
        "roi":        [int(v) for v in bbox],
        "t_capture":  round(float(t_capture or now_ts), 3),
        "t_infer_done": round(now_ts, 3),
        "t_send":     round(now_ts, 3),
        "note":       note,
    }
    return meta


# ─────────────────────────────────────────────
# 主要 API
# ─────────────────────────────────────────────

def send_alert(
    label: str,
    frame_bgr: np.ndarray,
    bbox: list,
    gps: dict,
    score: float = 0.9,
    mask: Optional[np.ndarray] = None,
    camera_id: str = "d435_a",
    level: str = "confirmed",
    note: str = "",
    event_id: Optional[str] = None,
    t_capture: Optional[float] = None,
) -> dict:
    """
    核心函式：組裝證據圖片 + meta，POST 到 Hub。

    參數
    ----
    label      : "fire" | "smoke" | "person"
    frame_bgr  : OpenCV BGR 影像 (np.ndarray, HxWx3 uint8)
    bbox       : [x, y, w, h]  偵測 ROI（整數像素座標）
    gps        : {"lat": float, "lon": float, "alt": float}
    score      : 信心分數 0~1（預設 0.9）
    mask       : HxW uint8/bool mask（可選，None 則跳過 overlay）
    camera_id  : 攝影機識別 ("d435_a" | "d435_b" | "fused")
    level      : "suspected" | "confirmed" | "critical"
    note       : 附加說明文字
    event_id   : 事件 UUID（None → 自動生成）
    t_capture  : 擷幀 unix timestamp（None → 使用當前時間）

    回傳
    ----
    dict: {"ok": bool, "status_code": int|None, "meta": dict, "error": str|None}
    """
    t0 = time.time()

    # ── 產圖 ──
    try:
        thumb_bytes = make_thumb(frame_bgr)
        evid_bytes  = make_evidence(frame_bgr, bbox, label, score, mask)
    except Exception as e:
        log.error(f"[send_alert] 圖像生成失敗: {e}")
        return {"ok": False, "status_code": None, "meta": {}, "error": str(e)}

    # ── 組 meta ──
    meta = build_meta(
        label=label, bbox=bbox, gps=gps,
        score=score, camera_id=camera_id,
        level=level, note=note,
        event_id=event_id, t_capture=t_capture,
    )
    # 用圖像生成後的時間更新 t_send
    meta["t_send"] = round(time.time(), 3)

    # ── POST ──
    headers = {"X-Auth-Token": AUTH_TOKEN}
    files = {
        "meta":     (None,          json.dumps(meta, ensure_ascii=False), "application/json"),
        "thumb":    ("thumb.webp",  thumb_bytes,  "image/webp"),
        "evidence": ("evidence.webp", evid_bytes, "image/webp"),
    }

    log.info(f"[send_alert] POST → {HUB_URL}  event_id={meta['event_id']}  label={label}")
    try:
        resp = requests.post(
            HUB_URL, headers=headers, files=files, timeout=POST_TIMEOUT
        )
        elapsed = time.time() - t0
        if resp.status_code == 200:
            log.info(
                f"[send_alert] ✅ 成功！status={resp.status_code}  "
                f"耗時={elapsed:.2f}s  thumb={len(thumb_bytes)/1024:.1f}KB  "
                f"evid={len(evid_bytes)/1024:.1f}KB"
            )
            try:
                log.info(f"[send_alert] Hub 回應: {resp.json()}")
            except Exception:
                log.info(f"[send_alert] Hub 回應(raw): {resp.text[:200]}")
            return {"ok": True, "status_code": resp.status_code, "meta": meta, "error": None}
        else:
            log.warning(
                f"[send_alert] ⚠️  非 200 回應 status={resp.status_code}  "
                f"body={resp.text[:200]}"
            )
            return {"ok": False, "status_code": resp.status_code, "meta": meta,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except requests.exceptions.Timeout:
        log.error(f"[send_alert] ❌ POST 超時（>{POST_TIMEOUT}s）")
        return {"ok": False, "status_code": None, "meta": meta, "error": "Timeout"}
    except requests.exceptions.ConnectionError as e:
        log.error(f"[send_alert] ❌ 連線失敗: {e}")
        return {"ok": False, "status_code": None, "meta": meta, "error": str(e)}
    except Exception as e:
        log.error(f"[send_alert] ❌ 未知錯誤: {e}")
        return {"ok": False, "status_code": None, "meta": meta, "error": str(e)}


# ─────────────────────────────────────────────
# --test 模式：假資料自動測試
# ─────────────────────────────────────────────

def _make_fake_frame(w: int = 640, h: int = 480) -> np.ndarray:
    """
    生成一張假的 BGR 測試圖（彩色漸層 + 格狀線條）。
    若不需要特效圖案，只要純色也可。
    """
    img = np.zeros((h, w, 3), dtype=np.uint8)
    # 彩色漸層
    for i in range(h):
        img[i, :, 0] = int(i / h * 200) + 30        # B
        img[i, :, 2] = int((h - i) / h * 200) + 30  # R
    for j in range(w):
        img[:, j, 1] = int(j / w * 180) + 40        # G

    # 格狀線條
    for step in range(0, h, 60):
        cv2.line(img, (0, step), (w, step), (255, 255, 255), 1)
    for step in range(0, w, 80):
        cv2.line(img, (step, 0), (step, h), (255, 255, 255), 1)

    # 模擬「火焰」橘色區塊
    cx, cy = w // 2 - 40, h // 2 - 30
    pts = np.array([
        [cx, cy + 80], [cx - 35, cy + 50], [cx - 20, cy + 30],
        [cx - 50, cy], [cx - 10, cy + 10], [cx, cy - 40],
        [cx + 10, cy + 10], [cx + 50, cy], [cx + 20, cy + 30],
        [cx + 35, cy + 50],
    ], np.int32)
    cv2.fillPoly(img, [pts], (0, 100, 255))     # BGR 橘紅
    cv2.polylines(img, [pts], True, (0, 50, 200), 2)
    cv2.putText(img, "SIMULATED FIRE", (cx - 80, cy - 60),
                cv2.FONT_HERSHEY_DUPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)
    return img


def _make_fake_mask(frame: np.ndarray, bbox: list) -> np.ndarray:
    """在 bbox 區域內生成橢圓形假 mask。"""
    h, w = frame.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    x, y, bw, bh = bbox
    cx, cy = x + bw // 2, y + bh // 2
    cv2.ellipse(mask, (cx, cy), (bw // 2, bh // 2), 0, 0, 360, 1, -1)
    return mask


def run_test(image_path: Optional[str] = None):
    """
    --test 模式執行函式。
    用假圖（或指定圖片）+ 假 GPS 送一筆告警到 Hub。
    """
    log.info("=" * 60)
    log.info("  [TEST MODE]  假資料告警測試")
    log.info(f"  Hub URL    : {HUB_URL}")
    log.info(f"  Auth Token : {AUTH_TOKEN[:12]}...（前12字元）")
    log.info("=" * 60)

    # ── 載入或生成測試影像 ──
    if image_path and Path(image_path).is_file():
        frame_bgr = cv2.imread(image_path)
        if frame_bgr is None:
            log.warning(f"無法讀取圖片 {image_path}，改用生成假圖")
            frame_bgr = _make_fake_frame()
        else:
            log.info(f"使用測試圖片: {image_path}  size={frame_bgr.shape[1]}x{frame_bgr.shape[0]}")
    else:
        frame_bgr = _make_fake_frame()
        log.info("使用程式生成的假圖（640x480）")

    img_h, img_w = frame_bgr.shape[:2]

    # ── 假 bbox（圖像中央偏左）──
    bx = max(0, img_w // 2 - 120)
    by = max(0, img_h // 2 - 80)
    bw = min(240, img_w - bx)
    bh = min(160, img_h - by)
    bbox = [bx, by, bw, bh]

    # ── 假 mask ──
    mask = _make_fake_mask(frame_bgr, bbox)

    # ── 假 GPS（台灣某山區：合歡山附近）──
    fake_gps = {
        "lat":  24.1415,
        "lon": 121.2829,
        "alt":  3158.0,
    }

    # ── 呼叫 send_alert ──
    result = send_alert(
        label="fire",
        frame_bgr=frame_bgr,
        bbox=bbox,
        gps=fake_gps,
        score=0.92,
        mask=mask,
        camera_id="d435_a",
        level="confirmed",
        note="[TEST] Fake fire event from thor_send_alert.py --test",
        event_id=f"test_{uuid.uuid4().hex[:8]}",
        t_capture=time.time(),
    )

    # ── 印出摘要 ──
    log.info("-" * 60)
    if result["ok"]:
        log.info("✅  測試成功！請到 Hub Dashboard 確認事件卡片")
        log.info(f"    event_id  : {result['meta'].get('event_id')}")
        log.info(f"    timestamp : {result['meta'].get('timestamp')}")
        log.info(f"    GPS       : {result['meta'].get('uav_gps')}")
    else:
        log.error(f"❌  測試失敗！錯誤: {result['error']}")
        log.error("    請確認：")
        log.error("      1) WireGuard VPN 已啟動（ping 10.0.0.7）")
        log.error("      2) Hub 服務正在執行（curl http://10.0.0.7:8080/api/v1/alerts）")
        log.error("      3) AUTH_TOKEN 與 Hub 設定一致")
    log.info("-" * 60)
    return result["ok"]


# ─────────────────────────────────────────────
# 本機存檔（離線 queue 輔助用）
# ─────────────────────────────────────────────

def save_to_queue(
    meta: dict,
    thumb_bytes: bytes,
    evid_bytes: bytes,
    queue_dir: str = "queue",
) -> Path:
    """
    將事件存到本地 queue/<event_id>/ 資料夾。
    供後續背景重送使用。
    """
    event_id = meta.get("event_id", f"unknown_{int(time.time()*1000)}")
    out_dir = Path(queue_dir) / event_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "thumb.webp").write_bytes(thumb_bytes)
    (out_dir / "evidence.webp").write_bytes(evid_bytes)
    log.info(f"[queue] 已存 {out_dir}")
    return out_dir


# ─────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────

def main():
    global HUB_URL, AUTH_TOKEN  # 必須在函式最頂端宣告，才能在下方賦值

    parser = argparse.ArgumentParser(
        description="thor_send_alert.py — UAV 事件式告警回傳工具"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="假資料測試模式：用假圖 + 假 GPS 送一筆事件到 Hub"
    )
    parser.add_argument(
        "--image", type=str, default=None,
        help="[--test 選用] 指定測試用圖片路徑（不指定則自動生成假圖）"
    )
    parser.add_argument(
        "--hub-url", type=str, default=None,
        help="覆蓋 Hub URL（預設: http://10.0.0.7:8080/api/v1/alerts）"
    )
    parser.add_argument(
        "--token", type=str, default=None,
        help="覆蓋 Auth Token（預設使用程式內硬編值）"
    )
    args = parser.parse_args()

    if args.hub_url:
        HUB_URL = args.hub_url
    if args.token:
        AUTH_TOKEN = args.token

    if args.test:
        ok = run_test(image_path=args.image)
        raise SystemExit(0 if ok else 1)
    else:
        print(__doc__)
        print("\n提示：以 --test 旗標執行假資料測試。")
        print("      或在你的主程式中 import send_alert 使用。")


if __name__ == "__main__":
    main()
