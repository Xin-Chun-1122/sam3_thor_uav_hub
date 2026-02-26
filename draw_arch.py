"""
UAV 雙目視覺事件式告警回傳系統 — 系統架構圖產生器
輸出：system_arch.png  (1920×1080, 300 DPI)
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.patheffects as pe
from matplotlib import font_manager

# 注冊 Noto Sans CJK TC 字型（支援繁體中文）
_CJK_REGULAR = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
_CJK_BOLD    = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
for _fp in [_CJK_REGULAR, _CJK_BOLD]:
    font_manager.fontManager.addfont(_fp)
plt.rcParams["font.family"] = ["Noto Sans CJK JP", "DejaVu Sans"]

# ─── 色盤 ─────────────────────────────────────────────────────────────────────
BG        = "#0d1117"
BG_UAV    = "#161b22"
BG_HUB    = "#161b22"
BG_NET    = "#0d1117"

C_CAM     = "#1f6feb"   # 藍 — 相機
C_FC      = "#388bfd"   # 淺藍 — 飛控
C_SAM     = "#f78166"   # 橘紅 — SAM3
C_FSM     = "#d2a8ff"   # 紫 — FSM
C_FUSE    = "#56d364"   # 綠 — 影像融合
C_SEND    = "#f0883e"   # 橘 — 傳輸
C_QUEUE   = "#8b949e"   # 灰 — Queue
C_HUB     = "#1f6feb"   # 藍 — Hub
C_DASH    = "#56d364"   # 綠 — Dashboard
C_WIRE    = "#3fb950"   # 亮綠 — WireGuard

TXT_W     = "#e6edf3"
TXT_DIM   = "#8b949e"

# ─── 畫布 ─────────────────────────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(19.2, 10.8), dpi=100)
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)
ax.set_xlim(0, 19.2)
ax.set_ylim(0, 10.8)
ax.axis("off")

# ─── 工具函數 ──────────────────────────────────────────────────────────────────
def rbox(ax, x, y, w, h, color, alpha=0.18, radius=0.18, lw=1.5, edge=None):
    """圓角填色方塊"""
    edge = edge or color
    box = FancyBboxPatch((x, y), w, h,
                         boxstyle=f"round,pad=0,rounding_size={radius}",
                         linewidth=lw, edgecolor=edge,
                         facecolor=color, alpha=alpha,
                         zorder=2)
    ax.add_patch(box)
    return box

def rbox_solid(ax, x, y, w, h, color, radius=0.15, lw=1.5, alpha=1.0):
    """實心圓角方塊（無透明度）"""
    box = FancyBboxPatch((x, y), w, h,
                         boxstyle=f"round,pad=0,rounding_size={radius}",
                         linewidth=lw, edgecolor=color,
                         facecolor=color, alpha=alpha,
                         zorder=3)
    ax.add_patch(box)
    return box

def label(ax, x, y, text, size=9, color=TXT_W, bold=False, ha="center", va="center", zorder=5):
    weight = "bold" if bold else "normal"
    ax.text(x, y, text, fontsize=size, color=color,
            ha=ha, va=va, fontweight=weight, zorder=zorder)

def sublabel(ax, x, y, text, size=7.5, color=TXT_DIM, ha="center", va="center"):
    ax.text(x, y, text, fontsize=size, color=color,
            ha=ha, va=va, zorder=5)

def arrow(ax, x1, y1, x2, y2, color="#8b949e", lw=1.5, style="-|>", zorder=4, head=8):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle=style,
                                color=color, lw=lw,
                                mutation_scale=head),
                zorder=zorder)

def dashed_arrow(ax, x1, y1, x2, y2, color="#8b949e", lw=1.5, zorder=4):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle="-|>",
                                color=color, lw=lw,
                                linestyle="dashed",
                                mutation_scale=8),
                zorder=zorder)

# ─── 大區塊背景 ────────────────────────────────────────────────────────────────
# UAV 側
rbox(ax, 0.3, 1.0, 11.4, 9.4, BG_UAV, alpha=0.45, radius=0.35, lw=2, edge="#30363d")
label(ax, 6.0, 10.15, "無人機端  (Jetson AGX Thor · CUDA 13.0 · Docker)",
      size=11, bold=True, color="#58a6ff")

# Hub 側
rbox(ax, 13.5, 3.2, 5.4, 6.0, BG_HUB, alpha=0.45, radius=0.35, lw=2, edge="#30363d")
label(ax, 16.2, 9.0, "Hub 地面站", size=11, bold=True, color="#58a6ff")
sublabel(ax, 16.2, 8.7, "家用筆電  10.0.0.7:8080", size=8.5)

# ─── 標題 ─────────────────────────────────────────────────────────────────────
label(ax, 9.6, 10.55,
      "無人機雙目視覺事件式告警回傳系統  —  System Architecture",
      size=14, bold=True, color=TXT_W)

# ═══════════════════════════════════════════════════════════════════════════════
# UAV 側  元件
# ═══════════════════════════════════════════════════════════════════════════════

# --- 相機 A ------------------------------------------------------------------
rbox_solid(ax, 0.55, 7.8, 2.1, 1.5, C_CAM, alpha=0.25)
rbox(ax, 0.55, 7.8, 2.1, 1.5, C_CAM, alpha=0.0, lw=2, edge=C_CAM)
label(ax, 1.60, 8.85, "[CAM]  D435 Camera A", size=9, bold=True, color=C_CAM)
sublabel(ax, 1.60, 8.58, "SN: 332522075298", size=7.5)
sublabel(ax, 1.60, 8.35, "640×480 @ 15 fps", size=7.5)
sublabel(ax, 1.60, 8.12, "BGR Stream  (USB 3.2)", size=7.5)

# --- 相機 B ------------------------------------------------------------------
rbox_solid(ax, 0.55, 5.95, 2.1, 1.5, C_CAM, alpha=0.25)
rbox(ax, 0.55, 5.95, 2.1, 1.5, C_CAM, alpha=0.0, lw=2, edge=C_CAM)
label(ax, 1.60, 7.00, "[CAM]  D435 Camera B", size=9, bold=True, color=C_CAM)
sublabel(ax, 1.60, 6.73, "SN: 332522073133", size=7.5)
sublabel(ax, 1.60, 6.50, "640×480 @ 15 fps", size=7.5)
sublabel(ax, 1.60, 6.27, "BGR Stream  (USB 3.2)", size=7.5)

# --- 飛控 -------------------------------------------------------------------
rbox_solid(ax, 0.55, 4.1, 2.1, 1.5, C_FC, alpha=0.22)
rbox(ax, 0.55, 4.1, 2.1, 1.5, C_FC, alpha=0.0, lw=2, edge=C_FC)
label(ax, 1.60, 5.15, "[GPS]  ArduPilot 飛控", size=9, bold=True, color=C_FC)
sublabel(ax, 1.60, 4.88, "/dev/ttyACM1  115200", size=7.5)
sublabel(ax, 1.60, 4.65, "GLOBAL_POSITION_INT", size=7.5)
sublabel(ax, 1.60, 4.42, "GPS_RAW_INT (fix_type)", size=7.5)

# --- MAVLink 執行緒 -------------------------------------------------------
rbox_solid(ax, 0.55, 2.4, 2.1, 1.35, "#0d419d", alpha=0.35)
rbox(ax, 0.55, 2.4, 2.1, 1.35, C_FC, alpha=0.0, lw=1.5, edge=C_FC)
label(ax, 1.60, 3.35, "MAVLink Thread", size=9, bold=True, color=C_FC)
sublabel(ax, 1.60, 3.10, "_mav_reader_thread()", size=7.5)
sublabel(ax, 1.60, 2.88, "GPS Cache + auto-reconnect", size=7.5)
sublabel(ax, 1.60, 2.65, "home fallback  (NTHU)", size=7.5)

# 飛控 → MAVLink 執行緒
arrow(ax, 1.60, 4.10, 1.60, 3.75, color=C_FC, lw=1.5)

# --- pyrealsense2 frame buffer ─────────────────────────────────────────────
rbox(ax, 3.05, 6.8, 2.5, 1.2, C_CAM, alpha=0.2, lw=1.5, edge=C_CAM)
label(ax, 4.30, 7.65, "pyrealsense2", size=9, bold=True, color=C_CAM)
sublabel(ax, 4.30, 7.40, "BGR Frame Buffer", size=8)
sublabel(ax, 4.30, 7.18, "wait_for_frames(3000ms)", size=7.5)

# 相機 A → frame buffer
arrow(ax, 2.65, 8.55, 3.05, 7.55, color=C_CAM, lw=1.5)
# 相機 B → frame buffer
arrow(ax, 2.65, 6.70, 3.05, 7.15, color=C_CAM, lw=1.5)

# --- Main Loop ──────────────────────────────────────────────────────────────
rbox(ax, 3.10, 4.9, 2.4, 1.4, "#58a6ff", alpha=0.22, lw=2, edge="#58a6ff")
label(ax, 4.30, 5.90, "_main_loop_body()", size=9.5, bold=True, color="#58a6ff")
sublabel(ax, 4.30, 5.65, "主迴圈  2 Hz (0.5s interval)", size=8)
sublabel(ax, 4.30, 5.43, "infer → FSM → fuse → send", size=7.5)
sublabel(ax, 4.30, 5.20, "run_realsense_mode()", size=7.5)

# frame buffer → main loop
arrow(ax, 4.30, 6.80, 4.30, 6.30, color=C_CAM, lw=1.5)
# MAVLink thread → main loop (GPS)
arrow(ax, 2.65, 3.07, 3.10, 5.10, color=C_FC, lw=1.3)
ax.text(2.72, 4.35, "get_gps()", fontsize=7.2, color=C_FC, rotation=60, zorder=5)

# --- SAM3 推論 ───────────────────────────────────────────────────────────────
rbox(ax, 5.95, 7.05, 2.9, 2.25, C_SAM, alpha=0.22, lw=2, edge=C_SAM)
label(ax, 7.40, 9.00, "[AI]  Meta SAM3", size=10, bold=True, color=C_SAM)
label(ax, 7.40, 8.72, "_infer_sam3_real()", size=8.5, bold=False, color=C_SAM)
sublabel(ax, 7.40, 8.48, "Text-Grounding  (Zero-shot)", size=7.8)
sublabel(ax, 7.40, 8.28, 'Prompts: "fire" / "smoke" / "person"', size=7.5)
sublabel(ax, 7.40, 8.08, "Resolution: 1008 px  |  FP16 autocast", size=7.5)
sublabel(ax, 7.40, 7.88, "3.45 GB  |  set_image() × 1 per frame", size=7.5)
sublabel(ax, 7.40, 7.68, "→  masks + scores per label", size=7.5)
sublabel(ax, 7.40, 7.30, "Score threshold: 0.45", size=7.5)

# main loop → SAM3
arrow(ax, 5.50, 5.60, 6.60, 7.05, color=C_SAM, lw=1.5)

# --- 影像融合 ─────────────────────────────────────────────────────────────────
rbox(ax, 5.95, 5.4, 2.9, 1.3, C_FUSE, alpha=0.2, lw=1.5, edge=C_FUSE)
label(ax, 7.40, 6.35, "[IMG]  fuse_frames()", size=9, bold=True, color=C_FUSE)
sublabel(ax, 7.40, 6.10, "hstack  cam_A | sep | cam_B", size=7.8)
sublabel(ax, 7.40, 5.88, "1284 × 480  (4px separator)", size=7.8)
sublabel(ax, 7.40, 5.65, "CAM-A / CAM-B 標籤疊加", size=7.5)

# main loop → fuse
arrow(ax, 5.50, 5.40, 6.60, 5.95, color=C_FUSE, lw=1.5)

# --- EventStateMachine ────────────────────────────────────────────────────────
rbox(ax, 5.95, 2.4, 2.9, 2.65, C_FSM, alpha=0.2, lw=2, edge=C_FSM)
label(ax, 7.40, 4.75, "[FSM]  EventStateMachine", size=9.5, bold=True, color=C_FSM)
sublabel(ax, 7.40, 4.50, "5 狀態 FSM  (per label)", size=8)

# FSM states
states = [
    ("Normal",     "#8b949e", 6.20, 4.18),
    ("Suspected",  "#d2a8ff", 6.20, 3.85),
    ("Confirmed",  "#f0883e", 6.20, 3.52),
    ("Tracking",   "#56d364", 6.20, 3.19),
    ("Lost",       "#ff7b72", 6.20, 2.86),
]
for name, col, sx, sy in states:
    rbox(ax, sx, sy - 0.12, 1.55, 0.27, col, alpha=0.35, radius=0.06, lw=1, edge=col)
    label(ax, sx + 0.775, sy, name, size=7.5, color=col, bold=True)

# FSM params
sublabel(ax, 8.50, 4.18, "M1=5 / N1=2", size=7.2)
sublabel(ax, 8.50, 3.85, "SM_M2=10 / N2=4", size=7.2)
sublabel(ax, 8.50, 3.52, "T_send=2.0s", size=7.2)
sublabel(ax, 8.50, 3.19, "T_lost=10s", size=7.2)

# SAM3 → FSM
arrow(ax, 7.40, 7.05, 7.40, 5.05, color=C_FSM, lw=1.5)

# fuse → FSM (fused frame)
arrow(ax, 7.40, 5.40, 7.40, 5.05, color=C_FUSE, lw=1.2)

# ─── should_send? ─────────────────────────────────────────────────────────────
rbox(ax, 6.15, 1.3, 2.5, 0.75, "#f0883e", alpha=0.25, lw=1.5, edge="#f0883e")
label(ax, 7.40, 1.73, "should_send()?", size=9, bold=True, color="#f0883e")
sublabel(ax, 7.40, 1.50, "Confirmed / Tracking 狀態時觸發", size=7.5)

# FSM → should_send
arrow(ax, 7.40, 2.40, 7.40, 2.05, color="#f0883e", lw=1.5)

# --- send_alert() ─────────────────────────────────────────────────────────────
rbox(ax, 9.3, 4.9, 2.5, 2.7, C_SEND, alpha=0.22, lw=2, edge=C_SEND)
label(ax, 10.55, 7.28, "[TX]  send_alert()", size=9.5, bold=True, color=C_SEND)
sublabel(ax, 10.55, 7.03, "POST multipart/form-data", size=8)
sublabel(ax, 10.55, 6.80, "meta.json  →  label/score/GPS/time", size=7.5)
sublabel(ax, 10.55, 6.57, "thumb.webp  (320×240, Q60)", size=7.5)
sublabel(ax, 10.55, 6.33, "evidence.webp (ROI, Q75)", size=7.5)
sublabel(ax, 10.55, 6.10, "Token Auth Header", size=7.5)
sublabel(ax, 10.55, 5.87, "thor_send_alert.py", size=7.5)
sublabel(ax, 10.55, 5.62, "Timeout 15s  /  retry 3×", size=7.5)
sublabel(ax, 10.55, 5.35, "make_evidence()  (bbox + mask)", size=7.5)

# should_send → send_alert
arrow(ax, 8.65, 1.67, 10.55, 4.90, color=C_SEND, lw=1.5)

# FSM → send_alert (直接)
arrow(ax, 8.85, 3.70, 9.30, 6.20, color=C_SEND, lw=1.3)

# --- Offline Queue ─────────────────────────────────────────────────────────────
rbox(ax, 9.3, 2.4, 2.5, 1.3, C_QUEUE, alpha=0.22, lw=1.5, edge=C_QUEUE)
label(ax, 10.55, 3.37, "[Q]  save_to_queue()", size=9, bold=True, color=C_QUEUE)
sublabel(ax, 10.55, 3.12, "離線容錯佇列", size=8)
sublabel(ax, 10.55, 2.90, "hub_queue/<timestamp>/", size=7.5)
sublabel(ax, 10.55, 2.67, "自動重傳 on reconnect", size=7.5)

# send_alert 失敗 → queue
dashed_arrow(ax, 10.55, 4.90, 10.55, 3.70, color=C_QUEUE, lw=1.3)
ax.text(10.65, 4.32, "失敗", fontsize=7.5, color=C_QUEUE, zorder=5)

# ═══════════════════════════════════════════════════════════════════════════════
# WireGuard VPN 通道
# ═══════════════════════════════════════════════════════════════════════════════
# VPN 背景帶
rbox(ax, 11.85, 4.3, 1.6, 2.0, C_WIRE, alpha=0.10, radius=0.15, lw=1.5, edge=C_WIRE)
label(ax, 12.65, 5.88, "[VPN] WireGuard", size=8.5, bold=True, color=C_WIRE)
label(ax, 12.65, 5.60, "VPN Tunnel", size=8.5, bold=True, color=C_WIRE)
sublabel(ax, 12.65, 5.33, "10.0.0.20 → 10.0.0.7", size=7.5)
sublabel(ax, 12.65, 5.10, "ChaCha20-Poly1305", size=7.5)
sublabel(ax, 12.65, 4.87, "RTT ~230ms (4G LTE)", size=7.5)
sublabel(ax, 12.65, 4.62, "< 300 Kbps avg", size=7.5)

# send_alert → VPN → Hub
arrow(ax, 11.80, 5.90, 13.50, 6.50, color=C_WIRE, lw=2.5, head=10)
arrow(ax, 11.80, 5.60, 13.50, 5.50, color=C_WIRE, lw=2.5, head=10)

# ═══════════════════════════════════════════════════════════════════════════════
# Hub 側 元件
# ═══════════════════════════════════════════════════════════════════════════════

# --- Flask Hub Server ─────────────────────────────────────────────────────────
rbox(ax, 13.6, 6.7, 5.0, 2.0, C_HUB, alpha=0.22, lw=2, edge=C_HUB)
label(ax, 16.10, 8.40, "[SRV]  Flask Hub Server", size=10, bold=True, color=C_HUB)
label(ax, 16.10, 8.12, "hub_server.py", size=8.5, color="#58a6ff")
sublabel(ax, 16.10, 7.88, "POST /api/v1/alerts  →  Bearer Token 驗證", size=8)
sublabel(ax, 16.10, 7.65, "meta.json + thumb.webp + evidence.webp 寫入", size=7.8)
sublabel(ax, 16.10, 7.42, "hub_events/<event_id>/  (本地儲存)", size=7.8)
sublabel(ax, 16.10, 7.18, "location_label()  →  地點中文名稱映射", size=7.8)
sublabel(ax, 16.10, 6.95, "_primary_display_label()  fire > smoke > person", size=7.8)

# --- Web Dashboard ────────────────────────────────────────────────────────────
rbox(ax, 13.6, 4.9, 5.0, 1.55, C_DASH, alpha=0.18, lw=2, edge=C_DASH)
label(ax, 16.10, 6.15, "[WEB]  Web Dashboard", size=10, bold=True, color=C_DASH)
sublabel(ax, 16.10, 5.90, "GET /  →  HTML  (自動 10s 刷新)", size=8)
sublabel(ax, 16.10, 5.67, "Evidence 全寬展示 (max 960px)", size=7.8)
sublabel(ax, 16.10, 5.45, "GPS 連結  →  Google Maps", size=7.8)
sublabel(ax, 16.10, 5.22, "火/煙/人  DETECTION badge (色碼區分)", size=7.8)

# Hub Server → Dashboard
arrow(ax, 16.10, 6.70, 16.10, 6.45, color=C_DASH, lw=1.5)

# --- 儲存 ─────────────────────────────────────────────────────────────────────
rbox(ax, 13.6, 3.3, 5.0, 1.3, C_QUEUE, alpha=0.18, lw=1.5, edge=C_QUEUE)
label(ax, 16.10, 4.30, "[DB]  hub_events/ 儲存", size=9, bold=True, color=C_QUEUE)
sublabel(ax, 16.10, 4.05, "meta.json  /  thumb.webp  /  evidence.webp", size=8)
sublabel(ax, 16.10, 3.82, "<event_id>  =  label_YYYYmmdd_HHMMSS_uuid4", size=7.8)
sublabel(ax, 16.10, 3.58, "永久保存  (手動清理)", size=7.8)

# Hub → 儲存
arrow(ax, 16.10, 6.70, 16.10, 4.60, color=C_QUEUE, lw=1.3)

# ═══════════════════════════════════════════════════════════════════════════════
# 圖例 + 版本
# ═══════════════════════════════════════════════════════════════════════════════
legend_items = [
    (C_CAM,   "相機 / 影像採集"),
    (C_FC,    "MAVLink / GPS"),
    (C_SAM,   "SAM3 推論引擎"),
    (C_FSM,   "事件狀態機 FSM"),
    (C_FUSE,  "影像融合輸出"),
    (C_SEND,  "告警傳輸"),
    (C_WIRE,  "WireGuard VPN"),
    (C_HUB,   "Hub 伺服器"),
    (C_DASH,  "Dashboard"),
    (C_QUEUE, "容錯佇列/儲存"),
]
lx, ly = 0.38, 0.85
for i, (col, name) in enumerate(legend_items):
    xi = lx + i * 1.88
    rbox(ax, xi, ly - 0.12, 0.22, 0.22, col, alpha=0.85, radius=0.04, lw=1, edge=col)
    ax.text(xi + 0.28, ly, name, fontsize=7.2, color=TXT_DIM, va="center", zorder=5)

ax.text(18.9, 0.22, "v2.0 · 2026-02-25", fontsize=7.5, color=TXT_DIM,
        ha="right", va="bottom", zorder=5)
ax.text(0.38, 0.22, "UAV Dual-Camera Event-Driven Alert System — System Architecture",
        fontsize=7.5, color=TXT_DIM, ha="left", va="bottom", zorder=5)

# ─── 儲存 ─────────────────────────────────────────────────────────────────────
out = "/home/alan/xin/uav_hub/system_arch.png"
fig.savefig(out, dpi=150, bbox_inches="tight",
            facecolor=BG, edgecolor="none")
print(f"✅  已儲存：{out}")
print(f"   尺寸：1920×1080 等效，DPI 150")
