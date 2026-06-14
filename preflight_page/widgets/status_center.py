"""Durum merkezi — karar bandı, sayımlar, öncelikli sorun kartları ve gruplu
kontrol ağacı. Web arayüzündeki 'her şey tamam mı?' merkezinin Qt karşılığı.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea,
                             QTabWidget, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)
from PyQt5.QtGui import QColor

from ..style import STATUS_COLOR, STATUS_TR, chip


class StatusView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.verdict = QLabel("Henüz kontrol çalıştırılmadı")
        self.verdict.setAlignment(Qt.AlignCenter)
        self.verdict.setMinimumHeight(54)
        self.verdict.setStyleSheet(
            "background:#7f8c8d;color:#fff;border-radius:8px;"
            "font-size:18px;font-weight:800;padding:8px;")
        root.addWidget(self.verdict)

        self.counts = QLabel("")
        self.counts.setAlignment(Qt.AlignCenter)
        self.counts.setTextFormat(Qt.RichText)
        root.addWidget(self.counts)

        tabs = QTabWidget()
        # --- öncelikli sorunlar ---
        self.problem_host = QWidget()
        self.problem_lay = QVBoxLayout(self.problem_host)
        self.problem_lay.setContentsMargins(2, 2, 2, 2)
        self.problem_lay.setSpacing(6)
        self.problem_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.problem_host)
        tabs.addTab(scroll, "Sorunlar")

        # --- tüm kontroller (gruplu ağaç) ---
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Kontrol", "Sonuç", "Açıklama"])
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setStretchLastSection(True)
        tabs.addTab(self.tree, "Tüm Kontroller")
        root.addWidget(tabs, 1)

        self._placeholder()

    # ------------------------------------------------------------------ #
    def _placeholder(self):
        lbl = QLabel("Bağlanıp “▶ Tüm Kontrolleri Çalıştır”a basın.")
        lbl.setStyleSheet("color:#7f8c8d;padding:16px;")
        lbl.setAlignment(Qt.AlignCenter)
        self.problem_lay.insertWidget(0, lbl)

    def _clear_problems(self):
        while self.problem_lay.count() > 1:   # son stretch kalsın
            item = self.problem_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    @staticmethod
    def _problem_card(p):
        card = QFrame()
        color = STATUS_COLOR.get(p["status"], "#7f8c8d")
        card.setStyleSheet(
            "QFrame{background:#fff;border:1px solid #d5d8dc;border-left:5px "
            "solid %s;border-radius:6px;}" % color)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 7, 10, 8)
        lay.setSpacing(3)
        head = QHBoxLayout()
        head.addWidget(chip(STATUS_TR.get(p["status"], p["status"]), color))
        name = QLabel("<b>%s</b>" % p["name"])
        name.setTextFormat(Qt.RichText)
        head.addWidget(name)
        grp = QLabel(p.get("group", ""))
        grp.setStyleSheet("color:#7f8c8d;font-size:11px;")
        head.addStretch(1)
        head.addWidget(grp)
        lay.addLayout(head)
        detail = QLabel(p.get("detail", ""))
        detail.setWordWrap(True)
        detail.setStyleSheet("color:#1c2833;")
        lay.addWidget(detail)
        remedy = p.get("remedy")
        if remedy:
            r = QLabel("<b style='color:#1d8348'>Çözüm:</b> " + remedy)
            r.setTextFormat(Qt.RichText)
            r.setWordWrap(True)
            lay.addWidget(r)
        return card

    # ------------------------------------------------------------------ #
    def set_results(self, results, manual_done=0, manual_total=0):
        if not results:
            return
        if results.get("error"):
            self.verdict.setText("Kontroller başarısız: %s" % results["error"])
            self.verdict.setStyleSheet(
                "background:#c0392b;color:#fff;border-radius:8px;"
                "font-size:16px;font-weight:800;padding:8px;")
            return

        counts = results.get("counts", {})
        verdict = results.get("verdict", "FAIL")
        nfail = counts.get("FAIL", 0)
        nwarn = counts.get("WARN", 0)
        ready = (verdict == "PASS" and (manual_total == 0
                                        or manual_done == manual_total))
        if ready:
            text, bg = "✔ HER ŞEY TAMAM — uçuşa hazır", STATUS_COLOR["PASS"]
        elif verdict == "FAIL":
            text, bg = "✘ %d SORUN ÇÖZÜLMELİ" % nfail, STATUS_COLOR["FAIL"]
        else:
            bits = []
            if nwarn:
                bits.append("%d uyarı" % nwarn)
            missing = manual_total - manual_done
            if missing > 0:
                bits.append("%d manuel madde" % missing)
            text = "⚠ KOŞULLU — " + (", ".join(bits) or "gözden geçirin")
            bg = STATUS_COLOR["WARN"]
        self.verdict.setText(text)
        self.verdict.setStyleSheet(
            "background:%s;color:#fff;border-radius:8px;font-size:18px;"
            "font-weight:800;padding:8px;" % bg)

        # sayımlar
        parts = []
        for key, label in (("PASS", "Geçti"), ("WARN", "Uyarı"),
                           ("FAIL", "Hata"), ("INFO", "Bilgi"),
                           ("SKIP", "Atlandı")):
            parts.append("<span style='color:%s;font-weight:700'>%d</span> %s"
                         % (STATUS_COLOR[key], counts.get(key, 0), label))
        if manual_total:
            parts.append("<b>%d/%d</b> manuel" % (manual_done, manual_total))
        self.counts.setText(" &nbsp;·&nbsp; ".join(parts))

        # sorun kartları
        self._clear_problems()
        problems = results.get("problems", [])
        if not problems:
            ok = QLabel("Çözülmesi gereken bir sorun yok. 🎉")
            ok.setStyleSheet("color:#1d8348;font-weight:600;padding:12px;")
            ok.setAlignment(Qt.AlignCenter)
            self.problem_lay.insertWidget(0, ok)
        else:
            for i, p in enumerate(problems):
                self.problem_lay.insertWidget(i, self._problem_card(p))

        # gruplu ağaç
        self.tree.clear()
        for g in results.get("groups", []):
            gi = QTreeWidgetItem([g["name"], "", ""])
            f = gi.font(0)
            f.setBold(True)
            gi.setFont(0, f)
            self.tree.addTopLevelItem(gi)
            for c in g["checks"]:
                ci = QTreeWidgetItem([c["name"],
                                      STATUS_TR.get(c["status"], c["status"]),
                                      c.get("detail", "")])
                ci.setForeground(1, QColor(STATUS_COLOR.get(c["status"],
                                                            "#7f8c8d")))
                f2 = ci.font(1)
                f2.setBold(True)
                ci.setFont(1, f2)
                gi.addChild(ci)
            gi.setExpanded(True)
        self.tree.resizeColumnToContents(0)
        self.tree.resizeColumnToContents(1)
