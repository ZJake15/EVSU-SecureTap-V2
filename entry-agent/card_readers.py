"""Which card reader a tap came from - the entrance's or the exit's.

The readers are keyboard-like USB devices: a tap "types" the card number and
Enter into the gate monitor's hidden text box (ui.py), and the box itself
can't tell which reader typed it. Windows can: its raw input feature reports
every key press together with the device that sent it. This module listens
for those reports on its own hidden window and remembers which device typed
most recently, so a tap can be put down to its reader.

One reader is set as the EXIT reader (the gate monitor's "Cameras & card
readers" window: "Set exit reader", then a tap on it); every other reader
counts as the entrance's - so a gate with one reader works exactly as before.
Readers are told apart by the USB port they're plugged into (two readers of
the same model look identical otherwise), so each must stay in its port.
The choice is kept in card_readers.json in the data folder.

Without raw input (not Windows, or it couldn't start) every tap counts as
the entrance's.
"""

import json
import re
import threading
import time
from collections import Counter, deque

from config import data_file
from lanes import ENTRY, EXIT

CHOICE_FILE = "card_readers.json"
# A card number is typed within a few hundredths of a second; the keys
# looked at for a tap are those of the last BURST_SECONDS before its Enter.
BURST_SECONDS = 1.0

_WM_INPUT = 0x00FF
_RID_INPUT = 0x10000003
_RIM_TYPEKEYBOARD = 1
_RIDEV_INPUTSINK = 0x00000100
_RIDI_DEVICENAME = 0x20000007
_RI_KEY_BREAK = 0x01
_HWND_MESSAGE = -3


