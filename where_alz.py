import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
import calibration as C

im = C.grab()
w, h = im.size
print("screen          :", im.size)
print("client rect     :", C._client_rect())
print("resolution key  :", C.resolution_key())
band = C._box(C.ALZ_SEARCH_F)
print("alz_search band :", band)
print("  found in band :", C.find_alz(im, band))

wide = (w // 2, 0, w, h)
hit = C.find_alz(im, wide)
print("right half sweep:", hit)
if hit:
    print("  reads         :",
          repr(C.read_line(im, (hit[0] - 4, hit[1] - 6, hit[2] + 40, hit[3] + 6))))
    x, y, cw, ch = C._client_rect()
    print("  as fractions  : [%.4f, %.4f, %.4f, %.4f]"
          % ((hit[0] - x) / cw, (hit[1] - y) / ch,
             (hit[2] - x) / cw, (hit[3] - y) / ch))
else:
    print("  nothing gold and digit-shaped in the right half.")
    print("  If the Inventory panel is not open in game, open it (I) and run")
    print("  this again -- the balance only shows while that panel is up.")

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "alz_probe.png")
im.save(out)
print("saved the whole screen to", out)
