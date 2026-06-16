"""UI-independent preflight operations shared by web and PyQt frontends."""

import os
import threading
import time

from app import checks as checks_mod
from app import report as report_mod
from app.checklist_def import MANUAL_ITEMS
from app.mavlink_client import SEVERITY_NAMES


SURFACE_FN = {
    4: "Kanatçık (Aileron)", 19: "Elevatör", 21: "Rudder",
    2: "Flap", 3: "Otomatik Flap",
    24: "Flaperon Sol", 25: "Flaperon Sağ",
    77: "Elevon Sol", 78: "Elevon Sağ",
    79: "V-Kuyruk Sol", 80: "V-Kuyruk Sağ",
}

SURFACE_AXES = {"roll": "RCMAP_ROLL", "pitch": "RCMAP_PITCH", "yaw": "RCMAP_YAW"}
AXIS_DEFAULT_CHAN = {"roll": 1, "pitch": 2, "yaw": 4}


class OperationState:
    """Small shared busy/progress lock for long-running preflight operations."""

    def __init__(self):
        self._lock = threading.RLock()
        self.busy = None
        self.progress = ""

    def set_busy(self, name, progress=""):
        with self._lock:
            if self.busy and name:
                return False
            self.busy = name
            self.progress = progress
            return True

    def set_progress(self, text):
        with self._lock:
            self.progress = text

    def set_progress_if_busy(self, name, text):
        with self._lock:
            if self.busy != name:
                return False
            self.progress = text
            return True

    def clear_if(self, name):
        with self._lock:
            if self.busy == name:
                self.busy = None
                self.progress = ""

    def snapshot(self):
        with self._lock:
            return {"busy": self.busy, "progress": self.progress}


def release_override(mav):
    """Release all RC override channels; repeat because one packet may drop."""
    for _ in range(3):
        mav.rc_override([0] * 8)
        time.sleep(0.05)


class SurfaceTestManager:
    """Runs a short RC override pulse for control-surface direction checks."""

    def __init__(self, operation):
        self.operation = operation
        self.servo_test = None
        self._lock = threading.Lock()
        self._thread = None
        self._stop = None
        self._token = 0

    def snapshot(self):
        with self._lock:
            return dict(self.servo_test) if self.servo_test else None

    def start(self, mav, vals, mode0, dur, axis, direction):
        with self._lock:
            if self.operation.busy and self.operation.busy != "servo":
                return False, "Başka bir işlem sürüyor"
            old_stop = self._stop
            if old_stop is not None:
                old_stop.set()
            self._token += 1
            token = self._token
            stop = threading.Event()
            self._stop = stop
            progress = "Yüzey testi: %s" % axis
            if not self.operation.set_busy("servo", progress):
                self.operation.set_progress_if_busy("servo", progress)
            self.servo_test = {"axis": axis, "dir": direction}
            thread = threading.Thread(
                target=self._worker,
                args=(mav, vals, mode0, dur, stop, token),
                daemon=True)
            self._thread = thread
            thread.start()
        return True, None

    def stop(self):
        with self._lock:
            self._token += 1
            if self._stop is not None:
                self._stop.set()
            self._thread = None
            self.servo_test = None
        self.operation.clear_if("servo")

    def _worker(self, mav, vals, mode0, dur, stop, token):
        sent = False
        try:
            end = time.time() + dur
            while time.time() < end and not stop.is_set():
                hb = mav.get_msg("HEARTBEAT", max_age=2)
                if hb is None or (hb.base_mode & 128) or \
                        (mode0 is not None and hb.custom_mode != mode0):
                    break
                if not mav.rc_override(vals):
                    break
                sent = True
                stop.wait(0.2)
        finally:
            with self._lock:
                superseded = token != self._token
                if not superseded:
                    self.servo_test = None
                    self._thread = None
                    self.operation.clear_if("servo")
            if sent and not superseded:
                release_override(mav)


