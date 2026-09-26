import ctypes

TRIPPED = []
BLOCK = ("SendInput", "SetCursorPos", "keybd_event", "mouse_event",
         "SetForegroundWindow", "ShowWindow", "BringWindowToTop", "SetFocus",
         "SetActiveWindow", "AttachThreadInput", "PostMessageW", "SendMessageW",
         "PostMessageA", "SendMessageA", "SwitchToThisWindow")


class GameInput(AssertionError):
    pass


def _raiser(name):
    def blocked(*a, **k):
        TRIPPED.append(name)
        raise GameInput(f"a test tried to send real input to the game: {name}")
    return blocked


def arm():
    import open_inventory
    for lib in (open_inventory._user32, ctypes.windll.user32):
        for name in BLOCK:
            setattr(lib, name, _raiser(name))
    return TRIPPED
