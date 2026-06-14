"""Canlı telemetri paneli — saf Qt göstergeleri (harita yok).

``checks.telemetry_summary`` çıktısını okur; web arayüzündeki durum kutucukları
ve küçük harita yerine sade değer kutuları gösterir (GPS/konum/EKF/batarya/
titreşim/RC/attitude/hava hızı).
"""

from PyQt5.QtWidgets import (QGridLayout, QGroupBox, QLabel, QVBoxLayout,
                             QWidget)

from ..style import value_box


class TelemetryPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        box = QGroupBox("Canlı Telemetri")
        grid = QGridLayout(box)
        grid.setSpacing(6)
        self._v = {}

        cells = [
            ("mode", "Uçuş Modu"), ("armed", "Arm Durumu"),
            ("gps", "GPS"), ("sats", "Uydu / HDOP"),
            ("pos", "Konum"), ("alt", "İrtifa / Yön"),
            ("ekf", "EKF"), ("ekf_var", "EKF Varyans (en kötü)"),
            ("batt", "Batarya"), ("vcc", "Kart 5V"),
            ("vib", "Titreşim x/y/z"), ("clip", "Clipping"),
            ("rc", "RC Kanal / RSSI"), ("att", "Roll / Pitch"),
            ("airspeed", "Hava Hızı"), ("prearm", "Pre-Arm"),
        ]
        cols = 4
        for i, (key, title) in enumerate(cells):
            frame, lbl = value_box(title)
            self._v[key] = lbl
            grid.addWidget(frame, i // cols, i % cols)
        root.addWidget(box)
        root.addStretch(1)

    @staticmethod
    def _set(lbl, text, color=None):
        lbl.setText(text if text else "—")
        if color:
            lbl.setStyleSheet("color:%s;font-size:15px;font-weight:700;"
                              "border:none;background:none;" % color)
        else:
            lbl.setStyleSheet("color:#1c2833;font-size:15px;font-weight:700;"
                              "border:none;background:none;")

    def update_telemetry(self, t):
        t = t or {}
        V = self._v

        self._set(V["mode"], t.get("mode"))
        armed = t.get("armed")
        if armed is None:
            self._set(V["armed"], "—")
        elif armed:
            self._set(V["armed"], "ARM", "#c0392b")
        else:
            self._set(V["armed"], "Disarm", "#1d8348")

        gps = t.get("gps") or {}
        if gps:
            fix = gps.get("fix", 0)
            col = "#1d8348" if fix >= 3 else ("#b7950b" if fix >= 2 else "#c0392b")
            self._set(V["gps"], gps.get("fix_name", "?"), col)
            hd = gps.get("hdop")
            self._set(V["sats"], "%s uydu · HDOP %s"
                      % (gps.get("sats", "?"),
                         ("%.2f" % hd) if hd is not None else "?"))
        else:
            self._set(V["gps"], "—")
            self._set(V["sats"], "—")

        pos = t.get("pos") or {}
        if pos:
            self._set(V["pos"], "%.6f, %.6f" % (pos["lat"], pos["lon"]))
            ra = pos.get("rel_alt")
            hdg = pos.get("hdg")
            parts = []
            if ra is not None:
                parts.append("%.1f m" % ra)
            if hdg is not None:
                parts.append("%.0f°" % hdg)
            self._set(V["alt"], " · ".join(parts) if parts else "—")
        else:
            self._set(V["pos"], "—")
            self._set(V["alt"], "—")

        ekf_ok = t.get("ekf_ok")
        if ekf_ok is None:
            self._set(V["ekf"], "—")
        else:
            self._set(V["ekf"], "Sağlıklı" if ekf_ok else "Sorunlu",
                      "#1d8348" if ekf_ok else "#c0392b")
        ev = t.get("ekf_var") or {}
        if ev:
            worst = max(ev.values())
            col = "#1d8348" if worst < 0.5 else ("#b7950b" if worst < 0.8
                                                 else "#c0392b")
            self._set(V["ekf_var"], "%.2f" % worst, col)
        else:
            self._set(V["ekf_var"], "—")

        batt = t.get("batt") or {}
        if batt:
            v = batt.get("volt")
            a = batt.get("current")
            rem = batt.get("remaining")
            parts = []
            if v is not None:
                parts.append("%.2f V" % v)
            if a is not None:
                parts.append("%.1f A" % a)
            if rem is not None:
                parts.append("%%%d" % rem)
            self._set(V["batt"], " · ".join(parts) if parts else "—")
        else:
            self._set(V["batt"], "—")
        vcc = t.get("vcc")
        if vcc is not None:
            col = "#1d8348" if 4.8 <= vcc <= 5.4 else "#b7950b"
            self._set(V["vcc"], "%.2f V" % vcc, col)
        else:
            self._set(V["vcc"], "—")

        vib = t.get("vib") or {}
        if vib:
            mx = max(vib.get("x", 0), vib.get("y", 0), vib.get("z", 0))
            col = "#1d8348" if mx < 30 else ("#b7950b" if mx <= 60 else "#c0392b")
            self._set(V["vib"], "%.0f / %.0f / %.0f"
                      % (vib.get("x", 0), vib.get("y", 0), vib.get("z", 0)), col)
            self._set(V["clip"], str(vib.get("clip", 0)),
                      "#c0392b" if vib.get("clip", 0) else None)
        else:
            self._set(V["vib"], "—")
            self._set(V["clip"], "—")

        rc = t.get("rc") or {}
        if rc:
            rssi = rc.get("rssi")
            self._set(V["rc"], "%s kanal · RSSI %s"
                      % (rc.get("chan", "?"),
                         rssi if rssi is not None else "—"))
        else:
            self._set(V["rc"], "—")

        roll = t.get("roll")
        pitch = t.get("pitch")
        if roll is not None and pitch is not None:
            self._set(V["att"], "%.0f° / %.0f°" % (roll, pitch))
        else:
            self._set(V["att"], "—")

        asp = t.get("airspeed")
        alt = t.get("alt")
        if asp is not None:
            self._set(V["airspeed"], "%.1f m/s  ·  alt %s"
                      % (asp, ("%.0f m" % alt) if alt is not None else "?"))
        else:
            self._set(V["airspeed"], "—")

        pa = t.get("prearm_ok")
        if pa is None:
            self._set(V["prearm"], "—")
        else:
            self._set(V["prearm"], "Geçiyor" if pa else "Geçmiyor",
                      "#1d8348" if pa else "#c0392b")
