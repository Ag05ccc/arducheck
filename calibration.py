"""Interactive calibration sessions for ArduCheck.

Supports the calibrations a ground station can drive over MAVLink while the
vehicle is disarmed on the ground:

  * gyro   — single step (MAV_CMD_PREFLIGHT_CALIBRATION param1=1)
  * level  — board level / trim (param5=2)
  * baro   — ground pressure + airspeed zero (param3=1)
  * accel  — 6-position wizard (param5=1 + MAV_CMD_ACCELCAL_VEHICLE_POS)
  * compass— onboard magnetometer cal with live progress
             (MAV_CMD_DO_START_MAG_CAL / ACCEPT / CANCEL + MAG_CAL_* msgs)

A single CalManager runs one session at a time in a worker thread. The UI
polls snapshot() and drives the flow with proceed()/cancel()/reset().
The manager never arms the vehicle.
"""

import threading
import time

# MAVLink command numbers (kept explicit for clarity/robustness)
CMD_PREFLIGHT_CALIBRATION = 241
CMD_DO_START_MAG_CAL = 42424
CMD_DO_ACCEPT_MAG_CAL = 42425
CMD_DO_CANCEL_MAG_CAL = 42426
CMD_ACCELCAL_VEHICLE_POS = 42429

# ACCELCAL_VEHICLE_POS special values
ACCEL_POS_SUCCESS = 16777215
ACCEL_POS_FAILED = 16777216

# (pos value, Turkish instruction, English STATUSTEXT keywords from the AP prompt)
ACCEL_POSITIONS = [
    (1, "Aracı tam YATAY (düz) konuma getirin.", ["level"]),
    (2, "Aracı SOL yanına yatırın.", ["left"]),
    (3, "Aracı SAĞ yanına yatırın.", ["right"]),
    (4, "Aracın BURNUNU AŞAĞI çevirin (dik).", ["nose down"]),
    (5, "Aracın BURNUNU YUKARI çevirin (dik).", ["nose up"]),
    (6, "Aracı SIRTÜSTÜ (ters) çevirin.", ["back"]),
]

MAG_STATUS = {0: "başlamadı", 1: "bekliyor", 2: "1. adım", 3: "2. adım",
              4: "başarılı", 5: "başarısız", 6: "hatalı yönelim",
              7: "yarıçap hatası"}

SIMPLE_SPECS = {
    "gyro": {"params": {"p1": 1}, "title": "Jiroskop Kalibrasyonu",
             "intro": "Aracı tamamen SABİT ve düz tutun. Kalibrasyon bitene "
                      "kadar hareket ettirmeyin.",
             "done_kw": ["gyro calibration", "gyros calibrated",
                         "calibration successful", "calibration complete"],
             "duration": 6},
    "level": {"params": {"p5": 2}, "title": "Yatay (Level) Kalibrasyonu",
              "intro": "Aracı normal uçuş pozisyonunda, tam yatay bir zemine "
                       "koyun ve sabit tutun.",
              "done_kw": ["trim ok", "level calibration", "calibrated"],
              "duration": 4},
    "baro": {"params": {"p3": 1}, "title": "Barometre / Hava Hızı Sıfırlama",
             "intro": "Pitot tüpüne rüzgar gelmediğinden emin olun "
                      "(gerekirse gevşekçe kapatın).",
             "done_kw": ["barometer", "pressure calibrat", "airspeed calibrat",
                         "calibration complete"], "duration": 4},
}


class CalManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._proceed = threading.Event()
        self._cancel = threading.Event()
        self._thread = None
        self._state = {"kind": None, "phase": "idle", "title": "",
                       "message": "", "ap_prompt": "", "progress": None,
                       "step": 0, "total_steps": 0, "can_confirm": False,
                       "confirm_label": "", "report": "", "result": None}

    # ----- public API ------------------------------------------------ #

    def is_running(self):
        t = self._thread
        return t is not None and t.is_alive()

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    def start(self, mav, kind, on_finish):
        if self.is_running():
            return False, "Bir kalibrasyon zaten sürüyor."
        if kind not in SIMPLE_SPECS and kind not in ("accel", "compass"):
            return False, "Bilinmeyen kalibrasyon türü."
        self._proceed.clear()
        self._cancel.clear()
        self._set(kind=kind, phase="running", title="", message="",
                  ap_prompt="", progress=None, step=0, total_steps=0,
                  can_confirm=False, confirm_label="", report="", result=None)
        self._thread = threading.Thread(
            target=self._run, args=(mav, kind, on_finish), daemon=True)
        self._thread.start()
        return True, None

    def proceed(self):
        self._proceed.set()

    def cancel(self):
        self._cancel.set()

    def reset(self):
        if self.is_running():
            return False
        self._set(kind=None, phase="idle", title="", message="",
                  ap_prompt="", progress=None, step=0, total_steps=0,
                  can_confirm=False, confirm_label="", report="", result=None)
        return True

    # ----- internals ------------------------------------------------- #

    def _set(self, **kw):
        with self._lock:
            self._state.update(kw)

    def _run(self, mav, kind, on_finish):
        try:
            if kind in SIMPLE_SPECS:
                self._simple(mav, kind)
            elif kind == "accel":
                self._accel(mav)
            elif kind == "compass":
                self._compass(mav)
        except Exception as exc:  # never let the worker die silently
            self._set(phase="error", result="error",
                      message="Beklenmeyen hata: %s" % exc)
        finally:
            try:
                on_finish()
            except Exception:
                pass

    def _wait_proceed(self, timeout=600):
        """Block until the user confirms (True) or cancels (False)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancel.is_set():
                return False
            if self._proceed.is_set():
                self._proceed.clear()
                return True
            time.sleep(0.1)
        return False

    def _scan(self, mav, since, want_kw, timeout=30):
        """Watch STATUSTEXT after `since`. Returns:
        ('match', text) when a want keyword appears,
        ('fail', text) on a failure message,
        ('cancel', None) if the user cancelled,
        ('timeout', None) otherwise."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._cancel.is_set():
                return "cancel", None
            for (_, sev, text, _n) in mav.recent_statustexts(since=since):
                low = text.lower()
                if any(k in low for k in want_kw):
                    return "match", text
                if "fail" in low and ("cal" in low or "accel" in low):
                    return "fail", text
            time.sleep(0.2)
        return "timeout", None

    # ----- simple (gyro / level / baro) ------------------------------ #

    def _simple(self, mav, kind):
        spec = SIMPLE_SPECS[kind]
        self._set(title=spec["title"], message=spec["intro"], total_steps=1)
        start = time.time()
        acked, result = mav.run_command(CMD_PREFLIGHT_CALIBRATION,
                                        ack_timeout=5, **spec["params"])
        if result in (2, 4):  # DENIED / FAILED
            self._set(phase="failed", result="failed",
                      message="Otopilot kalibrasyonu reddetti "
                              "(sonuç kodu %s). Araç disarm mı?" % result)
            return
        if result == 3:  # UNSUPPORTED
            self._set(phase="failed", result="failed",
                      message="Bu kalibrasyon bu firmware'de desteklenmiyor.")
            return
        if not acked or result is None:
            self._set(phase="failed", result="failed",
                      message="Otopilot komutu yanıtlamadı (ACK yok). "
                              "Bağlantıyı ve aracın disarm olduğunu kontrol "
                              "edip tekrar deneyin.")
            return
        # watch for completion / failure text (timeout -> treat as success,
        # because a completed simple cal does not always emit a final text)
        outcome, text = self._scan(mav, start, want_kw=spec["done_kw"],
                                   timeout=spec["duration"])
        if outcome == "cancel":
            self._set(phase="cancelled", result="cancelled",
                      message="İptal edildi.")
        elif outcome == "fail":
            self._set(phase="failed", result="failed",
                      message="Kalibrasyon başarısız: " + text)
        else:
            # ACCEPTED with no failure -> treat as success
            self._set(phase="success", result="success",
                      message="Kalibrasyon tamamlandı.",
                      report=text if outcome == "match" else "")

    # ----- accelerometer 6-position ---------------------------------- #

    def _accel(self, mav):
        """6-position accel cal. The autopilot drives the sequence: it sends
        MAV_CMD_ACCELCAL_VEHICLE_POS(param1=position) ~1 Hz to request each
        pose; we display it, wait for the operator, then send the same command
        back to confirm. (After the first reply ArduPilot stops emitting the
        STATUSTEXT prompts, so we rely on these requests, not on text.)"""
        instr_by_pos = {p: instr for (p, instr, _) in ACCEL_POSITIONS}
        self._set(title="İvmeölçer Kalibrasyonu (6 pozisyon)", total_steps=6,
                  step=0, can_confirm=False,
                  message="Kalibrasyon başlatılıyor...")
        mav.clear_accel_pos_request()
        start = time.time()
        acked, result = mav.run_command(CMD_PREFLIGHT_CALIBRATION, p5=1,
                                        ack_timeout=3)
        if result in (2, 4):
            self._set(phase="failed", result="failed",
                      message="Otopilot kalibrasyonu reddetti. Araç disarm mı?")
            return

        shown = None        # position currently displayed, awaiting operator
        collecting = None   # position confirmed, autopilot is sampling
        last_send = 0.0
        last_seen = time.time()   # last time a NEW request was seen
        last_req = (None, 0.0)    # last (pos, timestamp) observed
        while True:
            if self._cancel.is_set():
                mav.send_command(CMD_ACCELCAL_VEHICLE_POS, p1=ACCEL_POS_FAILED)
                self._set(phase="cancelled", result="cancelled",
                          message="İptal edildi.")
                return
            pos, ts = mav.accel_pos_request(since=start)
            # advance the stall timer only on a genuinely new request, so a
            # silent autopilot (same stale request) trips the 30s timeout
            if pos is None or (pos, ts) == last_req:
                if time.time() - last_seen > 30:
                    self._set(phase="failed", result="failed",
                              message="Otopilot pozisyon istemi göndermedi "
                                      "(zaman aşımı). Bağlantıyı kontrol edin.")
                    return
                if pos is None:
                    time.sleep(0.2)
                    continue
            else:
                last_req = (pos, ts)
                last_seen = time.time()

            if pos == ACCEL_POS_SUCCESS:
                texts = [s[2] for s in mav.recent_statustexts(since=start)
                         if "success" in s[2].lower() or "calibrated" in s[2].lower()]
                self._set(phase="success", result="success", step=6,
                          message="İvmeölçer kalibrasyonu başarılı. Değerlerin "
                                  "etkili olması için otopilotu yeniden "
                                  "başlatın.",
                          report=texts[-1] if texts else "")
                return
            if pos == ACCEL_POS_FAILED:
                self._set(phase="failed", result="failed",
                          message="İvmeölçer kalibrasyonu başarısız. Tekrar "
                                  "deneyin.")
                return
            if not (1 <= pos <= 6):
                time.sleep(0.2)
                continue

            if collecting == pos:
                # autopilot still on this pose: re-send confirm in case the
                # previous one was lost, until it advances
                if time.time() - last_send > 1.5:
                    mav.send_command(CMD_ACCELCAL_VEHICLE_POS, p1=pos)
                    last_send = time.time()
                self._set(phase="running", step=pos, can_confirm=False,
                          message="Örnek toplanıyor — aracı bu pozisyonda "
                                  "SABİT tutun...")
            else:
                if shown != pos:
                    shown = pos
                    # discard any stale/double-click confirm so the operator
                    # must explicitly confirm THIS position
                    self._proceed.clear()
                    self._set(phase="waiting", step=pos, can_confirm=True,
                              confirm_label="Bu pozisyondayım", ap_prompt="",
                              message=instr_by_pos.get(pos, "Pozisyon %d" % pos))
                if self._proceed.is_set():
                    self._proceed.clear()
                    mav.send_command(CMD_ACCELCAL_VEHICLE_POS, p1=pos)
                    last_send = time.time()
                    collecting = pos
                    shown = None
                    self._set(phase="running", step=pos, can_confirm=False,
                              message="Örnek toplanıyor — aracı SABİT tutun...")
            time.sleep(0.2)

    # ----- compass (onboard mag cal) --------------------------------- #

    def _compass(self, mav):
        self._set(title="Pusula Kalibrasyonu",
                  message="Aracı tüm eksenlerde (her yöne doğru burun) "
                          "yavaşça çevirin. İlerleme aşağıda gösterilir.",
                  progress=0)
        start = time.time()
        # p1=0 all compasses, p2=0 no retry, p3=0 no autosave (we Accept),
        # p4=0 delay, p5=0 no autoreboot
        acked, result = mav.run_command(CMD_DO_START_MAG_CAL, p1=0, p2=0, p3=0,
                                        ack_timeout=3)
        if result in (2, 3, 4):
            self._set(phase="failed", result="failed",
                      message="Pusula kalibrasyonu başlatılamadı "
                              "(sonuç kodu %s)." % result)
            return

        last_activity = time.time()
        while True:
            if self._cancel.is_set():
                mav.send_command(CMD_DO_CANCEL_MAG_CAL)
                self._set(phase="cancelled", result="cancelled",
                          message="İptal edildi.")
                return
            # inactivity timeout: a live cal keeps emitting progress; a stalled
            # one (link drop, firmware abort) must not hang the worker forever
            if time.time() - last_activity > 90:
                mav.send_command(CMD_DO_CANCEL_MAG_CAL)
                self._set(phase="failed", result="failed",
                          message="Pusula kalibrasyonu zaman aşımına uğradı "
                                  "(otopilot ilerleme göndermeyi durdurdu). "
                                  "Bağlantıyı kontrol edip tekrar deneyin.")
                return
            prog = mav.get_msg_after("MAG_CAL_PROGRESS", start)
            if prog is not None:
                self._set(progress=int(prog.completion_pct))
                # a recently-arrived progress message proves the link/AP is
                # alive (even at 0% while the operator is still rotating)
                age = mav.msg_age("MAG_CAL_PROGRESS")
                if age is not None and age < 3.0:
                    last_activity = time.time()
            rep = mav.get_msg_after("MAG_CAL_REPORT", start)
            if rep is not None:
                status = rep.cal_status
                if status == 4:  # SUCCESS
                    ofs = (rep.ofs_x, rep.ofs_y, rep.ofs_z)
                    report = ("Başarılı. Uyum (fitness): %.1f, ofset: "
                              "%.0f / %.0f / %.0f" % (rep.fitness, *ofs))
                    self._set(phase="accept", progress=100, result=None,
                              can_confirm=True, confirm_label="Kaydet",
                              message="Kalibrasyon tamamlandı. Sonucu "
                                      "kaydetmek ister misiniz?", report=report)
                    break
                if status in (5, 6, 7):
                    self._set(phase="failed", result="failed",
                              message="Pusula kalibrasyonu başarısız (%s). "
                                      "Tekrar deneyin." % MAG_STATUS.get(status))
                    return
            time.sleep(0.3)

        # success -> wait for Accept / Cancel
        if self._wait_proceed():
            mav.send_command(CMD_DO_ACCEPT_MAG_CAL)
            with self._lock:
                rep_txt = self._state.get("report", "")
            self._set(phase="success", result="success", can_confirm=False,
                      message="Kalibrasyon kaydedildi. Değerlerin etkili "
                              "olması için otopilotu YENİDEN BAŞLATIN.",
                      report=rep_txt)
        else:
            mav.send_command(CMD_DO_CANCEL_MAG_CAL)
            self._set(phase="cancelled", result="cancelled", can_confirm=False,
                      message="Kaydedilmedi, iptal edildi.")
