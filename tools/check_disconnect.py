"""Print DISCONNECTED if the game is sitting on the disconnect notice.

Read-only: one screen grab plus the OCR recovery.disconnected() already
uses, so it can run alongside a live driver.py run.
"""
import sys

sys.path.insert(0, r"C:\Users\tl456\Desktop\cabal_trade\src_1080p")

try:
    import recovery
    where = recovery.disconnected()
    if where is not None:
        print("DISCONNECTED")
except Exception:
    pass
