"""MAVLink connection management and telemetry collection for ArduCheck.

Single background reader thread keeps the latest copy of every message type,
short histories for noisy signals (vibration, GPS), the full parameter table
and a STATUSTEXT ring buffer. All public methods are thread-safe.
"""

import threading
import time
import collections

from pymavlink import mavutil

# Message types for which we keep a rolling history (value extraction below)
HISTORY_SECONDS = 30.0

STATUSTEXT_KEEP = 300

SEVERITY_NAMES = {
    0: "EMERGENCY", 1: "ALARM", 2: "CRITICAL", 3: "ERROR",
    4: "WARNING", 5: "NOTICE", 6: "INFO", 7: "DEBUG",
}

# Streams we explicitly request from the autopilot
EXTRA_MESSAGE_INTERVALS = {
    # msg id: interval in microseconds
    193: 500000,   # EKF_STATUS_REPORT 2 Hz
    241: 500000,   # VIBRATION 2 Hz
    147: 500000,   # BATTERY_STATUS 2 Hz
    125: 1000000,  # POWER_STATUS 1 Hz
    22: 0,         # placeholder, unused
}


class MavClient:
    def __init__(self):
        self._lock = threading.RLock()
        self._conn = None
        self._thread = None
        self._stop = threading.Event()

        self.connected = False
        self.connect_error = None
        self.target = None            # connection string used
        self.sysid = None
        self.compid = None

        self.messages = {}            # type -> (msg, recv_time)
        self.history = collections.defaultdict(collections.deque)  # type -> deque[(t, msg)]
        self.statustexts = collections.deque(maxlen=STATUSTEXT_KEEP)  # (t, severity, text, count)
        self.params = {}              # name -> float
        self.param_count = None
        self.param_fetch_started = None
        self.param_fetch_done = False
        self._param_indexes = {}      # index -> name (for gap detection)

        self.autopilot_version = None # AUTOPILOT_VERSION msg
        self.last_heartbeat = 0.0
        self.heartbeat_count = 0
        self.msg_rate = 0.0
        self._msg_times = collections.deque(maxlen=200)

        self._ack_events = {}         # command id -> [(event, [result])]
        self._statustext_chunks = {}  # (sysid, id) -> {t, sev, parts:{seq: text}, late}
        self._flushed_chunks = {}     # (sysid, id) -> kesik yayınlanma zamanı
        self._accel_pos_req = None    # (position, time) from AP during accel cal

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    def connect(self, target, baud=115200):
        """Open a MAVLink connection. target examples:
        'tcp:127.0.0.1:5760', 'udp:0.0.0.0:14550', '/dev/ttyACM0', 'COM3'.
        Returns (ok, error_message)."""
        self.disconnect()
        self._stop.clear()
        with self._lock:
            self.connect_error = None
            self.target = target
        try:
            conn = mavutil.mavlink_connection(
                target, baud=baud, source_system=255, source_component=190,
                autoreconnect=True)
        except Exception as exc:
            with self._lock:
                self.connect_error = str(exc)
            return False, str(exc)

        with self._lock:
            self._conn = conn
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

        # Wait for an autopilot heartbeat (not another GCS) up to 12 s
        deadline = time.time() + 12.0
        while time.time() < deadline:
            if self._stop.is_set():
                return False, self.connect_error or "Bağlantı kapandı"
            with self._lock:
                if self.connected:
                    return True, None
                if self.connect_error:
                    return False, self.connect_error
            time.sleep(0.1)
        self.disconnect()
        return False, ("Kalp atışı (heartbeat) alınamadı. Aracın açık ve "
                       "bağlantı ayarlarının doğru olduğundan emin olun.")

    def disconnect(self):
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None
        with self._lock:
            if self._conn:
                try:
                    self._conn.close()
                except Exception:
                    pass
            self._conn = None
            self.connected = False
            self.sysid = None
            self.compid = None
            self.messages = {}
            self.history = collections.defaultdict(collections.deque)
            self.params = {}
            self.param_count = None
            self.param_fetch_done = False
            self.param_fetch_started = None
            self._param_indexes = {}
            self.autopilot_version = None
            self.heartbeat_count = 0
            self.last_heartbeat = 0.0
            # statustexts bilerek KORUNUR: kopmayı açıklayan mesajlar ve tam
            # oturum geçmişi kaybolmasın (istemci artımlı biriktirir).
            # Bekleyen yarım chunk'lar silinmeden önce kesik olarak yayınlanır.
            self._flush_stale_chunks(time.time(), max_age=0.0)
            self._statustext_chunks = {}
            self._flushed_chunks = {}
            self._accel_pos_req = None
            self._ack_events = {}

    # ------------------------------------------------------------------ #
    # Reader thread
    # ------------------------------------------------------------------ #

    def _reader(self):
        while not self._stop.is_set():
            conn = self._conn
            if conn is None:
                return
            try:
                msg = conn.recv_match(blocking=True, timeout=1.0)
            except Exception as exc:
                with self._lock:
                    if not self.connected:
                        self.connect_error = str(exc)
                    self.connected = False
                time.sleep(0.5)
                continue
            if msg is None:
                with self._lock:
                    # sessiz linkte bekleyen yarım chunk'ları da yaşlandır
                    self._flush_stale_chunks(time.time())
                    if self.connected and time.time() - self.last_heartbeat > 5.0:
                        self.connected = False  # link stale; reader keeps trying
                continue
            self._handle(msg)

    def _handle(self, msg):
        mtype = msg.get_type()
        if mtype == "BAD_DATA":
            return
        now = time.time()
        with self._lock:
            self._msg_times.append(now)

        if mtype == "HEARTBEAT":
            # Ignore other ground stations sharing the link
            if msg.autopilot == mavutil.mavlink.MAV_AUTOPILOT_INVALID:
                return
            with self._lock:
                first = not self.connected
                self.connected = True
                self.last_heartbeat = now
                self.heartbeat_count += 1
                self.sysid = msg.get_srcSystem()
                self.compid = msg.get_srcComponent()
                conn = self._conn
                if conn is not None:
                    conn.target_system = self.sysid
                    conn.target_component = self.compid
            if first:
                self._on_first_heartbeat()

        with self._lock:
            self.messages[mtype] = (msg, now)
            hist = self.history[mtype]
            hist.append((now, msg))
            cutoff = now - HISTORY_SECONDS
            while hist and hist[0][0] < cutoff:
                hist.popleft()

        if mtype == "STATUSTEXT":
            self._handle_statustext(msg, now)
        elif mtype == "PARAM_VALUE":
            with self._lock:
                name = msg.param_id if isinstance(msg.param_id, str) \
                    else msg.param_id.decode("ascii", "ignore")
                name = name.rstrip("\x00")
                self.params[name] = msg.param_value
                if msg.param_count not in (0, 65535):
                    self.param_count = msg.param_count
                if msg.param_index != 65535:
                    self._param_indexes[msg.param_index] = name
                if self.param_count and len(self._param_indexes) >= self.param_count:
                    self.param_fetch_done = True
        elif mtype == "AUTOPILOT_VERSION":
            with self._lock:
                self.autopilot_version = msg
        elif mtype == "COMMAND_ACK":
            with self._lock:
                waiters = self._ack_events.get(msg.command)
                pending = waiters.pop(0) if waiters else None
            if pending:
                event, box = pending
                box.append(msg.result)
                event.set()
        elif mtype == "COMMAND_LONG":
            # During accel cal the autopilot requests each position by sending
            # MAV_CMD_ACCELCAL_VEHICLE_POS (42429) with param1 = position.
            if msg.command == 42429:
                with self._lock:
                    self._accel_pos_req = (int(msg.param1), now)

    def _handle_statustext(self, msg, now):
        text = msg.text if isinstance(msg.text, str) else msg.text.decode("ascii", "ignore")
        text = text.rstrip("\x00")
        # tek kilit altında: flush + ekleme atomik olsun ki aynı t'li iki
        # girdinin arasına poll girip msgs_since ile mesaj düşürmesin
        with self._lock:
            self._flush_stale_chunks(now)
            # MAVLink2 chunked statustext: id != 0 means multi-part
            msg_id = getattr(msg, "id", 0)
            chunk_seq = getattr(msg, "chunk_seq", 0)
            if msg_id:
                key = (msg.get_srcSystem(), msg_id)
                # grup az önce kesik yayınlandıysa bu geç kalan kuyruk parçası
                late = now - self._flushed_chunks.get(key, 0) < 5.0
                entry = self._statustext_chunks.setdefault(
                    key, {"t": now, "sev": msg.severity, "parts": {},
                          "late": late})
                entry["parts"][chunk_seq] = text
                entry["t"] = now
                if len(text) == 50:   # full chunk -> more may follow
                    return
                text = "".join(entry["parts"][i] for i in sorted(entry["parts"]))
                self._statustext_chunks.pop(key, None)
                if entry.get("late"):
                    text = "[…kesik] " + text
            self._append_statustext(now, msg.severity, text)

    def _flush_stale_chunks(self, now, max_age=2.0):
        """Kayıplı linkte sonlandırıcı parçası düşen çok parçalı mesajları
        sonsuza dek bekletme — kesik haliyle yayınla."""
        self._flushed_chunks = {k: v for k, v in self._flushed_chunks.items()
                                if now - v < 5.0}
        for key in list(self._statustext_chunks):
            entry = self._statustext_chunks[key]
            if now - entry["t"] > max_age:
                partial = "".join(entry["parts"][i]
                                  for i in sorted(entry["parts"]))
                self._statustext_chunks.pop(key, None)
                self._flushed_chunks[key] = now
                if partial:
                    if entry.get("late"):
                        partial = "[…kesik] " + partial
                    self._append_statustext(now, entry["sev"],
                                            partial + " […kesik]")

    def _append_statustext(self, t, sev, text):
        with self._lock:
            # ardışık tekrarları tek girdide birleştir (×N) — ArduPilot aynı
            # PreArm mesajını ~30 sn'de bir yeniden yayınlar
            if self.statustexts:
                _lt, lsev, ltext, lcount = self.statustexts[-1]
                if lsev == sev and ltext == text:
                    self.statustexts[-1] = (t, lsev, ltext, lcount + 1)
                    return
            self.statustexts.append((t, sev, text, 1))

    def _on_first_heartbeat(self):
        """Request streams and firmware version once we know the vehicle."""
        conn = self._conn
        if conn is None:
            return
        try:
            for stream_id in (mavutil.mavlink.MAV_DATA_STREAM_RAW_SENSORS,
                              mavutil.mavlink.MAV_DATA_STREAM_EXTENDED_STATUS,
                              mavutil.mavlink.MAV_DATA_STREAM_RC_CHANNELS,
                              mavutil.mavlink.MAV_DATA_STREAM_POSITION,
                              mavutil.mavlink.MAV_DATA_STREAM_EXTRA1,
                              mavutil.mavlink.MAV_DATA_STREAM_EXTRA2,
                              mavutil.mavlink.MAV_DATA_STREAM_EXTRA3):
                conn.mav.request_data_stream_send(
                    self.sysid, self.compid, stream_id, 4, 1)
            for msg_id, interval in EXTRA_MESSAGE_INTERVALS.items():
                if interval:
                    conn.mav.command_long_send(
                        self.sysid, self.compid,
                        mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                        msg_id, interval, 0, 0, 0, 0, 0)
            # AUTOPILOT_VERSION (msg 148) via MAV_CMD_REQUEST_MESSAGE
            conn.mav.command_long_send(
                self.sysid, self.compid,
                mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE, 0,
                148, 0, 0, 0, 0, 0, 0)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Parameters
    # ------------------------------------------------------------------ #

    def fetch_params(self, timeout=90.0):
        """Request the full parameter list; fill gaps with indexed reads.
        Blocking; call from a worker thread. Returns True when complete.
        Already-known values are kept (not wiped) so a stalled re-fetch can
        never leave the table with fewer params than before."""
        with self._lock:
            conn = self._conn
            sysid, compid = self.sysid, self.compid
            if conn is None or sysid is None or not self.connected:
                return False
            self._param_indexes = {}
            self.param_count = None
            self.param_fetch_done = False
            self.param_fetch_started = time.time()
        conn.mav.param_request_list_send(sysid, compid)

        deadline = time.time() + timeout
        last_progress = (0, time.time())
        while time.time() < deadline and not self._stop.is_set():
            with self._lock:
                done = self.param_fetch_done
                got = len(self._param_indexes)
                total = self.param_count
            if done:
                return True
            if got != last_progress[0]:
                last_progress = (got, time.time())
            elif time.time() - last_progress[1] > 3.0 and total:
                # Stalled: request the missing indexes individually
                with self._lock:
                    if self._stop.is_set() or self.sysid is None:
                        return False
                    missing = [i for i in range(total)
                               if i not in self._param_indexes]
                for idx in missing[:50]:
                    if self._stop.is_set():
                        return False
                    conn.mav.param_request_read_send(sysid, compid, b"", idx)
                last_progress = (got, time.time())
            time.sleep(0.2)
        with self._lock:
            return self.param_fetch_done

    def param_progress(self):
        with self._lock:
            if self.param_fetch_started is None:
                return None
            return {
                "got": len(self._param_indexes) or len(self.params),
                "total": self.param_count,
                "done": self.param_fetch_done,
            }

    def get_param(self, name, default=None):
        with self._lock:
            return self.params.get(name, default)

    def params_snapshot(self):
        """Thread-safe copy of the parameter table."""
        with self._lock:
            return dict(self.params)

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #

    def run_command(self, command, p1=0, p2=0, p3=0, p4=0, p5=0, p6=0, p7=0,
                    ack_timeout=5.0):
        """Send COMMAND_LONG and wait for COMMAND_ACK.
        Returns (acked, result_code)."""
        with self._lock:
            conn = self._conn
            sysid, compid = self.sysid, self.compid
            if conn is None or sysid is None or not self.connected:
                return False, None
            event = threading.Event()
            box = []
            entry = (event, box)
            self._ack_events.setdefault(command, []).append(entry)
        try:
            conn.mav.command_long_send(
                sysid, compid, command, 0, p1, p2, p3, p4, p5, p6, p7)
            acked = event.wait(ack_timeout)
            return acked, (box[0] if box else None)
        finally:
            with self._lock:
                waiters = self._ack_events.get(command)
                if waiters:
                    try:
                        waiters.remove(entry)
                    except ValueError:
                        pass
                    if not waiters:
                        del self._ack_events[command]

    def set_param(self, name, value, timeout=3.0):
        """PARAM_SET gönder, PARAM_VALUE yankısını bekle.
        Returns (ok, current_value_or_error). Yalnız kullanıcı eylemiyle
        çağrılır (servo trim) — uygulama başka hiçbir parametre yazmaz."""
        with self._lock:
            conn = self._conn
            sysid, compid = self.sysid, self.compid
            if conn is None or sysid is None or not self.connected:
                return False, "bağlı değil"
        try:
            conn.mav.param_set_send(
                sysid, compid, name.encode("ascii"), float(value),
                mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
        except Exception as exc:
            return False, str(exc)
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                v = self.params.get(name)
            if v is not None and abs(v - float(value)) < 0.5:
                return True, v
            time.sleep(0.1)
        with self._lock:
            return False, "otopilot doğrulamadı (PARAM_VALUE yok)"

    def rc_override(self, values):
        """RC_CHANNELS_OVERRIDE gönder. values: 8 kanal PWM listesi
        (0 = kanalı serbest bırak). True/False döner."""
        with self._lock:
            conn = self._conn
            sysid, compid = self.sysid, self.compid
            if conn is None or sysid is None or not self.connected:
                return False
        try:
            v = (list(values) + [0] * 8)[:8]
            conn.mav.rc_channels_override_send(sysid, compid, *v)
            return True
        except Exception:
            return False

    def accel_pos_request(self, since):
        """Latest accel-cal position the autopilot is requesting, as
        (position, time), or (None, None) if none since `since`."""
        with self._lock:
            r = self._accel_pos_req
        if r is None or r[1] < since:
            return None, None
        return r

    def clear_accel_pos_request(self):
        with self._lock:
            self._accel_pos_req = None

    def send_command(self, command, p1=0, p2=0, p3=0, p4=0, p5=0, p6=0, p7=0):
        """Fire-and-forget COMMAND_LONG (no ACK wait). Returns True if sent."""
        with self._lock:
            conn = self._conn
            sysid, compid = self.sysid, self.compid
            if conn is None or sysid is None or not self.connected:
                return False
        try:
            conn.mav.command_long_send(
                sysid, compid, command, 0, p1, p2, p3, p4, p5, p6, p7)
            return True
        except Exception:
            return False

    def request_message(self, msg_id):
        return self.run_command(
            mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE, p1=msg_id)

    # ------------------------------------------------------------------ #
    # Snapshots for the API / checks
    # ------------------------------------------------------------------ #

    def get_msg(self, mtype, max_age=None):
        """Latest message of a type, or None if absent/stale."""
        with self._lock:
            entry = self.messages.get(mtype)
        if not entry:
            return None
        msg, t = entry
        if max_age is not None and time.time() - t > max_age:
            return None
        return msg

    def msg_age(self, mtype):
        with self._lock:
            entry = self.messages.get(mtype)
        return None if not entry else time.time() - entry[1]

    def get_msg_after(self, mtype, since):
        """Latest message of a type, only if received at/after `since`."""
        with self._lock:
            entry = self.messages.get(mtype)
        if not entry:
            return None
        msg, t = entry
        return msg if t >= since else None

    def get_history(self, mtype, seconds):
        cutoff = time.time() - seconds
        with self._lock:
            return [m for (t, m) in self.history.get(mtype, ()) if t >= cutoff]

    def recent_statustexts(self, since=None):
        with self._lock:
            items = list(self.statustexts)
        if since is not None:
            items = [s for s in items if s[0] >= since]
        return items

    def link_stats(self):
        now = time.time()
        with self._lock:
            recent = [t for t in self._msg_times if now - t < 5.0]
            rate = len(recent) / 5.0
            return {
                "connected": self.connected and (now - self.last_heartbeat) < 5.0,
                "heartbeat_age": (now - self.last_heartbeat) if self.last_heartbeat else None,
                "heartbeats": self.heartbeat_count,
                "msg_rate": round(rate, 1),
                "target": self.target,
                "sysid": self.sysid,
            }


def list_serial_ports():
    """Detected serial ports with descriptions (flight controllers first)."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    ports = []
    for p in list_ports.comports():
        desc = " ".join(filter(None, [p.description or "", p.manufacturer or ""]))
        lower = desc.lower()
        is_fc = any(k in lower for k in (
            "ardupilot", "px4", "pixhawk", "cube", "mro", "holybro",
            "matek", "fmu", "autopilot"))
        ports.append({"device": p.device, "description": desc.strip(),
                      "likely_fc": is_fc})
    ports.sort(key=lambda x: (not x["likely_fc"], x["device"]))
    return ports
