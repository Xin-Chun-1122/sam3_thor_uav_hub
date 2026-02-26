# decord_stub.py — 放在工作目錄，讓 SAM3 inference-only 模式不需要安裝 decord
# SAM3 的 train/data/sam3_image_dataset.py 在 module 頂層 import decord，
# 但推論時完全不會呼叫到 VideoReader，所以用 stub 讓 import 靜默通過即可。

class _FakeCtx:
    pass

def cpu(num_threads=0):
    return _FakeCtx()

class VideoReader:
    """Stub VideoReader — 推論時不會被呼叫，只讓 import 不報錯。"""
    def __init__(self, *a, **kw):
        raise RuntimeError("decord.VideoReader stub: 僅供推論用途，不支援影片讀取")
    def __len__(self): return 0
    def __getitem__(self, idx): raise RuntimeError("decord stub")
