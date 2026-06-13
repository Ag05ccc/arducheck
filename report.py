"""Standalone HTML report generation (printable, self-contained)."""

import html
import time

from checklist_def import MANUAL_ITEMS

STATUS_TR = {"PASS": "GEÇTİ", "WARN": "UYARI", "FAIL": "HATA",
             "INFO": "BİLGİ", "SKIP": "ATLANDI"}
STATUS_COLOR = {"PASS": "#1d8348", "WARN": "#b7950b", "FAIL": "#c0392b",
                "INFO": "#2471a3", "SKIP": "#7f8c8d"}

CSS = """
body{font-family:'Segoe UI',Arial,sans-serif;margin:24px auto;max-width:900px;
     color:#1c2833;line-height:1.45}
h1{font-size:1.5em;border-bottom:3px solid #1c2833;padding-bottom:6px}
h2{font-size:1.1em;margin:22px 0 6px;color:#17202a}
table{width:100%;border-collapse:collapse;margin:4px 0 14px}
td,th{border:1px solid #d5d8dc;padding:5px 8px;font-size:.92em;text-align:left}
th{background:#ebedef}
.badge{display:inline-block;padding:1px 9px;border-radius:9px;color:#fff;
       font-weight:600;font-size:.85em}
.summary{display:flex;gap:14px;margin:14px 0;flex-wrap:wrap}
.sumbox{border:1px solid #d5d8dc;border-radius:8px;padding:10px 18px;
        text-align:center;min-width:90px}
.sumbox b{font-size:1.5em;display:block}
.verdict{font-size:1.2em;font-weight:700;padding:12px 16px;border-radius:8px;
         color:#fff;margin:12px 0}
.footer{margin-top:26px;font-size:.8em;color:#7f8c8d;border-top:1px solid
        #d5d8dc;padding-top:8px}
@media print{.noprint{display:none}}
"""


def _esc(s):
    return html.escape(str(s if s is not None else ""))


def render(results, manual_state, telemetry):
    counts = results.get("counts", {})
    verdict = results.get("verdict", "FAIL")
    vehicle = results.get("vehicle", {})
    ts = time.strftime("%d.%m.%Y %H:%M:%S",
                       time.localtime(results.get("time", time.time())))

    manual_total = sum(len(s["items"]) for s in MANUAL_ITEMS)
    manual_done = sum(1 for s in MANUAL_ITEMS for i in s["items"]
                      if manual_state.get(i["id"]))
    overall_ready = (verdict == "PASS" and manual_done == manual_total)

    parts = ["<!DOCTYPE html><html lang='tr'><head><meta charset='utf-8'>",
             "<title>ArduCheck Uçuş Öncesi Kontrol Raporu</title>",
             "<style>", CSS, "</style></head><body>",
             "<h1>✈ ArduCheck — Uçuş Öncesi Kontrol Raporu</h1>",
             "<p><b>Tarih:</b> %s &nbsp; <b>Araç:</b> sysid %s &nbsp; "
             "<b>Firmware:</b> %s &nbsp; <b>Mod:</b> %s</p>"
             % (_esc(ts), _esc(vehicle.get("sysid", "?")),
                _esc(vehicle.get("fw_version", "?")),
                _esc(vehicle.get("mode", "?")))]

    if overall_ready:
        parts.append("<div class='verdict' style='background:#1d8348'>"
                     "✔ UÇUŞA HAZIR — otomatik kontroller geçti, manuel liste "
                     "tamamlandı.</div>")
    elif verdict == "FAIL":
        parts.append("<div class='verdict' style='background:#c0392b'>"
                     "✘ UÇUŞA HAZIR DEĞİL — kritik hatalar var.</div>")
    else:
        missing = manual_total - manual_done
        msg = []
        if verdict == "WARN":
            msg.append("uyarılar gözden geçirilmeli")
        if missing:
            msg.append("%d manuel madde eksik" % missing)
        parts.append("<div class='verdict' style='background:#b7950b'>"
                     "⚠ KOŞULLU — %s.</div>" % _esc(", ".join(msg)))

    parts.append("<div class='summary'>")
    for key, label in (("PASS", "Geçti"), ("WARN", "Uyarı"), ("FAIL", "Hata"),
                       ("INFO", "Bilgi"), ("SKIP", "Atlandı")):
        parts.append("<div class='sumbox'><b style='color:%s'>%d</b>%s</div>"
                     % (STATUS_COLOR[key], counts.get(key, 0), label))
    parts.append("<div class='sumbox'><b>%d/%d</b>Manuel liste</div></div>"
                 % (manual_done, manual_total))

    for group in results.get("groups", []):
        parts.append("<h2>%s</h2><table><tr><th style='width:32%%'>Kontrol</th>"
                     "<th style='width:12%%'>Sonuç</th><th>Açıklama</th></tr>"
                     % _esc(group["name"]))
        for c in group["checks"]:
            color = STATUS_COLOR.get(c["status"], "#7f8c8d")
            detail = _esc(c["detail"])
            remedy = c.get("remedy")
            if remedy and c["status"] in ("FAIL", "WARN"):
                detail += ("<br><b style='color:#1d8348'>Çözüm:</b> "
                           + _esc(remedy))
            parts.append(
                "<tr><td>%s</td><td><span class='badge' style='background:%s'>"
                "%s</span></td><td>%s</td></tr>"
                % (_esc(c["name"]), color,
                   STATUS_TR.get(c["status"], c["status"]), detail))
        parts.append("</table>")

    parts.append("<h2>Manuel Kontrol Listesi</h2>")
    for section in MANUAL_ITEMS:
        parts.append("<h2 style='font-size:.95em;color:#566573'>%s</h2>"
                     "<table>" % _esc(section["section"]))
        for item in section["items"]:
            done = manual_state.get(item["id"])
            mark = ("<span class='badge' style='background:#1d8348'>✔</span>"
                    if done else
                    "<span class='badge' style='background:#c0392b'>—</span>")
            parts.append("<tr><td style='width:8%%'>%s</td><td>%s</td></tr>"
                         % (mark, _esc(item["text"])))
        parts.append("</table>")

    parts.append(
        "<div class='footer'>Bu rapor ArduCheck tarafından üretildi. "
        "Sınırlamalar: telemetri kontrolleri tek kanal RC arızalarını, donanım "
        "kusurlarını, motor arızasını, ters bağlı kontrol yüzeylerini veya "
        "pitot kapağının fiziksel durumunu TESPİT EDEMEZ — bunlar manuel "
        "listededir. Nihai uçuş kararı ve sorumluluk pilota aittir.</div>"
        "</body></html>")
    return "".join(parts)
