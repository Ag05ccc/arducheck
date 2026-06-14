"""dronekit ``Vehicle`` -> ArduCheck ``MavClient`` köprüsü.

ArduCheck'in tüm kontrol/kalibrasyon motoru (``app.checks``, ``app.calibration``)
yalnızca ``MavClient``'in arayüzüne (get_msg / get_history / get_param /
run_command / rc_override / recent_statustexts ...) bağlıdır; mesajların NEREDEN
geldiğini bilmez. ``MavClient`` mesajları kendi okuma thread'inde pymavlink'ten
toplar. Burada o okuma thread'ini HİÇ başlatmadan, mesajları bir dronekit
``Vehicle``'ın ``add_message_listener('*')`` akışından besliyoruz; komutları da
aynı ``Vehicle``'ın master MAVLink bağlantısı üzerinden gönderiyoruz.

Böylece ``MavClient``'in incelikli bölümleri (çok parçalı STATUSTEXT birleştirme,
parametre boşluk doldurma, COMMAND_ACK eşleştirme, ivmeölçer pozisyon istekleri)
olduğu gibi yeniden kullanılır — kopyalanmaz.
"""

# --- Python 3.10+ uyumluluğu: dronekit 2.9.2 kaldırılmış collections takma
# adlarını kullanır. dronekit'i import etmeden ÖNCE geri ekle. -------------- #
import collections
import collections.abc as _abc
for _n in ("MutableMapping", "Mapping", "MutableSequence", "Sequence",
           "Iterable", "Callable", "Hashable", "MutableSet", "Set"):
    if not hasattr(collections, _n):
        setattr(collections, _n, getattr(_abc, _n))
del _abc, _n

import os
import sys
import time

# ``app`` paketini (kontrol motorları) içeri alabilmek için depo kökünü yola ekle
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.mavlink_client import MavClient  # noqa: E402  (yol ekledikten sonra)


def _import_dronekit():
    """dronekit'i tembel ve anlaşılır hata mesajıyla içeri al."""
    try:
        import dronekit
        return dronekit
    except ImportError as exc:
        raise ImportError(
            "dronekit içe aktarılamadı (%s). Kurmak için:\n"
            "    pip install dronekit future\n"
            "(Python 3.10+ için 'future' paketi de gereklidir.)" % exc
        ) from exc


class DronekitMavClient(MavClient):
    """dronekit ``Vehicle`` ile beslenen MavClient.

    Parametreler:
        vehicle: bağlı bir dronekit ``Vehicle`` (host uygulamadan ya da
            :func:`connect_vehicle` ile).
        owns_vehicle: ``True`` ise (bağımsız mod) ``disconnect()`` çağrıldığında
            ``Vehicle`` da kapatılır. Host bağlantıyı verdiyse ``False`` bırakın.
    """

    def __init__(self, vehicle, owns_vehicle=False):
        super().__init__()
        self._vehicle = vehicle
        self._owns = owns_vehicle
        self._listener = None
        self._conn = self._master_of(vehicle)
        self.target = "dronekit"

    # ------------------------------------------------------------------ #
    @staticmethod
    def _master_of(vehicle):
        """dronekit Vehicle'ın alttaki mavutil bağlantısını bul (komut gönderimi
        ve hedef sysid/compid için)."""
        for attr in ("_master",):
            m = getattr(vehicle, attr, None)
            if m is not None:
                return m
        handler = getattr(vehicle, "_handler", None)
        return getattr(handler, "master", None)

    # ------------------------------------------------------------------ #
    # Yaşam döngüsü
    # ------------------------------------------------------------------ #

    def attach(self):
        """Vehicle mesaj akışına bağlan. (ok, hata) döner — MavClient.connect ile
        aynı sözleşme."""
        v = self._vehicle
        if v is None:
            return False, "Vehicle yok"
        self._conn = self._master_of(v)
        if self._conn is None:
            return False, "dronekit Vehicle'da master MAVLink bağlantısı bulunamadı"

        self._stop.clear()
        # Kimliği master'dan tohumla; gerçek heartbeat'ler tazeleyip doğrular.
        try:
            self.sysid = self._conn.target_system or 1
            self.compid = self._conn.target_component or 1
        except Exception:
            self.sysid, self.compid = 1, 1
        self.connected = True
        self.last_heartbeat = time.time()

        def _on_message(_vehicle, _name, message):
            # dronekit'in alıcı thread'inde çalışır; _handle thread-güvenlidir.
            if self._stop.is_set():
                return
            try:
                self._handle(message)
            except Exception:
                pass

        self._listener = _on_message
        v.add_message_listener("*", _on_message)

        # İhtiyacımız olan ek akışları/aralıkları iste (EKF/VIBRATION/BATTERY/
        # POWER + AUTOPILOT_VERSION). MavClient'in kendi rutini bunu yapar.
        try:
            self._on_first_heartbeat()
        except Exception:
            pass
        return True, None

    # MavClient.connect imzası: kendi linkimizi AÇMAYIZ, sadece bağlanırız.
    def connect(self, target=None, baud=115200):
        return self.attach()

    def disconnect(self):
        """Dinleyiciyi kaldır, durumu sıfırla. Paylaşılan master'ı KAPATMA
        (yalnız sahibiysek Vehicle'ı kapatırız)."""
        v = self._vehicle
        lis = self._listener
        self._listener = None
        if lis is not None and v is not None:
            try:
                v.remove_message_listener("*", lis)
            except Exception:
                pass
        # Taban sınıfın disconnect'i self._conn.close() çağırır; paylaşılan
        # master'ı kapatmamak için önce referansı düşür.
        self._conn = None
        try:
            super().disconnect()
        except Exception:
            pass
        if self._owns and v is not None:
            try:
                v.close()
            except Exception:
                pass
        self._vehicle = None

    @property
    def vehicle(self):
        return self._vehicle


# ===================================================================== #
# Bağımsız mod yardımcıları
# ===================================================================== #

def connect_vehicle(connection_string, baud=115200, heartbeat_timeout=30,
                    status_printer=None):
    """Bir dronekit ``Vehicle`` aç (bağımsız test için).

    connection_string örnekleri:
        'tcp:127.0.0.1:5760'   (SITL)
        'udp:0.0.0.0:14550'    (GCS yayını)
        '/dev/ttyACM0'         (USB, baud=115200)
        'COM3'                 (Windows)
    """
    dronekit = _import_dronekit()
    kwargs = dict(wait_ready=True, baud=baud,
                  heartbeat_timeout=heartbeat_timeout)
    if status_printer is not None:
        # dronekit sürümüne göre status_printer parametresi olmayabilir.
        kwargs["status_printer"] = status_printer
    try:
        return dronekit.connect(connection_string, **kwargs)
    except TypeError:
        kwargs.pop("status_printer", None)
        return dronekit.connect(connection_string, **kwargs)


def make_client(connection_string, baud=115200, heartbeat_timeout=30):
    """Bağlantı dizesinden bağlı bir :class:`DronekitMavClient` üret (bağımsız).

    Vehicle'ın sahibi istemcidir: ``client.disconnect()`` Vehicle'ı da kapatır.
    """
    vehicle = connect_vehicle(connection_string, baud=baud,
                              heartbeat_timeout=heartbeat_timeout)
    client = DronekitMavClient(vehicle, owns_vehicle=True)
    ok, err = client.attach()
    if not ok:
        try:
            vehicle.close()
        except Exception:
            pass
        raise RuntimeError(err or "dronekit Vehicle'a bağlanılamadı")
    return client
