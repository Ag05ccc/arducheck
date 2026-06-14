"""PreflightController — Qt arayüzü ile ArduCheck motorları arasındaki köprü.

``app/server.py``'deki iş parçacığı/orkestrasyon mantığının (busy kilidi,
worker'lar, yüzey yön testi supersede, durum derlemesi) Qt'ye taşınmış hâlidir.
HTTP/poll yerine bir ``QTimer`` her çevrimde durumu derleyip ``state_changed``
sinyaliyle yayınlar; uzun işlemler (bağlan / kontroller / kalibrasyon / yüzey
override) daemon thread'lerde koşar ve durumu kilit altında günceller — böylece
Qt arayüzü ana thread'de bloklanmaz.

Bu sınıf hiçbir Qt widget'ı tanımaz: yalnızca sinyaller yayar ve metotlar sunar.
"""

import os
import threading
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from app import checks as checks_mod
from app import report as report_mod
from app.calibration import CalManager
from app.checklist_def import MANUAL_ITEMS
from app.mavlink_client import SEVERITY_NAMES, list_serial_ports

from .dronekit_adapter import DronekitMavClient, connect_vehicle

# SERVOn_FUNCTION -> Türkçe ad (kontrol yüzeyleri) — server.py ile birebir
SURFACE_FN = {
    4: "Kanatçık (Aileron)", 19: "Elevatör", 21: "Rudder",
    2: "Flap", 3: "Otomatik Flap",
    24: "Flaperon Sol", 25: "Flaperon Sağ",
    77: "Elevon Sol", 78: "Elevon Sağ",
    79: "V-Kuyruk Sol", 80: "V-Kuyruk Sağ",
}
SURFACE_AXES = {"roll": "RCMAP_ROLL", "pitch": "RCMAP_PITCH", "yaw": "RCMAP_YAW"}
AXIS_DEFAULT_CHAN = {"roll": 1, "pitch": 2, "yaw": 4}

POLL_MS = 200   # durum yayını sıklığı (5 Hz) — web istemcisinin poll'üne denk


