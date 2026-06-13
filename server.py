"""ArduCheck HTTP server: serves the web UI and a small JSON API.

Stdlib-only (http.server); pymavlink is the app's single external dependency.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import checks as checks_mod
import report as report_mod
from calibration import CalManager
from checklist_def import MANUAL_ITEMS
from mavlink_client import MavClient, list_serial_ports, SEVERITY_NAMES

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
BOOT_TIME = time.time()   # istemcinin sunucu yeniden başlatmasını sezmesi için

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}


class AppState:
    """Shared state between HTTP handlers and worker threads."""

    def __init__(self):
        self.mav = MavClient()
        self.cal = CalManager()
        self.lock = threading.Lock()
        self.busy = None           # current long operation name or None
        self.busy_progress = ""    # human-readable progress
        self.results = None        # last check run results (dict)
        self.connect_info = None   # {"target":..., "error":...}
        self.manual_checklist = {} # id -> bool (mirrored from UI for reports)
        # Yüzey yön testi (arka plan RC override) durumu. busy="servo" çapraz
        # işlem kilidini sağlar; servo_test hangi yönün etkin olduğunu UI'a
        # bildirir; _servo_token kuşak sayacı supersede yarışını çözer.
        self.servo_test = None     # {"axis","dir"} veya None
        self._servo_lock = threading.Lock()
        self._servo_thread = None
        self._servo_stop = None    # etkin worker'a "dur" sinyali (Event)
        self._servo_token = 0

    def set_busy(self, name, progress=""):
        with self.lock:
            if self.busy and name:
                return False
            self.busy = name
            self.busy_progress = progress
            return True

    def set_progress(self, text):
        with self.lock:
            self.busy_progress = text

    def start_servo_test(self, vals, mode0, dur, axis, direction):
        """Yüzey yön testini arka plan worker'ında başlat (ya da çalışanı
        devral/supersede). (ok, hata) döner. busy='servo' SÜREKLİ tutulur —
        kilit asla aradan bırakılmaz — böylece başka işlem araya giremez ve
        yeni yön, eskisini sıfır-override çatışması olmadan devralır."""
        with self._servo_lock:
            if self.busy and self.busy != "servo":
                return False, "Başka bir işlem sürüyor"
            old_stop = self._servo_stop
            if old_stop is not None:
                old_stop.set()             # önceki worker'a dur de
            self._servo_token += 1         # yeni kuşak: eski worker temizlik yapmaz
            token = self._servo_token
            stop = threading.Event()
            self._servo_stop = stop
            with self.lock:
                self.busy = "servo"
                self.busy_progress = "Yüzey testi: %s" % axis
            self.servo_test = {"axis": axis, "dir": direction}
            t = threading.Thread(
                target=_surface_worker,
                args=(self.mav, vals, mode0, dur, stop, token),
                daemon=True)
            self._servo_thread = t
            t.start()
        return True, None

    def stop_servo_test(self):
        """Çalışan yüzey testini durdur ve durumunu temizle (disconnect'te).
        Token artırılır ki worker'ın finally'si busy/servo_test'i ezmesin."""
        with self._servo_lock:
            self._servo_token += 1
            if self._servo_stop is not None:
                self._servo_stop.set()
            self._servo_thread = None
            self.servo_test = None


STATE = AppState()


def _connect_worker(target, baud):
    mav = STATE.mav
    try:
        STATE.set_progress("Bağlanılıyor: " + target)
        ok, err = mav.connect(target, baud)
        with STATE.lock:
            STATE.connect_info = {"target": target, "error": err, "ok": ok}
        if ok:
            STATE.set_progress("Parametreler okunuyor...")
            mav.fetch_params(timeout=120)
    except Exception as exc:
        with STATE.lock:
            STATE.connect_info = {"target": target, "error": str(exc),
                                  "ok": False}
    finally:
        STATE.set_busy(None)


def _checks_worker(options):
    try:
        STATE.set_progress("Kontroller çalıştırılıyor...")
        results = checks_mod.run_all(STATE.mav, STATE.set_progress,
                                     options or {})
        with STATE.lock:
            STATE.results = results
    except Exception as exc:
        with STATE.lock:
            STATE.results = {"error": str(exc), "time": time.time(),
                             "groups": []}
    finally:
        STATE.set_busy(None)


def _release_override(mav):
    """Tüm RC kanallarını serbest bırak — tek paket kaybolabilir, 3 kez yolla."""
    for _ in range(3):
        mav.rc_override([0] * 8)
        time.sleep(0.05)


def _surface_worker(mav, vals, mode0, dur, stop, token):
    """Yüzey yön testinin arka plan RC override darbesi. `dur` saniye boyunca
    5 Hz override gönderir; HER turda (ilk tur dâhil, göndermeden ÖNCE) aracın
    hâlâ DISARM, aynı modda ve linkin canlı olduğunu doğrular — TOCTOU yok.
    Normal bitişte override'ı bırakır. Yeni bir test DEVRALDIĞINDA (token
    uyuşmazlığı) göndermeyi bırakır ama override'ı SIFIRLAMAZ: yeni worker'ın
    override'ı zaten tüm kanalları belirttiğinden geçiş kusursuzdur ve eski
    worker'ın sıfır-darbesi yeni testle çatışmaz."""
    sent = False
    try:
        end = time.time() + dur
        while time.time() < end and not stop.is_set():
            hb = mav.get_msg("HEARTBEAT", max_age=2)
            if hb is None or (hb.base_mode & 128) or \
                    (mode0 is not None and hb.custom_mode != mode0):
                break                       # arm / mod değişimi / bayat link
            if not mav.rc_override(vals):
                break
            sent = True
            stop.wait(0.2)                  # 5 Hz; supersede'e anında tepki verir
    finally:
        with STATE._servo_lock:
            superseded = token != STATE._servo_token
            if not superseded:
                STATE.servo_test = None
                STATE._servo_thread = None
                STATE.set_busy(None)
        if sent and not superseded:
            _release_override(mav)          # yalnız gerçekten biten test sıfırlar


class Handler(BaseHTTPRequestHandler):
    server_version = "ArduCheck/1.0"

    # --- helpers ---------------------------------------------------- #

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass   # istemci yanıtı beklemeden ayrıldı (ör. sayfa yenileme)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def log_message(self, fmt, *args):
        pass  # keep the console quiet

    # --- routing ---------------------------------------------------- #

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/api/state":
            since = None
            for part in query.split("&"):
                if part.startswith("msgs_since="):
                    try:
                        val = float(part[11:])
                        # nan/inf/0 -> tam geçmiş (nan karşılaştırmaları False
                        # döndürüp akışı sessizce boşaltır)
                        if val > 0 and val != float("inf"):
                            since = val
                    except ValueError:
                        pass
            return self._json(self._state_payload(since))
        if path == "/api/ports":
            return self._json({"ports": list_serial_ports()})
        if path == "/api/results":
            with STATE.lock:
                results = STATE.results
            return self._json({"results": results})
        if path == "/api/checklist_def":
            return self._json({"sections": MANUAL_ITEMS})
        if path == "/api/refparams":
            return self._refparams_info()
        if path == "/api/param_diff":
            return self._param_diff()
        if path == "/api/servos":
            return self._servos()
        if path == "/api/report":
            return self._report()
        return self._static(path)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        body = self._read_body()
        if path == "/api/connect":
            return self._connect(body)
        if path == "/api/disconnect":
            STATE.cal.cancel()        # break any running calibration worker first
            STATE.stop_servo_test()   # stop any running surface override
            if STATE.mav.link_stats()["connected"]:
                _release_override(STATE.mav)   # zero channels while link is up
            STATE.mav.disconnect()
            with STATE.lock:
                STATE.connect_info = None
                STATE.results = None
                if STATE.busy == "servo":
                    STATE.busy = None  # worker exits async; don't block reconnect
            return self._json({"ok": True})
        if path == "/api/run_checks":
            return self._run_checks(body)
        if path == "/api/checklist":
            with STATE.lock:
                STATE.manual_checklist = body.get("items", {})
            return self._json({"ok": True})
        if path == "/api/cal/start":
            return self._cal_start(body)
        if path == "/api/cal/action":
            return self._cal_action(body)
        if path == "/api/refparams/save_current":
            return self._refparams_save_current()
        if path == "/api/refparams/upload":
            return self._refparams_upload(body)
        if path == "/api/servo/trim":
            return self._servo_trim(body)
        if path == "/api/surface/test":
            return self._surface_test(body)
        return self._json({"error": "bilinmeyen uç nokta"}, 404)

    # --- handlers --------------------------------------------------- #

    def _connect(self, body):
        target = (body.get("target") or "").strip()
        baud = int(body.get("baud") or 115200)
        if not target:
            return self._json({"error": "Bağlantı hedefi boş"}, 400)
        if not STATE.set_busy("connect"):
            return self._json({"error": "Başka bir işlem sürüyor"}, 409)
        threading.Thread(target=_connect_worker, args=(target, baud),
                         daemon=True).start()
        return self._json({"ok": True})

    def _run_checks(self, body):
        if not STATE.mav.link_stats()["connected"]:
            return self._json({"error": "Araç bağlı değil"}, 400)
        if not STATE.set_busy("checks"):
            return self._json({"error": "Başka bir işlem sürüyor"}, 409)
        threading.Thread(target=_checks_worker, args=(body,),
                         daemon=True).start()
        return self._json({"ok": True})

    def _cal_start(self, body):
        """Start an interactive calibration session."""
        kind = body.get("kind")
        mav = STATE.mav
        if not mav.link_stats()["connected"]:
            return self._json({"error": "Araç bağlı değil"}, 400)
        hb = mav.get_msg("HEARTBEAT", max_age=5)
        if hb and (hb.base_mode & 128):
            return self._json({"error": "Araç ARM edilmiş — kalibrasyon "
                               "yalnızca disarm durumda yapılabilir."}, 400)
        if not STATE.set_busy("calibrate"):
            return self._json({"error": "Başka bir işlem sürüyor"}, 409)
        ok, err = STATE.cal.start(mav, kind,
                                  on_finish=lambda: STATE.set_busy(None))
        if not ok:
            STATE.set_busy(None)
            return self._json({"error": err}, 400)
        return self._json({"ok": True})

    def _cal_action(self, body):
        """Drive an active calibration: confirm / accept / cancel / close."""
        action = body.get("action")
        cal = STATE.cal
        if action in ("confirm", "accept"):
            cal.proceed()
        elif action == "cancel":
            cal.cancel()
        elif action == "close":
            cal.reset()
        else:
            return self._json({"error": "bilinmeyen eylem"}, 400)
        return self._json({"ok": True})

    # --- Faz 4: referans parametre + servo araçları ------------------ #

    SURFACE_FN = {  # SERVOn_FUNCTION -> Türkçe ad (kontrol yüzeyleri)
        4: "Kanatçık (Aileron)", 19: "Elevatör", 21: "Rudder",
        2: "Flap", 3: "Otomatik Flap",
        24: "Flaperon Sol", 25: "Flaperon Sağ",
        77: "Elevon Sol", 78: "Elevon Sağ",
        79: "V-Kuyruk Sol", 80: "V-Kuyruk Sağ",
    }

    def _disarmed_error(self):
        """Servo/param yazma kapıları: bağlı + DISARM şart. TOCTOU penceresini
        daraltmak için istek SONRASI gelen taze heartbeat beklenir (1 Hz).
        Hata METNİ döndürür (None = geçti) — yanıtı ÇAĞIRAN gönderir; _json'ın
        dönüş değerine asla güvenme (None döner)."""
        mav = STATE.mav
        if not mav.link_stats()["connected"]:
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

    def _refparams_info(self):
        info = {"exists": os.path.exists(checks_mod.REF_PARAMS_PATH),
                "path": os.path.basename(checks_mod.REF_PARAMS_PATH)}
        if info["exists"]:
            try:
                ref = checks_mod.load_ref_params()
                info["count"] = len(ref)
                info["mtime"] = os.path.getmtime(checks_mod.REF_PARAMS_PATH)
            except OSError as exc:
                info["error"] = str(exc)
        return self._json(info)

    def _param_diff(self):
        if not os.path.exists(checks_mod.REF_PARAMS_PATH):
            return self._json({"error": "Referans dosyası yok"}, 400)
        params = STATE.mav.params_snapshot()
        if not params:
            return self._json({"error": "Araç parametreleri henüz yok"}, 400)
        try:
            ref = checks_mod.load_ref_params()
        except OSError as exc:
            return self._json({"error": "Referans okunamadı: %s" % exc}, 500)
        diffs, missing, ignored = checks_mod.param_diff(params, ref)
        return self._json({"diffs": diffs, "missing": missing,
                           "ignored_volatile": ignored,
                           "param_fetch_done": STATE.mav.param_fetch_done,
                           "ref_count": len(ref),
                           "vehicle_count": len(params)})

    def _refparams_save_current(self):
        params = STATE.mav.params_snapshot()
        if not params:
            return self._json({"error": "Araç parametreleri henüz yok — "
                               "bağlanıp indirmenin bitmesini bekleyin"}, 400)
        tmp = checks_mod.REF_PARAMS_PATH + ".tmp"
        skipped = 0
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("# ArduCheck referans parametreleri — %s\n"
                        % time.strftime("%Y-%m-%d %H:%M:%S"))
                for name in sorted(params):
                    if checks_mod.is_volatile_param(name):
                        skipped += 1   # STAT_*, *_GND_PRESS vb. her koşuda
                        continue       # değişir; referansa yazmak gürültü
                    f.write("%s,%.10g\n" % (name, params[name]))
            os.replace(tmp, checks_mod.REF_PARAMS_PATH)
        except OSError as exc:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return self._json({"error": str(exc)}, 500)
        return self._json({"ok": True, "count": len(params) - skipped,
                           "skipped_volatile": skipped})

    def _refparams_upload(self, body):
        text = body.get("text") or ""
        # önce geçici olarak ayrıştır: bozuk dosya mevcut referansı ezmesin
        tmp = checks_mod.REF_PARAMS_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            ref = checks_mod.load_ref_params(tmp)
            if not ref:
                return self._json({"error": "Dosyada geçerli parametre satırı "
                                   "bulunamadı"}, 400)
            os.replace(tmp, checks_mod.REF_PARAMS_PATH)
        except OSError as exc:
            return self._json({"error": str(exc)}, 500)
        finally:
            try:                      # hata yollarında .tmp artığı kalmasın
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
        return self._json({"ok": True, "count": len(ref)})

    def _servos(self):
        mav = STATE.mav
        if not mav.link_stats()["connected"]:
            return self._json({"error": "Araç bağlı değil"}, 400)
        out = []
        for n in range(1, 17):
            fn = mav.get_param("SERVO%d_FUNCTION" % n)
            if fn is None or int(fn) not in self.SURFACE_FN:
                continue
            out.append({
                "n": n, "function": self.SURFACE_FN[int(fn)],
                "trim": mav.get_param("SERVO%d_TRIM" % n),
                "min": mav.get_param("SERVO%d_MIN" % n),
                "max": mav.get_param("SERVO%d_MAX" % n),
                "reversed": bool(mav.get_param("SERVO%d_REVERSED" % n) or 0),
            })
        return self._json({"servos": out})

    def _servo_trim(self, body):
        err = self._disarmed_error()
        if err:
            return self._json({"error": err}, 400)
        with STATE.lock:
            if STATE.busy:   # kalibrasyon/koşu sırasında parametre yazma
                return self._json({"error": "Başka bir işlem sürüyor"}, 409)
        try:
            n = int(body.get("servo"))
            delta = int(body.get("delta"))
        except (TypeError, ValueError):
            return self._json({"error": "servo/delta geçersiz"}, 400)
        if not (1 <= n <= 16 and -50 <= delta <= 50 and delta != 0):
            return self._json({"error": "servo 1-16, delta ±1..50 olmalı"}, 400)
        mav = STATE.mav
        name = "SERVO%d_TRIM" % n
        cur = mav.get_param(name)
        if cur is None:
            return self._json({"error": "%s okunamadı" % name}, 400)
        lo = mav.get_param("SERVO%d_MIN" % n) or 800
        hi = mav.get_param("SERVO%d_MAX" % n) or 2200
        new = max(max(800, lo), min(min(2200, hi), int(cur) + delta))
        ok, res = mav.set_param(name, new)
        if not ok:
            return self._json({"error": str(res)}, 502)
        return self._json({"ok": True, "name": name, "value": res})

    # RCMAP varsayılanları: roll=1 pitch=2 throttle=3 yaw=4
    SURFACE_AXES = {"roll": "RCMAP_ROLL", "pitch": "RCMAP_PITCH",
                    "yaw": "RCMAP_YAW"}

    def _surface_test(self, body):
        """MANUAL modda RC override darbesiyle yüzey yön testi. Karışım
        (mixing) üzerinden geçer: 'pitch up' verildiğinde her iki elevon da
        yukarı kalkmalı. Kullanıcı gözle doğrular.

        Override darbesi arka plan iş parçacığında çalışır; bu istek ANINDA
        döner (arayüz donmaz). Çalışan bir test varken farklı bir yöne basmak
        testi anında DEVRALIR (supersede) — kumanda kolu gibi akıcı."""
        mav = STATE.mav
        axis = body.get("axis")
        direction = body.get("dir")
        if axis not in self.SURFACE_AXES or direction not in ("plus", "minus"):
            return self._json({"error": "axis/dir geçersiz"}, 400)
        # Çalışan test varken yön değişiminde (supersede) pahalı taze-heartbeat
        # beklemesini atla ki akıcı olsun — worker zaten her 0.2 sn'de arm/mod
        # kontrolü yapıyor. İlk başlatmada tam disarm kapısı uygulanır.
        superseding = STATE.busy == "servo" and STATE.servo_test is not None
        if superseding:
            if not mav.link_stats()["connected"]:
                return self._json({"error": "Araç bağlı değil"}, 400)
            hb = mav.get_msg("HEARTBEAT", max_age=3)
            if hb is not None and (hb.base_mode & 128):
                return self._json({"error": "Araç ARM edilmiş — bu işlem "
                                   "yalnız disarm durumda yapılabilir."}, 400)
        else:
            err = self._disarmed_error()
            if err:
                return self._json({"error": err}, 400)
        t = checks_mod.telemetry_summary(mav)
        if t.get("mode") != "MANUAL":
            return self._json({"error": "Yüzey testi için aracı MANUAL moda "
                               "alın (kumandadan ya da GCS'den)."}, 400)
        ot = mav.get_param("RC_OVERRIDE_TIME")
        if ot is None:
            return self._json({"error": "RC_OVERRIDE_TIME okunamadı — "
                               "parametre indirmenin bitmesini bekleyin."}, 400)
        if ot <= 0:
            return self._json({"error": "RC_OVERRIDE_TIME=%g: RC override "
                               "kapalı ya da zaman aşımı yok — test "
                               "reddedildi (3 önerilir)." % ot}, 400)
        chan = int(mav.get_param(self.SURFACE_AXES[axis]) or
                   {"roll": 1, "pitch": 2, "yaw": 4}[axis])
        if not (1 <= chan <= 8):
            return self._json({"error": "RCMAP kanalı 1-8 dışı"}, 400)
        rev = bool(mav.get_param("RC%d_REVERSED" % chan) or 0)
        # ArduPilot giriş kuralı: yüksek PWM = pozitif giriş = burun yukarı /
        # sağa yatış / sağa yaw (MANUAL passthrough, ArduPlane/mode_manual.cpp).
        # RCn_REVERSED işareti çevirir.
        high = (direction == "plus")
        if rev:
            high = not high
        pwm = 1900 if high else 1100
        vals = [0] * 8
        vals[chan - 1] = pwm
        hb0 = mav.get_msg("HEARTBEAT")
        mode0 = hb0.custom_mode if hb0 else None
        try:
            dur = min(3.0, max(0.5, float(body.get("ms") or 1500) / 1000.0))
        except (TypeError, ValueError):
            dur = 1.5
        ok, err = STATE.start_servo_test(vals, mode0, dur, axis, direction)
        if not ok:
            return self._json({"error": err}, 409)
        return self._json({"ok": True, "started": True, "chan": chan,
                           "pwm": pwm, "dur": dur})

    def _state_payload(self, msgs_since=None):
        mav = STATE.mav
        with STATE.lock:
            busy = STATE.busy
            progress = STATE.busy_progress
            connect_info = STATE.connect_info
            servo_test = STATE.servo_test
            have_results = STATE.results is not None
            results_time = STATE.results.get("time") if STATE.results else None
        payload = {
            "boot": BOOT_TIME,
            "results_time": results_time,
            "link": mav.link_stats(),
            "busy": busy,
            "servo_test": servo_test,
            "progress": progress,
            "connect_info": connect_info,
            "param_progress": mav.param_progress(),
            "have_results": have_results,
            "telemetry": checks_mod.telemetry_summary(mav),
            "vehicle": checks_mod.vehicle_info(mav),
            "calibration": STATE.cal.snapshot(),
            # msgs_since verilirse KESİN olarak ondan yenileri döner (istemci
            # tam oturum geçmişini kendinde biriktirir); recent_statustexts'in
            # >= penceresi diğer tüketiciler için korunur, burada > daraltılır
            "messages": [
                {"t": t, "sev": sev, "sev_name": SEVERITY_NAMES.get(sev, "?"),
                 "text": text, "count": count}
                for (t, sev, text, count)
                in mav.recent_statustexts(since=msgs_since)
                if msgs_since is None or t > msgs_since
            ],
        }
        return payload

    def _report(self):
        with STATE.lock:
            results = STATE.results
            checklist = dict(STATE.manual_checklist)
        if not results:
            return self._json({"error": "Önce kontrolleri çalıştırın"}, 400)
        html = report_mod.render(results, checklist,
                                 checks_mod.telemetry_summary(STATE.mav))
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Disposition",
                         'attachment; filename="arducheck-rapor.html"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        full = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not (full == WEB_DIR or full.startswith(WEB_DIR + os.sep)) \
                or not os.path.isfile(full):
            self.send_response(404)
            self.end_headers()
            return
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(port=8642, open_browser=True):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d" % port
    print("ArduCheck çalışıyor: " + url)
    if open_browser:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatılıyor...")
    finally:
        STATE.mav.disconnect()