def describe(device_name):
    """A short, readable name for a reader's device path, e.g. "USB FFFF:0035
    (port 7&1A2B)". The port part is what tells two readers of the same model apart."""
    if not device_name:
        return "Unknown reader"
    match = re.search(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})[^#]*#([^#]+)", device_name, re.IGNORECASE)
    if not match:
        return "Reader"
    vid, pid, instance = match.groups()
    port = "&".join(instance.split("&")[:2]).upper()
    return f"USB {vid.upper()}:{pid.upper()} (port {port})"


class CardReaders:
    def __init__(self, choice_path=None):
        self._choice_path = choice_path or data_file(CHOICE_FILE)
        self._keys = deque(maxlen=256)  # (monotonic time, device name) per key press
        self._names = {}  # raw input device handle -> device name
        self.available = False
        self.awaiting_exit_tap = False
        self.exit_reader = self._load()

    # ---- the exit reader -------------------------------------------------

    def _load(self):
        try:
            with open(self._choice_path, encoding="utf-8") as fh:
                return (json.load(fh).get("exit_reader") or "").strip() or None
        except (OSError, ValueError, AttributeError):
            return None

    def _save(self):
        try:
            with open(self._choice_path, "w", encoding="utf-8") as fh:
                json.dump({"exit_reader": self.exit_reader}, fh)
        except OSError:
            pass  # setting it again next time still works

    def assign_exit_from_last_tap(self):
        """Makes the reader that typed the latest tap the exit reader. Returns
        its device name, or None if no reader could be told apart."""
        self.awaiting_exit_tap = False
        device = self.last_tap_device()
        if not device:
            return None
        self.exit_reader = device
        self._save()
        return device

    def clear_exit_reader(self):
        self.awaiting_exit_tap = False
        self.exit_reader = None
        self._save()

    def status(self):
        """For the gate monitor: {"available", "exit_reader" (readable name or None), "waiting"}."""
        return {"available": self.available, "exit_reader": describe(self.exit_reader) if self.exit_reader else None,
                "waiting": self.awaiting_exit_tap}

    # ---- which reader typed ------------------------------------------------

    def note_key(self, device_name, at=None):
        """One key press from `device_name` (called by the raw input thread; tests call it directly)."""
        self._keys.append((time.monotonic() if at is None else at, device_name or ""))

    def last_tap_device(self, now=None):
        """The device that typed the most keys in the last BURST_SECONDS -
        the reader that just typed a card number - or None."""
        now = time.monotonic() if now is None else now
        recent = Counter(name for at, name in list(self._keys) if now - at <= BURST_SECONDS and name)
        return recent.most_common(1)[0][0] if recent else None

    def direction_of_last_tap(self, now=None):
        if self.exit_reader and self.last_tap_device(now) == self.exit_reader:
            return EXIT
        return ENTRY

    # ---- Windows raw input ---------------------------------------------------

    def start(self):
        """Starts listening on its own thread. Returns at once; `available`
        says (within a moment) whether Windows took the registration."""
        ready = threading.Event()
        threading.Thread(target=self._run, args=(ready,), daemon=True, name="card-readers").start()
        ready.wait(2.0)
        return self.available

    def _run(self, ready):
        try:
            self._listen(ready)
        except Exception:
            self.available = False
            ready.set()

    def _listen(self, ready):
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        lresult = ctypes.c_ssize_t
        wndproc_type = ctypes.WINFUNCTYPE(lresult, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", wndproc_type), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        class RAWINPUTDEVICE(ctypes.Structure):
            _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT), ("dwFlags", wintypes.DWORD),
                        ("hwndTarget", wintypes.HWND)]

        class RAWINPUTHEADER(ctypes.Structure):
            _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD), ("hDevice", wintypes.HANDLE),
                        ("wParam", wintypes.WPARAM)]

        class RAWKEYBOARD(ctypes.Structure):
            _fields_ = [("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT), ("Reserved", wintypes.USHORT),
                        ("VKey", wintypes.USHORT), ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG)]

        class RAWINPUT(ctypes.Structure):
            _fields_ = [("header", RAWINPUTHEADER), ("keyboard", RAWKEYBOARD)]

        user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.restype = lresult
        user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
        user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.RegisterRawInputDevices.argtypes = (ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT)
        user32.GetRawInputData.argtypes = (wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
                                           ctypes.POINTER(wintypes.UINT), wintypes.UINT)
        user32.GetRawInputData.restype = wintypes.UINT
        user32.GetRawInputDeviceInfoW.argtypes = (wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID,
                                                  ctypes.POINTER(wintypes.UINT))
        user32.GetRawInputDeviceInfoW.restype = wintypes.UINT
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        header_size = ctypes.sizeof(RAWINPUTHEADER)

        def device_name(handle):
            if not handle:
                return ""
            if handle not in self._names:
                length = wintypes.UINT(0)
                user32.GetRawInputDeviceInfoW(handle, _RIDI_DEVICENAME, None, ctypes.byref(length))
                buffer = ctypes.create_unicode_buffer(length.value + 1)
                user32.GetRawInputDeviceInfoW(handle, _RIDI_DEVICENAME, buffer, ctypes.byref(length))
                self._names[handle] = buffer.value
            return self._names[handle]

        def on_message(hwnd, message, wparam, lparam):
            if message == _WM_INPUT:
                try:
                    size = wintypes.UINT(0)
                    user32.GetRawInputData(lparam, _RID_INPUT, None, ctypes.byref(size), header_size)
                    if size.value >= ctypes.sizeof(RAWINPUT):
                        buffer = ctypes.create_string_buffer(size.value)
                        user32.GetRawInputData(lparam, _RID_INPUT, buffer, ctypes.byref(size), header_size)
                        raw = ctypes.cast(buffer, ctypes.POINTER(RAWINPUT)).contents
                        if raw.header.dwType == _RIM_TYPEKEYBOARD and not raw.keyboard.Flags & _RI_KEY_BREAK:
                            self.note_key(device_name(raw.header.hDevice))
                except Exception:
                    pass  # one unreadable report - never worth stopping the listener
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._wndproc = wndproc_type(on_message)  # kept: Windows calls it for as long as the window lives
        instance = kernel32.GetModuleHandleW(None)
        window_class = WNDCLASSW(lpfnWndProc=self._wndproc, hInstance=instance,
                                 lpszClassName="SecureTapCardReaders")
        user32.RegisterClassW(ctypes.byref(window_class))  # fails harmlessly if already registered
        hwnd = user32.CreateWindowExW(0, "SecureTapCardReaders", "SecureTap card readers", 0, 0, 0, 0, 0,
                                      _HWND_MESSAGE, None, instance, None)
        # Keyboards (usage page 1, usage 6), even while another window has the
        # keyboard. The usual key messages still reach the gate monitor's text box.
        device = RAWINPUTDEVICE(1, 6, _RIDEV_INPUTSINK, hwnd)
        self.available = bool(hwnd) and bool(user32.RegisterRawInputDevices(ctypes.byref(device), 1,
                                                                            ctypes.sizeof(RAWINPUTDEVICE)))
        ready.set()
        if not self.available:
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