class PreflightController(QObject):
    """Tüm uçuş-öncesi işlemlerini dronekit bağlantısı üzerinden sürer."""

    # Her poll çevriminde derlenmiş canlı durum (link, telemetri, busy, kal...)
    state_changed = pyqtSignal(dict)
    # Yeni STATUSTEXT'ler (otopilot mesaj şeridi için)
    messages_appended = pyqtSignal(list)
    # Kontrol koşusu bittiğinde sonuç sözlüğü
    results_ready = pyqtSignal(dict)
    # Bağlantı durumu değiştiğinde (bağlı?, bilgi sözlüğü)
    connection_changed = pyqtSignal(bool, dict)
    # Geçici kullanıcı bildirimi (seviye: "info"/"warn"/"error", metin)
    notice = pyqtSignal(str, str)

    def __init__(self, vehicle=None, parent=None):
        super().__init__(parent)
        self.mav = None
        self.cal = CalManager()
        self._embedded_vehicle = vehicle   # host verdiyse (sahip değiliz)

        self._lock = threading.RLock()
        self.busy = None            # süren uzun işlem adı ya da None
        self.busy_progress = ""
        self.results = None
        self.connect_info = None
        self.manual_checklist = {}  # id -> bool

        # Yüzey yön testi (arka plan RC override) — server.py ile birebir
        self.servo_test = None
        self._servo_lock = threading.Lock()
        self._servo_thread = None
        self._servo_stop = None
        self._servo_token = 0

        # poll/yayın takibi
        self._notices = []          # (level, text) kuyruğu, _tick boşaltır
        self._last_msg_t = 0.0
        self._last_results_time = None
        self._last_connected = None

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._tick)

    # ------------------------------------------------------------------ #
    # Yaşam döngüsü
    # ------------------------------------------------------------------ #

    def start(self):
        """Poll zamanlayıcısını başlat ve (varsa) host Vehicle'ına bağlan."""
        self._timer.start()
        if self._embedded_vehicle is not None:
            self.attach_vehicle(self._embedded_vehicle)

    def shutdown(self):
        """Pencere kapanırken: zamanlayıcıyı durdur, işlemleri temizle."""
        self._timer.stop()
        self.disconnect()

    @property
    def embedded(self):
        return self._embedded_vehicle is not None

    # ------------------------------------------------------------------ #
    # busy kilidi (server.AppState ile birebir)
    # ------------------------------------------------------------------ #

    def set_busy(self, name, progress=""):
        with self._lock:
            if self.busy and name:
                return False
            self.busy = name
            self.busy_progress = progress
            return True

    def set_progress(self, text):
        with self._lock:
            self.busy_progress = text

    def _push_notice(self, level, text):
        with self._lock:
            self._notices.append((level, text))

    # ------------------------------------------------------------------ #
    # Bağlantı
    # ------------------------------------------------------------------ #

    def attach_vehicle(self, vehicle):
        """Host'un hazır dronekit Vehicle'ına bağlan (gömülü mod). Sahip değiliz."""
        if not self.set_busy("connect", "Bağlantıya ekleniyor..."):
            return False
        threading.Thread(target=self._attach_worker,
                         args=(vehicle, False), daemon=True).start()
        return True

    def connect_target(self, target, baud=115200):
        """Bağlantı dizesinden yeni bir dronekit Vehicle aç (bağımsız mod)."""
        target = (target or "").strip()
        if not target:
            self._push_notice("error", "Bağlantı hedefi boş")
            return False
        if not self.set_busy("connect", "Bağlanılıyor: " + target):
            self._push_notice("warn", "Başka bir işlem sürüyor")
            return False
        threading.Thread(target=self._connect_worker,
                         args=(target, baud), daemon=True).start()
        return True

    def _connect_worker(self, target, baud):
        try:
            self.set_progress("Bağlanılıyor: " + target)
            vehicle = connect_vehicle(target, baud=baud, heartbeat_timeout=30)
            self._attach_worker(vehicle, owns=True, _already_busy=True)
        except Exception as exc:
            with self._lock:
                self.connect_info = {"target": target, "error": str(exc),
                                     "ok": False}
            self._push_notice("error", "Bağlantı başarısız: %s" % exc)
            self.set_busy(None)

    def _attach_worker(self, vehicle, owns, _already_busy=False):
        target = "dronekit"
        try:
            client = DronekitMavClient(vehicle, owns_vehicle=owns)
            ok, err = client.attach()
            if not ok:
                if owns:
                    try:
                        vehicle.close()
                    except Exception:
                        pass
                with self._lock:
                    self.connect_info = {"target": target, "error": err,
                                         "ok": False}
                self._push_notice("error", "Bağlanılamadı: %s" % err)
                return
            with self._lock:
                self.mav = client
                self.connect_info = {"target": client.target, "error": None,
                                     "ok": True}
            self.set_progress("Parametreler okunuyor...")
            client.fetch_params(timeout=120)
        except Exception as exc:
            with self._lock:
                self.connect_info = {"target": target, "error": str(exc),
                                     "ok": False}
            self._push_notice("error", "Bağlantı hatası: %s" % exc)
        finally:
            self.set_busy(None)

    def disconnect(self):
        """Bağlantıyı kapat: kalibrasyonu/servo testini durdur, override'ı bırak."""
        self.cal.cancel()
        self.stop_servo_test()
        mav = self.mav
        if mav is not None:
            try:
                if mav.link_stats().get("connected"):
                    self._release_override(mav)
                mav.disconnect()
            except Exception:
                pass
        with self._lock:
            self.mav = None
            self.connect_info = None
            self.results = None
            self._last_results_time = None
            if self.busy == "servo":
                self.busy = None

    # ------------------------------------------------------------------ #
    # Kontroller
    # ------------------------------------------------------------------ #

    def run_checks(self, options=None):
        mav = self.mav
        if mav is None or not mav.link_stats().get("connected"):
            self._push_notice("error", "Araç bağlı değil")
            return False
        if not self.set_busy("checks", "Kontroller çalıştırılıyor..."):
            self._push_notice("warn", "Başka bir işlem sürüyor")
            return False
        threading.Thread(target=self._checks_worker,
                         args=(options or {},), daemon=True).start()
        return True

    def _checks_worker(self, options):
        try:
            results = checks_mod.run_all(self.mav, self.set_progress, options)
            with self._lock:
                self.results = results
        except Exception as exc:
            with self._lock:
                self.results = {"error": str(exc), "time": time.time(),
                                "groups": [], "counts": {}, "problems": []}
            self._push_notice("error", "Kontroller başarısız: %s" % exc)
        finally:
            self.set_busy(None)

    # ------------------------------------------------------------------ #
    # Kalibrasyon
    # ------------------------------------------------------------------ #

    def start_calibration(self, kind):
        mav = self.mav
        if mav is None or not mav.link_stats().get("connected"):
            self._push_notice("error", "Araç bağlı değil")
            return False
        hb = mav.get_msg("HEARTBEAT", max_age=5)
        if hb and (hb.base_mode & 128):
            self._push_notice("error", "Araç ARM edilmiş — kalibrasyon yalnızca "
                                       "disarm durumda yapılabilir.")
            return False
        if not self.set_busy("calibrate", "Kalibrasyon..."):
            self._push_notice("warn", "Başka bir işlem sürüyor")
            return False
        ok, err = self.cal.start(mav, kind, on_finish=lambda: self.set_busy(None))
        if not ok:
            self.set_busy(None)
            self._push_notice("error", err or "Kalibrasyon başlatılamadı")
            return False
        return True

    def cal_action(self, action):
        if action in ("confirm", "accept"):
            self.cal.proceed()
        elif action == "cancel":
            self.cal.cancel()
        elif action == "close":
            self.cal.reset()
        else:
            return False
        return True

    # ------------------------------------------------------------------ #
    # Disarm kapısı (servo/param yazma) — server._disarmed_error ile birebir
    # ------------------------------------------------------------------ #

    def _disarmed_error(self):
        mav = self.mav
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

    # ------------------------------------------------------------------ #
    # Servo araçları
    # ------------------------------------------------------------------ #

    def list_servos(self):
        """(servos_listesi, hata) döner."""
        mav = self.mav
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

    def servo_trim(self, n, delta):
        """SERVOn_TRIM'i ±delta kaydır. (ok, ad/değer ya da hata) döner."""
        err = self._disarmed_error()
        if err:
            return False, err
        with self._lock:
            if self.busy:
                return False, "Başka bir işlem sürüyor"
        try:
            n = int(n)
            delta = int(delta)
        except (TypeError, ValueError):
            return False, "servo/delta geçersiz"
        if not (1 <= n <= 16 and -50 <= delta <= 50 and delta != 0):
            return False, "servo 1-16, delta ±1..50 olmalı"
        mav = self.mav
        name = "SERVO%d_TRIM" % n
        cur = mav.get_param(name)
        if cur is None:
            return False, "%s okunamadı" % name
        lo = mav.get_param("SERVO%d_MIN" % n) or 800
        hi = mav.get_param("SERVO%d_MAX" % n) or 2200
        new = max(max(800, lo), min(min(2200, hi), int(cur) + delta))
        ok, res = mav.set_param(name, new)
        if not ok:
            return False, str(res)
        return True, "%s = %s" % (name, int(res) if res is not None else "?")

    # --- yüzey yön testi (arka plan override + supersede) -------------- #

    def _release_override(self, mav):
        for _ in range(3):
            mav.rc_override([0] * 8)
            time.sleep(0.05)

    def start_servo_test(self, vals, mode0, dur, axis, direction):
        with self._servo_lock:
            if self.busy and self.busy != "servo":
                return False, "Başka bir işlem sürüyor"
            old_stop = self._servo_stop
            if old_stop is not None:
                old_stop.set()
            self._servo_token += 1
            token = self._servo_token
            stop = threading.Event()
            self._servo_stop = stop
            with self._lock:
                self.busy = "servo"
                self.busy_progress = "Yüzey testi: %s" % axis
            self.servo_test = {"axis": axis, "dir": direction}
            t = threading.Thread(target=self._surface_worker,
                                 args=(self.mav, vals, mode0, dur, stop, token),
                                 daemon=True)
            self._servo_thread = t
            t.start()
        return True, None

    def stop_servo_test(self):
        with self._servo_lock:
            self._servo_token += 1
            if self._servo_stop is not None:
                self._servo_stop.set()
            self._servo_thread = None
            self.servo_test = None

    def _surface_worker(self, mav, vals, mode0, dur, stop, token):
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
            with self._servo_lock:
                superseded = token != self._servo_token
                if not superseded:
                    self.servo_test = None
                    self._servo_thread = None
                    self.set_busy(None)
            if sent and not superseded:
                self._release_override(mav)

    def surface_test(self, axis, direction, ms=1500):
        """MANUAL modda RC override darbesiyle yüzey yön testi başlat.
        (ok, bilgi/hata) döner."""
        mav = self.mav
        if axis not in SURFACE_AXES or direction not in ("plus", "minus"):
            return False, "axis/dir geçersiz"
        superseding = self.busy == "servo" and self.servo_test is not None
        if superseding:
            if mav is None or not mav.link_stats().get("connected"):
                return False, "Araç bağlı değil"
            hb = mav.get_msg("HEARTBEAT", max_age=3)
            if hb is not None and (hb.base_mode & 128):
                return False, "Araç ARM edilmiş — yalnız disarm durumda yapılabilir."
        else:
            err = self._disarmed_error()
            if err:
                return False, err
        t = checks_mod.telemetry_summary(mav)
        if t.get("mode") != "MANUAL":
            return False, "Yüzey testi için aracı MANUAL moda alın (kumandadan/GCS)."
        ot = mav.get_param("RC_OVERRIDE_TIME")
        if ot is None:
            return False, "RC_OVERRIDE_TIME okunamadı — parametre indirmeyi bekleyin."
        if ot <= 0:
            return False, ("RC_OVERRIDE_TIME=%g: RC override kapalı — test "
                           "reddedildi (3 önerilir)." % ot)
        chan = int(mav.get_param(SURFACE_AXES[axis]) or AXIS_DEFAULT_CHAN[axis])
        if not (1 <= chan <= 8):
            return False, "RCMAP kanalı 1-8 dışı"
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
        ok, err = self.start_servo_test(vals, mode0, dur, axis, direction)
        if not ok:
            return False, err
        return True, "kanal %d -> %d µs (%.1f sn)" % (chan, pwm, dur)

    # ------------------------------------------------------------------ #
    # Referans parametreler
    # ------------------------------------------------------------------ #

    def refparams_info(self):
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

    def param_diff(self):
        """(payload, hata) döner."""
        mav = self.mav
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

    def save_ref_current(self):
        """Aracın mevcut parametrelerini referans olarak kaydet. (ok, mesaj)."""
        mav = self.mav
        if mav is None:
            return False, "Araç bağlı değil"
        params = mav.params_snapshot()
        if not params:
            return False, "Araç parametreleri henüz yok — indirmenin bitmesini bekleyin"
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
            return False, str(exc)
        return True, ("%d parametre kaydedildi (%d uçucu atlandı)"
                      % (len(params) - skipped, skipped))

    def upload_ref(self, text):
        """Bir .param metnini referans olarak yükle. (ok, mesaj)."""
        text = text or ""
        tmp = checks_mod.REF_PARAMS_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            ref = checks_mod.load_ref_params(tmp)
            if not ref:
                return False, "Dosyada geçerli parametre satırı bulunamadı"
            os.replace(tmp, checks_mod.REF_PARAMS_PATH)
        except OSError as exc:
            return False, str(exc)
        finally:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
        return True, "%d parametre yüklendi" % len(ref)

    # ------------------------------------------------------------------ #
    # Manuel liste + rapor
    # ------------------------------------------------------------------ #

    def set_manual_item(self, item_id, checked):
        with self._lock:
            self.manual_checklist[item_id] = bool(checked)

    def manual_state(self):
        with self._lock:
            return dict(self.manual_checklist)

    def build_report(self):
        """(html, hata) döner."""
        with self._lock:
            results = self.results
            checklist = dict(self.manual_checklist)
        if not results:
            return None, "Önce kontrolleri çalıştırın"
        tele = checks_mod.telemetry_summary(self.mav) if self.mav else {}
        return report_mod.render(results, checklist, tele), None

    @staticmethod
    def list_serial_ports():
        return list_serial_ports()

    @staticmethod
    def manual_items():
        return MANUAL_ITEMS

    # ------------------------------------------------------------------ #
    # Poll çevrimi — durumu derleyip yayınla
    # ------------------------------------------------------------------ #

    def _tick(self):
        mav = self.mav
        with self._lock:
            busy = self.busy
            progress = self.busy_progress
            connect_info = self.connect_info
            servo_test = self.servo_test
            results = self.results
            notices = self._notices
            self._notices = []

        link = mav.link_stats() if mav is not None else {"connected": False}
        connected = bool(link.get("connected"))
        if mav is not None:
            telemetry = checks_mod.telemetry_summary(mav)
            vehicle = checks_mod.vehicle_info(mav)
            param_progress = mav.param_progress()
        else:
            telemetry, vehicle, param_progress = {}, {}, None

        payload = {
            "embedded": self.embedded,
            "link": link,
            "connected": connected,
            "busy": busy,
            "progress": progress,
            "servo_test": servo_test,
            "connect_info": connect_info,
            "param_progress": param_progress,
            "telemetry": telemetry,
            "vehicle": vehicle,
            "calibration": self.cal.snapshot(),
            "have_results": results is not None,
        }
        self.state_changed.emit(payload)

        # geçici bildirimler
        for level, text in notices:
            self.notice.emit(level, text)

        # bağlantı geçişi
        if connected != self._last_connected:
            self._last_connected = connected
            self.connection_changed.emit(connected, connect_info or {})

        # yeni mesajlar
        if mav is not None:
            new_msgs = [
                {"t": t, "sev": sev, "sev_name": SEVERITY_NAMES.get(sev, "?"),
                 "text": text, "count": count}
                for (t, sev, text, count) in mav.recent_statustexts()
                if t > self._last_msg_t
            ]
            if new_msgs:
                self._last_msg_t = max(m["t"] for m in new_msgs)
                self.messages_appended.emit(new_msgs)

        # yeni sonuçlar
        if results is not None:
            rt = results.get("time")
            if rt != self._last_results_time:
                self._last_results_time = rt
                self.results_ready.emit(results)
