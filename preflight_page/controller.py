"""PreflightController — Qt arayüzü ile ArduCheck motorları arasındaki köprü.

``app/server.py``'deki iş parçacığı/orkestrasyon mantığının (busy kilidi,
worker'lar, yüzey yön testi supersede, durum derlemesi) Qt'ye taşınmış hâlidir.
HTTP/poll yerine bir ``QTimer`` her çevrimde durumu derleyip ``state_changed``
sinyaliyle yayınlar; uzun işlemler (bağlan / kontroller / kalibrasyon / yüzey
override) daemon thread'lerde koşar ve durumu kilit altında günceller — böylece
Qt arayüzü ana thread'de bloklanmaz.

Bu sınıf hiçbir Qt widget'ı tanımaz: yalnızca sinyaller yayar ve metotlar sunar.
"""

import threading
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from app import checks as checks_mod
from app.calibration import CalManager
from app.mavlink_client import list_serial_ports
from app.core import preflight as core

from .dronekit_adapter import DronekitMavClient, connect_vehicle

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
        self.operation = core.OperationState()
        self.surface = core.SurfaceTestManager(self.operation)
        self._embedded_vehicle = vehicle   # host verdiyse (sahip değiliz)

        self._lock = threading.RLock()
        self.results = None
        self.connect_info = None
        self.manual_checklist = {}  # id -> bool

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
        return self.operation.set_busy(name, progress)

    def set_progress(self, text):
        self.operation.set_progress(text)

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
                    core.release_override(mav)
                mav.disconnect()
            except Exception:
                pass
        with self._lock:
            self.mav = None
            self.connect_info = None
            self.results = None
            self._last_results_time = None
        self.operation.clear_if("servo")

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
    # Servo araçları
    # ------------------------------------------------------------------ #

    def list_servos(self):
        """(servos_listesi, hata) döner."""
        return core.list_servos(self.mav)

    def servo_trim(self, n, delta):
        """SERVOn_TRIM'i ±delta kaydır. (ok, ad/değer ya da hata) döner."""
        ok, err, payload = core.servo_trim(self.mav, self.operation, n, delta)
        if err:
            return False, err
        return ok, "%s = %s" % (
            payload["name"], int(payload["value"])
            if payload.get("value") is not None else "?")

    def stop_servo_test(self):
        self.surface.stop()

    def surface_test(self, axis, direction, ms=1500):
        """MANUAL modda RC override darbesiyle yüzey yön testi başlat.
        (ok, bilgi/hata) döner."""
        ok, err, payload = core.surface_test(
            self.mav, self.operation, self.surface, axis, direction, ms)
        if err:
            return False, err
        return True, "kanal %d -> %d µs (%.1f sn)" % (
            payload["chan"], payload["pwm"], payload["dur"])

    # ------------------------------------------------------------------ #
    # Referans parametreler
    # ------------------------------------------------------------------ #

    def refparams_info(self):
        return core.refparams_info()

    def param_diff(self):
        """(payload, hata) döner."""
        return core.param_diff(self.mav)

    def save_ref_current(self):
        """Aracın mevcut parametrelerini referans olarak kaydet. (ok, mesaj)."""
        payload, err = core.save_ref_current(self.mav)
        if err:
            return False, err
        return True, ("%d parametre kaydedildi (%d uçucu atlandı)"
                      % (payload["count"], payload["skipped_volatile"]))

    def upload_ref(self, text):
        """Bir .param metnini referans olarak yükle. (ok, mesaj)."""
        payload, err = core.upload_ref(text)
        if err:
            return False, err
        return True, "%d parametre yüklendi" % payload["count"]

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
        return core.render_report(results, checklist, self.mav)

    @staticmethod
    def list_serial_ports():
        return list_serial_ports()

    @staticmethod
    def manual_items():
        return core.manual_items()

    # ------------------------------------------------------------------ #
    # Poll çevrimi — durumu derleyip yayınla
    # ------------------------------------------------------------------ #

    def _tick(self):
        mav = self.mav
        with self._lock:
            connect_info = self.connect_info
            results = self.results
            notices = self._notices
            self._notices = []

        payload = core.build_state_payload(
            mav, self.operation, self.cal, connect_info, results,
            self.surface.snapshot(), msgs_since=self._last_msg_t,
            embedded=self.embedded)
        connected = bool(payload.get("connected"))
        self.state_changed.emit(payload)

        # geçici bildirimler
        for level, text in notices:
            self.notice.emit(level, text)

        # bağlantı geçişi
        if connected != self._last_connected:
            self._last_connected = connected
            self.connection_changed.emit(connected, connect_info or {})

        # yeni mesajlar
        new_msgs = payload.get("messages") or []
        if new_msgs:
            self._last_msg_t = max(m["t"] for m in new_msgs)
            self.messages_appended.emit(new_msgs)

        # yeni sonuçlar
        if results is not None:
            rt = results.get("time")
            if rt != self._last_results_time:
                self._last_results_time = rt
                self.results_ready.emit(results)
