"""ArduCheck HTTP server: serves the web UI and a small JSON API.

Stdlib-only (http.server); pymavlink is the app's single external dependency.
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import checks as checks_mod
from .calibration import CalManager
from .checklist_def import MANUAL_ITEMS
from .core import preflight as core
from .mavlink_client import MavClient, list_serial_ports

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
        self.operation = core.OperationState()
        self.lock = threading.Lock()
        self.results = None        # last check run results (dict)
        self.connect_info = None   # {"target":..., "error":...}
        self.manual_checklist = {} # id -> bool (mirrored from UI for reports)
        self.surface = core.SurfaceTestManager(self.operation)

    def set_busy(self, name, progress=""):
        return self.operation.set_busy(name, progress)

    def set_progress(self, text):
        self.operation.set_progress(text)

    def stop_servo_test(self):
        self.surface.stop()


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
                core.release_override(STATE.mav)   # zero channels while link is up
            STATE.mav.disconnect()
            with STATE.lock:
                STATE.connect_info = None
                STATE.results = None
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

    def _refparams_info(self):
        return self._json(core.refparams_info())

    def _param_diff(self):
        payload, err = core.param_diff(STATE.mav)
        if err:
            return self._json({"error": err}, 400)
        return self._json(payload)

    def _refparams_save_current(self):
        payload, err = core.save_ref_current(STATE.mav)
        if err:
            return self._json({"error": err}, 400)
        return self._json(payload)

    def _refparams_upload(self, body):
        payload, err = core.upload_ref(body.get("text") or "")
        if err:
            return self._json({"error": err}, 400)
        return self._json(payload)

    def _servos(self):
        out, err = core.list_servos(STATE.mav)
        if err:
            return self._json({"error": err}, 400)
        return self._json({"servos": out})

    def _servo_trim(self, body):
        ok, err, payload = core.servo_trim(
            STATE.mav, STATE.operation, body.get("servo"), body.get("delta"))
        if err:
            return self._json({"error": err}, 409 if err == "Başka bir işlem sürüyor" else 400)
        return self._json({"ok": ok, **payload})

    def _surface_test(self, body):
        """MANUAL modda RC override darbesiyle yüzey yön testi. Karışım
        (mixing) üzerinden geçer: 'pitch up' verildiğinde her iki elevon da
        yukarı kalkmalı. Kullanıcı gözle doğrular.

        Override darbesi arka plan iş parçacığında çalışır; bu istek ANINDA
        döner (arayüz donmaz). Çalışan bir test varken farklı bir yöne basmak
        testi anında DEVRALIR (supersede) — kumanda kolu gibi akıcı."""
        ok, err, payload = core.surface_test(
            STATE.mav, STATE.operation, STATE.surface,
            body.get("axis"), body.get("dir"), body.get("ms") or 1500)
        if err:
            return self._json({"error": err}, 409 if err == "Başka bir işlem sürüyor" else 400)
        return self._json({"ok": True, "started": True, **payload})

    def _state_payload(self, msgs_since=None):
        with STATE.lock:
            connect_info = STATE.connect_info
            results = STATE.results
        # msgs_since verilirse KESİN olarak ondan yenileri döner (istemci tam
        # oturum geçmişini kendinde biriktirir); recent_statustexts'in >=
        # penceresi diğer tüketiciler için korunur, burada > daraltılır.
        return core.build_state_payload(
            STATE.mav, STATE.operation, STATE.cal, connect_info, results,
            STATE.surface.snapshot(), msgs_since=msgs_since, boot=BOOT_TIME)

    def _report(self):
        with STATE.lock:
            results = STATE.results
            checklist = dict(STATE.manual_checklist)
        html, err = core.render_report(results, checklist, STATE.mav)
        if err:
            return self._json({"error": err}, 400)
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
