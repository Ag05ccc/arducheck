"""Kalibrasyon paneli — pusula / ivmeölçer (6 poz) / yatay / jiroskop / baro.

``CalManager.snapshot()`` çıktısını okur ve akışı confirm/cancel/close ile sürer
(motor app/calibration.py'de; bu yalnızca onun Qt yüzüdür)."""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QProgressBar, QPushButton, QVBoxLayout, QWidget)

KINDS = [
    ("compass", "Pusula"),
    ("accel", "İvmeölçer (6 poz)"),
    ("level", "Yatay (Level)"),
    ("gyro", "Jiroskop"),
    ("baro", "Baro / Hava Hızı"),
]

TERMINAL = ("success", "failed", "cancelled", "error")
ACTIVE = ("running", "waiting", "accept")
RESULT_COLOR = {"success": "#1d8348", "failed": "#c0392b",
                "cancelled": "#7f8c8d", "error": "#c0392b"}


class CalibrationPanel(QWidget):
    startRequested = pyqtSignal(str)     # kind
    actionRequested = pyqtSignal(str)    # confirm/cancel/close

    def __init__(self, parent=None):
        super().__init__(parent)
        self._can_start = False
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        note = QLabel("Yalnızca araç DISARM iken. Pusula/ivmeölçer aracı "
                      "fiziksel döndürmeyi gerektirir (sabit SITL'de tamamlanmaz).")
        note.setWordWrap(True)
        note.setStyleSheet("color:#7f8c8d;")
        root.addWidget(note)

        btn_box = QGroupBox("Kalibrasyon Başlat")
        grid = QGridLayout(btn_box)
        self._start_btns = []
        for i, (kind, label) in enumerate(KINDS):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=kind: self.startRequested.emit(k))
            grid.addWidget(b, i // 3, i % 3)
            self._start_btns.append(b)
        root.addWidget(btn_box)

        self.session = QGroupBox("Oturum")
        sl = QVBoxLayout(self.session)
        self.title = QLabel("—")
        self.title.setStyleSheet("font-weight:700;font-size:14px;")
        self.step = QLabel("")
        self.step.setStyleSheet("color:#2471a3;font-weight:600;")
        self.message = QLabel("Bir kalibrasyon seçin.")
        self.message.setWordWrap(True)
        self.ap_prompt = QLabel("")
        self.ap_prompt.setWordWrap(True)
        self.ap_prompt.setStyleSheet("color:#7f8c8d;font-style:italic;")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.report = QLabel("")
        self.report.setWordWrap(True)
        self.report.setStyleSheet("color:#566573;font-family:monospace;font-size:12px;")
        sl.addWidget(self.title)
        sl.addWidget(self.step)
        sl.addWidget(self.message)
        sl.addWidget(self.ap_prompt)
        sl.addWidget(self.progress)
        sl.addWidget(self.report)

        actions = QHBoxLayout()
        self.confirm_btn = QPushButton("Onayla")
        self.confirm_btn.setStyleSheet("font-weight:600;")
        self.confirm_btn.clicked.connect(lambda: self.actionRequested.emit("confirm"))
        self.cancel_btn = QPushButton("İptal")
        self.cancel_btn.clicked.connect(lambda: self.actionRequested.emit("cancel"))
        self.close_btn = QPushButton("Kapat")
        self.close_btn.clicked.connect(lambda: self.actionRequested.emit("close"))
        actions.addWidget(self.confirm_btn)
        actions.addWidget(self.cancel_btn)
        actions.addWidget(self.close_btn)
        actions.addStretch(1)
        sl.addLayout(actions)
        root.addWidget(self.session)
        root.addStretch(1)

        self.update_cal({"phase": "idle"})

    def set_can_start(self, ok):
        self._can_start = bool(ok)
        self._refresh_start_enabled(None)

    def _refresh_start_enabled(self, phase):
        idle = phase in (None, "idle")
        for b in self._start_btns:
            b.setEnabled(self._can_start and idle)

    def update_cal(self, snap):
        snap = snap or {}
        phase = snap.get("phase", "idle")
        self._refresh_start_enabled(phase)

        self.title.setText(snap.get("title") or "—")
        msg = snap.get("message") or ""
        result = snap.get("result")
        if result in RESULT_COLOR:
            self.message.setStyleSheet("font-weight:600;color:%s;"
                                       % RESULT_COLOR[result])
        else:
            self.message.setStyleSheet("color:#1c2833;")
        self.message.setText(msg or "Bir kalibrasyon seçin.")

        ap = snap.get("ap_prompt") or ""
        self.ap_prompt.setText(ap)
        self.ap_prompt.setVisible(bool(ap))

        total = snap.get("total_steps") or 0
        st = snap.get("step") or 0
        if total > 1 and st:
            self.step.setText("Adım %d / %d" % (st, total))
            self.step.show()
        else:
            self.step.hide()

        prog = snap.get("progress")
        if prog is None:
            self.progress.hide()
        else:
            self.progress.show()
            self.progress.setValue(int(prog))

        rep = snap.get("report") or ""
        self.report.setText(rep)
        self.report.setVisible(bool(rep))

        can_confirm = bool(snap.get("can_confirm"))
        self.confirm_btn.setText(snap.get("confirm_label") or "Onayla")
        self.confirm_btn.setVisible(can_confirm)
        self.confirm_btn.setEnabled(can_confirm)
        self.cancel_btn.setVisible(phase in ACTIVE)
        self.close_btn.setVisible(phase in TERMINAL)
