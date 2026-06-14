"""Bağlantı çubuğu — bağlantı türü seçimi, bağlan/kes ve canlı link durumu.

Gömülü modda (host Vehicle verildiğinde) bağlantı kontrolleri gizlenir; yalnızca
'host bağlantısı kullanılıyor' bilgisi ve canlı link göstergesi kalır.
"""

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit,
                             QPushButton, QStackedWidget, QWidget)


class ConnectionBar(QWidget):
    connectRequested = pyqtSignal(str, int)   # target, baud
    disconnectRequested = pyqtSignal()
    refreshPortsRequested = pyqtSignal()

    def __init__(self, embedded=False, parent=None):
        super().__init__(parent)
        self._embedded = embedded
        self._ports = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)

        if embedded:
            info = QLabel("⛓  Host uygulamasının dronekit bağlantısı kullanılıyor")
            info.setStyleSheet("font-weight:600;color:#1c2833;")
            lay.addWidget(info)
            lay.addStretch(1)
        else:
            self.kind = QComboBox()
            self.kind.addItems(["Seri / USB", "Telemetri (Seri)", "UDP",
                                "TCP / SITL"])
            self.kind.currentIndexChanged.connect(self._kind_changed)
            lay.addWidget(QLabel("Bağlantı:"))
            lay.addWidget(self.kind)

            # girdi alanı türüne göre değişir (stacked)
            self.stack = QStackedWidget()
            # 0: seri (port combo + yenile)
            serial_w = QWidget()
            sl = QHBoxLayout(serial_w)
            sl.setContentsMargins(0, 0, 0, 0)
            self.port = QComboBox()
            self.port.setMinimumWidth(220)
            refresh = QPushButton("⟳")
            refresh.setFixedWidth(30)
            refresh.setToolTip("Portları yenile")
            refresh.clicked.connect(self.refreshPortsRequested.emit)
            sl.addWidget(self.port)
            sl.addWidget(refresh)
            self.stack.addWidget(serial_w)
            # 1: ağ (host + port)
            net_w = QWidget()
            nl = QHBoxLayout(net_w)
            nl.setContentsMargins(0, 0, 0, 0)
            self.host = QLineEdit("127.0.0.1")
            self.host.setFixedWidth(120)
            self.netport = QLineEdit("5760")
            self.netport.setFixedWidth(70)
            nl.addWidget(QLabel("Adres:"))
            nl.addWidget(self.host)
            nl.addWidget(QLabel("Port:"))
            nl.addWidget(self.netport)
            self.stack.addWidget(net_w)
            lay.addWidget(self.stack)

            lay.addWidget(QLabel("Baud:"))
            self.baud = QComboBox()
            self.baud.addItems(["115200", "57600", "921600", "230400", "38400"])
            lay.addWidget(self.baud)

            self.connect_btn = QPushButton("Bağlan")
            self.connect_btn.clicked.connect(self._do_connect)
            self.disconnect_btn = QPushButton("Kes")
            self.disconnect_btn.clicked.connect(self.disconnectRequested.emit)
            self.disconnect_btn.setEnabled(False)
            lay.addWidget(self.connect_btn)
            lay.addWidget(self.disconnect_btn)
            lay.addStretch(1)
            self._kind_changed(0)

        self.status = QLabel("Bağlı değil")
        self.status.setStyleSheet("font-weight:600;color:#c0392b;")
        lay.addWidget(self.status)

    # ----- standalone yardımcıları ----------------------------------- #

    def _kind_changed(self, idx):
        kind = self.kind.currentText()
        if kind.startswith("Seri") or kind.startswith("Telemetri"):
            self.stack.setCurrentIndex(0)
            self.baud.setCurrentText("57600" if "Telemetri" in kind else "115200")
        elif kind == "UDP":
            self.stack.setCurrentIndex(1)
            self.host.setText("0.0.0.0")
            self.netport.setText("14550")
        else:  # TCP / SITL
            self.stack.setCurrentIndex(1)
            self.host.setText("127.0.0.1")
            self.netport.setText("5760")

    def set_ports(self, ports):
        self._ports = ports or []
        if self._embedded:
            return
        cur = self.port.currentData()
        self.port.clear()
        for p in self._ports:
            label = p["device"]
            if p.get("description"):
                label += "  —  " + p["description"]
            if p.get("likely_fc"):
                label = "✈ " + label
            self.port.addItem(label, p["device"])
        if not self._ports:
            self.port.addItem("(port bulunamadı — yenile)", None)
        # eski seçimi koru
        if cur is not None:
            i = self.port.findData(cur)
            if i >= 0:
                self.port.setCurrentIndex(i)

    def _build_target(self):
        kind = self.kind.currentText()
        if kind.startswith("Seri") or kind.startswith("Telemetri"):
            dev = self.port.currentData()
            return dev
        host = self.host.text().strip() or "127.0.0.1"
        port = self.netport.text().strip() or "5760"
        proto = "udp" if kind == "UDP" else "tcp"
        return "%s:%s:%s" % (proto, host, port)

    def _do_connect(self):
        target = self._build_target()
        if not target:
            self.status.setText("Önce bir port/adres seçin")
            self.status.setStyleSheet("font-weight:600;color:#b7950b;")
            return
        try:
            baud = int(self.baud.currentText())
        except ValueError:
            baud = 115200
        self.connectRequested.emit(target, baud)

    # ----- her iki mod: canlı durum güncelle ------------------------- #

    def set_state(self, payload):
        link = payload.get("link", {})
        connected = bool(payload.get("connected"))
        busy = payload.get("busy")
        if connected:
            rate = link.get("msg_rate")
            sysid = link.get("sysid")
            txt = "● Bağlı"
            if sysid is not None:
                txt += "  · sysid %s" % sysid
            if rate is not None:
                txt += "  · %.0f msg/s" % rate
            self.status.setText(txt)
            self.status.setStyleSheet("font-weight:600;color:#1d8348;")
        elif busy == "connect":
            self.status.setText("⟳ Bağlanılıyor…")
            self.status.setStyleSheet("font-weight:600;color:#b7950b;")
        else:
            self.status.setText("Bağlı değil")
            self.status.setStyleSheet("font-weight:600;color:#c0392b;")

        if not self._embedded:
            connecting = busy == "connect"
            self.connect_btn.setEnabled(not connected and not connecting)
            self.disconnect_btn.setEnabled(connected or connecting)
