"""PreflightPage — gömülebilir ana widget ve PreflightWindow sarmalayıcısı.

Tüm parçaları PreflightController etrafında birleştirir. Host uygulama ya bir
dronekit ``Vehicle`` verir (gömülü mod) ya da hiçbir şey vermez (bağımsız mod;
bağlantıyı kullanıcı bağlantı çubuğundan kurar).
"""

import os
import time
import webbrowser

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QFileDialog, QHBoxLayout, QLabel, QMainWindow,
                             QPushButton, QSplitter, QTabWidget, QVBoxLayout,
                             QWidget)

from .controller import PreflightController
from .widgets.calibration_panel import CalibrationPanel
from .widgets.connection_bar import ConnectionBar
from .widgets.manual_checklist import ManualChecklist
from .widgets.messages_strip import MessagesView
from .widgets.param_panel import ParamPanel
from .widgets.servo_panel import ServoPanel
from .widgets.status_center import StatusView
from .widgets.telemetry_panel import TelemetryPanel

NOTICE_COLOR = {"info": "#1d8348", "warn": "#b7950b", "error": "#c0392b"}


class PreflightPage(QWidget):
    """Tek bir QWidget içinde tüm uçuş öncesi kontrol arayüzü.

    Args:
        vehicle: hazır bir dronekit ``Vehicle`` (gömülü mod) ya da ``None``
            (bağımsız mod — bağlantı çubuğu gösterilir).
    """

    def __init__(self, vehicle=None, parent=None):
        super().__init__(parent)
        self.controller = PreflightController(vehicle=vehicle, parent=self)
        self._last_results = None
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(lambda: self.notice_lbl.setText(""))

        self._build_ui()
        self._wire()

        # ilk port listesi (bağımsız mod)
        if not self.controller.embedded:
            self._refresh_ports()
        self.controller.start()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self.conn_bar = ConnectionBar(embedded=self.controller.embedded)
        root.addWidget(self.conn_bar)

        # eylem çubuğu
        actions = QHBoxLayout()
        self.run_btn = QPushButton("▶ Tüm Kontrolleri Çalıştır")
        self.run_btn.setStyleSheet("font-weight:700;")
        self.run_btn.setEnabled(False)
        self.report_btn = QPushButton("⬇ Rapor")
        self.report_btn.setEnabled(False)
        self.progress_lbl = QLabel("")
        self.progress_lbl.setStyleSheet("color:#2471a3;")
        self.notice_lbl = QLabel("")
        actions.addWidget(self.run_btn)
        actions.addWidget(self.report_btn)
        actions.addWidget(self.progress_lbl)
        actions.addStretch(1)
        actions.addWidget(self.notice_lbl)
        root.addLayout(actions)

        # ana bölünme: sol sekmeler / sağ telemetri+mesajlar
        split = QSplitter(Qt.Horizontal)

        self.tabs = QTabWidget()
        self.status_view = StatusView()
        self.cal_panel = CalibrationPanel()
        self.servo_panel = ServoPanel(self.controller, self._notify)
        self.param_panel = ParamPanel(self.controller, self._notify)
        self.manual = ManualChecklist(self.controller.manual_items())
        self.tabs.addTab(self.status_view, "Durum")
        self.tabs.addTab(self.cal_panel, "Kalibrasyon")
        self.tabs.addTab(self.servo_panel, "Servo")
        self.tabs.addTab(self.param_panel, "Parametre")
        self.tabs.addTab(self.manual, "Manuel")
        split.addWidget(self.tabs)

        right = QSplitter(Qt.Vertical)
        self.telemetry = TelemetryPanel()
        self.messages = MessagesView()
        right.addWidget(self.telemetry)
        right.addWidget(self.messages)
        right.setSizes([320, 240])
        split.addWidget(right)
        split.setSizes([640, 480])
        root.addWidget(split, 1)

    # ------------------------------------------------------------------ #
    def _wire(self):
        c = self.controller
        c.state_changed.connect(self._on_state)
        c.results_ready.connect(self._on_results)
        c.messages_appended.connect(self.messages.append_messages)
        c.connection_changed.connect(self._on_connection)
        c.notice.connect(self._notify)

        self.conn_bar.connectRequested.connect(c.connect_target)
        self.conn_bar.disconnectRequested.connect(c.disconnect)
        self.conn_bar.refreshPortsRequested.connect(self._refresh_ports)

        self.cal_panel.startRequested.connect(c.start_calibration)
        self.cal_panel.actionRequested.connect(c.cal_action)

        self.manual.itemToggled.connect(c.set_manual_item)
        self.manual.progressChanged.connect(lambda *_: self._refresh_status())

        self.run_btn.clicked.connect(lambda: c.run_checks({}))
        self.report_btn.clicked.connect(self._save_report)

    # ----- controller -> ui ------------------------------------------ #
    def _on_state(self, payload):
        self.conn_bar.set_state(payload)
        self.telemetry.update_telemetry(payload.get("telemetry"))
        self.cal_panel.update_cal(payload.get("calibration"))
        self.servo_panel.update_state(payload)

        connected = bool(payload.get("connected"))
        busy = payload.get("busy")
        idle = busy is None
        self.run_btn.setEnabled(connected and idle)
        self.cal_panel.set_can_start(connected and idle)
        self.report_btn.setEnabled(self._last_results is not None)

        prog = payload.get("progress") or ""
        if busy and prog:
            self.progress_lbl.setText("⟳ " + prog)
        elif busy == "servo":
            self.progress_lbl.setText("⟳ Yüzey testi sürüyor")
        else:
            self.progress_lbl.setText("")

    def _on_results(self, results):
        self._last_results = results
        self.report_btn.setEnabled(True)
        self._refresh_status()
        self.tabs.setCurrentWidget(self.status_view)

    def _refresh_status(self):
        if self._last_results is not None:
            self.status_view.set_results(
                self._last_results, self.manual.done_count(), self.manual.total())

    def _on_connection(self, connected, info):
        if connected:
            self._notify("info", "Bağlandı. Parametreler indiriliyor olabilir; "
                                 "kontrolleri çalıştırmadan önce bekleyin.")
            # bağlantı kurulunca servo/param bilgisini tazele
            QTimer.singleShot(1500, self.param_panel.refresh_info)
        else:
            err = (info or {}).get("error")
            if err:
                self._notify("error", "Bağlantı kesildi: %s" % err)

    def _notify(self, level, text):
        if not text:
            return
        self.notice_lbl.setText(text)
        self.notice_lbl.setStyleSheet("color:%s;font-weight:600;"
                                      % NOTICE_COLOR.get(level, "#566573"))
        self._notice_timer.start(6000)

    # ----- yardımcılar ----------------------------------------------- #
    def _refresh_ports(self):
        try:
            ports = self.controller.list_serial_ports()
        except Exception:
            ports = []
        self.conn_bar.set_ports(ports)

    def _save_report(self):
        html, err = self.controller.build_report()
        if err:
            self._notify("warn", err)
            return
        default = os.path.join(os.path.expanduser("~"),
                               "arducheck-rapor-%s.html"
                               % time.strftime("%Y%m%d-%H%M%S"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Raporu kaydet", default, "HTML (*.html)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
        except OSError as exc:
            self._notify("error", "Rapor yazılamadı: %s" % exc)
            return
        self._notify("info", "Rapor kaydedildi: " + path)
        try:
            webbrowser.open("file://" + os.path.abspath(path))
        except Exception:
            pass

    # ----- kapanış ---------------------------------------------------- #
    def shutdown(self):
        """Host pencere kapanırken çağırın: poll'ü durdur, bağlantıyı temizle.
        Gömülü modda host'un Vehicle'ı KAPATILMAZ (sahip değiliz)."""
        try:
            self.controller.shutdown()
        except Exception:
            pass


class PreflightWindow(QMainWindow):
    """PreflightPage'i ayrı bir pencerede gösteren sarmalayıcı (host bir
    ``QDialog``/``QMainWindow`` içinde de açabilir)."""

    def __init__(self, vehicle=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ArduCheck — Uçuş Öncesi Kontrol")
        self.resize(1180, 760)
        self.page = PreflightPage(vehicle=vehicle)
        self.setCentralWidget(self.page)
        self.statusBar().showMessage(
            "Gömülü (host bağlantısı)" if self.page.controller.embedded
            else "Bağımsız mod — bağlantı çubuğundan araca bağlanın")

    def closeEvent(self, event):
        self.page.shutdown()
        super().closeEvent(event)
