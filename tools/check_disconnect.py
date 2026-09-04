import sys

sys.path.insert(0, r"C:\Users\tl456\Desktop\cabal_trade\src_1080p")

try:
    import recovery
    where = recovery.disconnected()
    if where is not None:
        print("DISCONNECTED")
except Exception:
    pass
