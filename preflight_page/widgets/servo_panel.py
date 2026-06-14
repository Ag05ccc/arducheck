"""Servo araçları — yüzey trim ayarı (SERVOn_TRIM yazar) ve yüzey yön testi
(MANUAL + DISARM şartıyla RC override darbesi). Motor mantığı controller'da."""

from PyQt5.QtWidgets import (QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)


class ServoPanel(QWidget):
    def __init__(self, controller, notify, parent=None):
        super().__init__(parent)
        self._c = controller
        self._notify = notify
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(8)

        note = QLabel("Yalnızca araç DISARM iken. Trim, kontrol yüzeyi servolarını "
                      "nötrde düz tutmak içindir; yön testi MANUAL modda mikslemeyi "
                      "test eder (operatör gözle doğrular).")
        note.setWordWrap(True)
        note.setStyleSheet("color:#7f8c8d;")
        root.addWidget(note)

        # --- servo trim listesi ---
        trim_box = QGroupBox("Yüzey Servo Trim")
        tl = QVBoxLayout(trim_box)
        refresh = QPushButton("⟳ Servoları Yükle / Yenile")
        refresh.clicked.connect(self.reload_servos)
        tl.addWidget(refresh)
        self._rows_host = QWidget()
        self._rows = QVBoxLayout(self._rows_host)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._rows_host)
        scroll.setMinimumHeight(140)
        tl.addWidget(scroll)
        root.addWidget(trim_box)

        # --- yüzey yön testi ---
        st_box = QGroupBox("Yüzey Yön Testi (MANUAL modda)")
        g = QGridLayout(st_box)
        tests = [
            ("Burun yukarı ▲", "pitch", "plus"),
            ("Burun aşağı ▼", "pitch", "minus"),
            ("Sağa yatış ▶", "roll", "plus"),
            ("Sola yatış ◀", "roll", "minus"),
            ("Sağa yaw ↻", "yaw", "plus"),
            ("Sola yaw ↺", "yaw", "minus"),
        ]
        for i, (label, axis, direction) in enumerate(tests):
            b = QPushButton(label)
            b.clicked.connect(
                lambda _=False, a=axis, d=direction: self._do_surface(a, d))
            g.addWidget(b, i // 2, i % 2)
        self.active_lbl = QLabel("")
        self.active_lbl.setStyleSheet("color:#2471a3;font-weight:600;")
        g.addWidget(self.active_lbl, 3, 0, 1, 2)
        root.addWidget(st_box)
        root.addStretch(1)

        self._empty()

    def _empty(self):
        self._clear_rows()
        lbl = QLabel("Servoları görmek için “Yükle / Yenile”ye basın.")
        lbl.setStyleSheet("color:#7f8c8d;")
        self._rows.addWidget(lbl)

    def _clear_rows(self):
        while self._rows.count():
            it = self._rows.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def reload_servos(self):
        servos, err = self._c.list_servos()
        self._clear_rows()
        if err:
            lbl = QLabel(err)
            lbl.setStyleSheet("color:#c0392b;")
            self._rows.addWidget(lbl)
            return
        if not servos:
            lbl = QLabel("Kontrol yüzeyi servosu bulunamadı "
                         "(SERVOn_FUNCTION atanmamış olabilir).")
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color:#7f8c8d;")
            self._rows.addWidget(lbl)
            return
        for s in servos:
            self._rows.addWidget(self._servo_row(s))

    def _servo_row(self, s):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        rev = " · ters" if s["reversed"] else ""
        trim = s.get("trim")
        name = QLabel("SERVO%d · %s  (trim %s%s)"
                      % (s["n"], s["function"],
                         int(trim) if trim is not None else "?", rev))
        name.setMinimumWidth(260)
        h.addWidget(name)
        for delta, label in ((-10, "−10"), (-1, "−1"), (1, "+1"), (10, "+10")):
            b = QPushButton(label)
            b.setFixedWidth(46)
            b.clicked.connect(lambda _=False, n=s["n"], d=delta: self._do_trim(n, d))
            h.addWidget(b)
        h.addStretch(1)
        return row

    def _do_trim(self, n, delta):
        ok, msg = self._c.servo_trim(n, delta)
        self._notify("info" if ok else "warn", msg)
        if ok:
            self.reload_servos()

    def _do_surface(self, axis, direction):
        ok, msg = self._c.surface_test(axis, direction)
        self._notify("info" if ok else "warn",
                     ("Yüzey testi: " + msg) if ok else msg)

    def update_state(self, payload):
        st = payload.get("servo_test")
        if st:
            self.active_lbl.setText("● Etkin yüzey testi: %s / %s"
                                    % (st.get("axis"), st.get("dir")))
        else:
            self.active_lbl.setText("")