def disarmed_error(mav):
    """Fresh-heartbeat DISARM gate for user-triggered write operations."""
    if mav is None or not mav.link_stats().get("connected"):
        return "Araç bağlı değil"
    t0 = time.time()
    hb = None
    while time.time() - t0 < 1.6:
        hb = mav.get_msg_after("HEARTBEAT", t0)
        if hb is not None:
            break
        time.sleep(0.1)
    if hb is None:
        return "Taze heartbeat alınamadı — bağlantı bayat, işlem reddedildi."
    if hb.base_mode & 128:
        return "Araç ARM edilmiş — bu işlem yalnız disarm durumda yapılabilir."
    return None


def list_servos(mav):
    if mav is None or not mav.link_stats().get("connected"):
        return [], "Araç bağlı değil"
    out = []
    for n in range(1, 17):
        fn = mav.get_param("SERVO%d_FUNCTION" % n)
        if fn is None or int(fn) not in SURFACE_FN:
            continue
        out.append({
            "n": n, "function": SURFACE_FN[int(fn)],
            "trim": mav.get_param("SERVO%d_TRIM" % n),
            "min": mav.get_param("SERVO%d_MIN" % n),
            "max": mav.get_param("SERVO%d_MAX" % n),
            "reversed": bool(mav.get_param("SERVO%d_REVERSED" % n) or 0),
        })
    return out, None


def servo_trim(mav, operation, n, delta):
    err = disarmed_error(mav)
    if err:
        return False, err, None
    if operation.busy:
        return False, "Başka bir işlem sürüyor", None
    try:
        n = int(n)
        delta = int(delta)
    except (TypeError, ValueError):
        return False, "servo/delta geçersiz", None
    if not (1 <= n <= 16 and -50 <= delta <= 50 and delta != 0):
        return False, "servo 1-16, delta ±1..50 olmalı", None
    name = "SERVO%d_TRIM" % n
    cur = mav.get_param(name)
    if cur is None:
        return False, "%s okunamadı" % name, None
    lo = mav.get_param("SERVO%d_MIN" % n) or 800
    hi = mav.get_param("SERVO%d_MAX" % n) or 2200
    new = max(max(800, lo), min(min(2200, hi), int(cur) + delta))
    ok, res = mav.set_param(name, new)
    if not ok:
        return False, str(res), None
    return True, None, {"name": name, "value": res}


def surface_test(mav, operation, surface, axis, direction, ms=1500):
    if axis not in SURFACE_AXES or direction not in ("plus", "minus"):
        return False, "axis/dir geçersiz", None
    superseding = operation.busy == "servo" and surface.snapshot() is not None
    if superseding:
        if mav is None or not mav.link_stats().get("connected"):
            return False, "Araç bağlı değil", None
        hb = mav.get_msg("HEARTBEAT", max_age=3)
        if hb is not None and (hb.base_mode & 128):
            return False, "Araç ARM edilmiş — bu işlem yalnız disarm durumda yapılabilir.", None
    else:
        err = disarmed_error(mav)
        if err:
            return False, err, None

    t = checks_mod.telemetry_summary(mav)
    if t.get("mode") != "MANUAL":
        return False, "Yüzey testi için aracı MANUAL moda alın (kumandadan/GCS).", None
    ot = mav.get_param("RC_OVERRIDE_TIME")
    if ot is None:
        return False, "RC_OVERRIDE_TIME okunamadı — parametre indirmeyi bekleyin.", None
    if ot <= 0:
        return False, ("RC_OVERRIDE_TIME=%g: RC override kapalı — test "
                       "reddedildi (3 önerilir)." % ot), None
    chan = int(mav.get_param(SURFACE_AXES[axis]) or AXIS_DEFAULT_CHAN[axis])
    if not (1 <= chan <= 8):
        return False, "RCMAP kanalı 1-8 dışı", None
    rev = bool(mav.get_param("RC%d_REVERSED" % chan) or 0)
    high = (direction == "plus")
    if rev:
        high = not high
    pwm = 1900 if high else 1100
    vals = [0] * 8
    vals[chan - 1] = pwm
    hb0 = mav.get_msg("HEARTBEAT")
    mode0 = hb0.custom_mode if hb0 else None
    try:
        dur = min(3.0, max(0.5, float(ms) / 1000.0))
    except (TypeError, ValueError):
        dur = 1.5
    ok, err = surface.start(mav, vals, mode0, dur, axis, direction)
    if not ok:
        return False, err, None
    return True, None, {"chan": chan, "pwm": pwm, "dur": dur}


