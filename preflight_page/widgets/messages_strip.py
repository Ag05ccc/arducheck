"""Otopilot mesaj şeridi (STATUSTEXT) — önem rengine göre listelenir."""

import time

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                             QPushButton, QVBoxLayout, QWidget)

from ..style import SEV_COLOR


class MessagesView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        head = QHBoxLayout()
        title = QLabel("Otopilot Mesajları")
        title.setStyleSheet("font-weight:600;")
        clear = QPushButton("Temizle")
        clear.setFixedWidth(72)
        clear.clicked.connect(self.clear)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(clear)
        lay.addLayout(head)

        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget{font-family:monospace;font-size:12px;}")
        lay.addWidget(self.list)

    def clear(self):
        self.list.clear()

    def append_messages(self, msgs):
        at_bottom = (self.list.verticalScrollBar().value()
                     >= self.list.verticalScrollBar().maximum() - 4)
        for m in msgs:
            ts = time.strftime("%H:%M:%S", time.localtime(m["t"]))
            count = m.get("count", 1)
            suffix = (" ×%d" % count) if count and count > 1 else ""
            text = "%s  [%s] %s%s" % (ts, m.get("sev_name", "?"), m["text"], suffix)
            item = QListWidgetItem(text)
            item.setForeground(QColor(SEV_COLOR.get(m.get("sev", 6), "#566573")))
            if m.get("sev", 6) <= 3:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self.list.addItem(item)
        # çok büyümesin
        while self.list.count() > 500:
            self.list.takeItem(0)
        if at_bottom:
            self.list.scrollToBottom()