def refparams_info():
    info = {"exists": os.path.exists(checks_mod.REF_PARAMS_PATH),
            "path": os.path.basename(checks_mod.REF_PARAMS_PATH)}
    if info["exists"]:
        try:
            ref = checks_mod.load_ref_params()
            info["count"] = len(ref)
            info["mtime"] = os.path.getmtime(checks_mod.REF_PARAMS_PATH)
        except OSError as exc:
            info["error"] = str(exc)
    return info


def param_diff(mav):
    if not os.path.exists(checks_mod.REF_PARAMS_PATH):
        return None, "Referans dosyası yok"
    if mav is None:
        return None, "Araç bağlı değil"
    params = mav.params_snapshot()
    if not params:
        return None, "Araç parametreleri henüz yok"
    try:
        ref = checks_mod.load_ref_params()
    except OSError as exc:
        return None, "Referans okunamadı: %s" % exc
    diffs, missing, ignored = checks_mod.param_diff(params, ref)
    return {"diffs": diffs, "missing": missing, "ignored_volatile": ignored,
            "param_fetch_done": mav.param_fetch_done, "ref_count": len(ref),
            "vehicle_count": len(params)}, None


def save_ref_current(mav):
    if mav is None:
        return None, "Araç bağlı değil"
    params = mav.params_snapshot()
    if not params:
        return None, "Araç parametreleri henüz yok — indirmenin bitmesini bekleyin"
    tmp = checks_mod.REF_PARAMS_PATH + ".tmp"
    skipped = 0
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("# ArduCheck referans parametreleri — %s\n"
                    % time.strftime("%Y-%m-%d %H:%M:%S"))
            for name in sorted(params):
                if checks_mod.is_volatile_param(name):
                    skipped += 1
                    continue
                f.write("%s,%.10g\n" % (name, params[name]))
        os.replace(tmp, checks_mod.REF_PARAMS_PATH)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return None, str(exc)
    return {"ok": True, "count": len(params) - skipped,
            "skipped_volatile": skipped}, None


def upload_ref(text):
    tmp = checks_mod.REF_PARAMS_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text or "")
        ref = checks_mod.load_ref_params(tmp)
        if not ref:
            return None, "Dosyada geçerli parametre satırı bulunamadı"
        os.replace(tmp, checks_mod.REF_PARAMS_PATH)
    except OSError as exc:
        return None, str(exc)
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
    return {"ok": True, "count": len(ref)}, None


def render_report(results, manual_state, mav):
    if not results:
        return None, "Önce kontrolleri çalıştırın"
    telemetry = checks_mod.telemetry_summary(mav) if mav is not None else {}
    return report_mod.render(results, manual_state, telemetry), None


def build_state_payload(mav, operation, calibration, connect_info, results,
                        servo_test, msgs_since=None, boot=None, embedded=None):
    op = operation.snapshot()
    payload = {
        "link": mav.link_stats() if mav is not None else {"connected": False},
        "busy": op["busy"],
        "servo_test": servo_test,
        "progress": op["progress"],
        "connect_info": connect_info,
        "param_progress": mav.param_progress() if mav is not None else None,
        "have_results": results is not None,
        "results_time": results.get("time") if results else None,
        "telemetry": checks_mod.telemetry_summary(mav) if mav is not None else {},
        "vehicle": checks_mod.vehicle_info(mav) if mav is not None else {},
        "calibration": calibration.snapshot(),
    }
    if boot is not None:
        payload["boot"] = boot
    if embedded is not None:
        payload["embedded"] = embedded
        payload["connected"] = bool(payload["link"].get("connected"))
    if mav is not None:
        payload["messages"] = [
            {"t": t, "sev": sev, "sev_name": SEVERITY_NAMES.get(sev, "?"),
             "text": text, "count": count}
            for (t, sev, text, count) in mav.recent_statustexts(since=msgs_since)
            if msgs_since is None or t > msgs_since
        ]
    else:
        payload["messages"] = []
    return payload


def manual_items():
    return MANUAL_ITEMS
