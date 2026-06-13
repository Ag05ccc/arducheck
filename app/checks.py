"""ArduCheck check engine.

Implements the preflight check catalog derived from the official ArduPilot
documentation (arming/prearm checks, failsafe, calibration and first-flight
guides). Every check returns PASS / WARN / FAIL / INFO / SKIP plus a Turkish
explanation. The engine never arms the vehicle and never writes parameters.
"""

import math
import os
import time

from pymavlink import mavutil

# referans parametre dosyası: araç parametreleri bununla karşılaştırılır.
# Paket içinde değil, depo kökünde tutulur (kullanıcıya ait çalışma-zamanı verisi).
REF_PARAMS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "ref_params.param")

PASS, WARN, FAIL, INFO, SKIP = "PASS", "WARN", "FAIL", "INFO", "SKIP"

GATHER_SECONDS = 6.0     # telemetry window evaluated by the checks
PREARM_WAIT = 4.0        # seconds to collect STATUSTEXT after MAV_CMD 401

# --------------------------------------------------------------------- #
# Parameter name compatibility (Plane >= 4.4 renamed several params).
# map: new name -> (old name, scale old->new)
# --------------------------------------------------------------------- #
PARAM_ALIASES = {
    "RTL_ALTITUDE": ("ALT_HOLD_RTL", 0.01),      # cm -> m
    "AIRSPEED_MIN": ("ARSPD_FBW_MIN", 1.0),
    "AIRSPEED_MAX": ("ARSPD_FBW_MAX", 1.0),
    "AIRSPEED_CRUISE": ("TRIM_ARSPD_CM", 0.01),  # cm/s -> m/s
    "ROLL_LIMIT_DEG": ("LIM_ROLL_CD", 0.01),     # cdeg -> deg
    "PTCH_LIM_MAX_DEG": ("LIM_PITCH_MAX", 0.01),
    "PTCH_LIM_MIN_DEG": ("LIM_PITCH_MIN", 0.01),
}

SENSOR_BITS = [
    # (bit, Turkish name, required-level): "fail" -> FAIL when absent/unhealthy
    (0x01, "3D Jiroskop", "fail"),
    (0x02, "3D İvmeölçer", "fail"),
    (0x04, "3D Manyetometre (Pusula)", "mag"),
    (0x08, "Mutlak Basınç (Barometre)", "fail"),
    (0x10, "Fark Basıncı (Hava Hızı)", "airspeed"),
    (0x20, "GPS", "fail"),
    (0x10000, "RC Alıcı", "health_only"),
    (0x20000, "2. Jiroskop", "if_present"),
    (0x40000, "2. İvmeölçer", "if_present"),
    (0x80000, "2. Pusula", "if_present"),
    (0x100000, "Geofence", "fence"),
    (0x200000, "AHRS", "fail"),
    (0x400000, "Arazi (Terrain)", "warn_only"),
    (0x1000000, "Kayıt (Logging)", "if_present"),
    (0x2000000, "Batarya", "battery"),
    (0x10000000, "Pre-Arm (arm edilebilir)", "prearm"),
]

ARMING_CHECK_BITS = {
    1: "Tümü", 2: "Barometre", 4: "Pusula", 8: "GPS kilidi", 16: "INS",
    32: "Parametreler", 64: "RC", 128: "Kart voltajı", 256: "Batarya",
    512: "Hava hızı", 1024: "Kayıt", 2048: "Emniyet anahtarı",
    4096: "GPS yapılandırma", 8192: "Sistem", 16384: "Görev",
    32768: "Mesafe sensörü", 65536: "Kamera", 131072: "Yetkilendirme",
    524288: "FFT",
}

GPS_FIX_NAMES = {
    0: "GPS yok", 1: "Fix yok", 2: "2D fix", 3: "3D fix", 4: "DGPS",
    5: "RTK Float", 6: "RTK Fixed", 7: "Statik", 8: "PPP",
}

BATTERY_FAULTS = {
    1: "derin deşarj", 2: "voltaj sıçraması", 4: "hücre arızası",
    8: "aşırı akım", 16: "aşırı sıcaklık", 32: "düşük sıcaklık",
    64: "uyumsuz voltaj", 128: "uyumsuz firmware", 256: "uyumsuz hücre yapısı",
}

EKF_FLAG_NAMES = {
    1: "duruş (attitude)", 2: "yatay hız", 4: "dikey hız",
    16: "mutlak yatay konum", 32: "mutlak dikey konum",
}


class Ctx:
    """Snapshot of everything the checks read, taken once per run."""

    def __init__(self, mav, options):
        self.mav = mav
        self.options = options
        self.params = dict(mav.params)
        self.start_time = time.time()
        self.prearm_texts = []      # STATUSTEXT seen after forcing prearm run
        self.window_texts = []      # STATUSTEXT during this run's window
        self.boot_texts = []        # persistent boot-time non-prearm messages
        self.params_ok = True       # False if param download was incomplete
        self.clip_before = None
        self.clip_after = None
        self.results = []

    # parameter access with old/new name fallback
    def param(self, name, default=None):
        if name in self.params:
            return self.params[name]
        alias = PARAM_ALIASES.get(name)
        if alias and alias[0] in self.params:
            return self.params[alias[0]] * alias[1]
        return default

    def has_param(self, name):
        return self.param(name) is not None

    def add(self, group, cid, name, status, detail=""):
        self.results.append({"group": group, "id": cid, "name": name,
                             "status": status, "detail": detail})

    def texts_matching(self, *needles):
        found = []
        for (_, sev, text, _n) in self.window_texts:
            low = text.lower()
            if any(n.lower() in low for n in needles):
                found.append(text)
        return found


def _fmt(v, nd=1):
    if v is None:
        return "?"
    if isinstance(v, float):
        return ("%."+str(nd)+"f") % v
    return str(v)


# ===================================================================== #
# Check groups
# ===================================================================== #

def check_connection(ctx):
    g = "Bağlantı / Sistem"
    mav = ctx.mav
    hb = mav.get_msg("HEARTBEAT", max_age=5)
    if hb is None:
        ctx.add(g, "CONN-01", "Kalp atışı (heartbeat)", FAIL,
                "Son 5 saniyede heartbeat alınamadı. Bağlantıyı kontrol edin.")
        return False
    ctx.add(g, "CONN-01", "Kalp atışı (heartbeat)", PASS,
            "Araç %d, son %.1f sn önce." % (mav.sysid or 0,
                                            mav.msg_age("HEARTBEAT") or 0))

    if hb.autopilot == mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA:
        ctx.add(g, "CONN-03", "Otopilot tipi", PASS, "ArduPilot")
    else:
        ctx.add(g, "CONN-03", "Otopilot tipi", WARN,
                "ArduPilot değil (autopilot=%d). Kontroller ArduPilot'a göre "
                "tasarlandı." % hb.autopilot)

    vtype = hb.type
    if vtype == mavutil.mavlink.MAV_TYPE_FIXED_WING:
        ctx.add(g, "CONN-02", "Araç tipi", PASS, "Sabit kanat (Plane)")
    elif 19 <= vtype <= 25:
        ctx.add(g, "CONN-02", "Araç tipi", WARN,
                "VTOL/QuadPlane (tip %d). Plane kontrolleri uygulanacak." % vtype)
    else:
        ctx.add(g, "CONN-02", "Araç tipi", WARN,
                "Sabit kanat değil (tip %d). Bazı kontroller uçağa özgüdür." % vtype)

    armed = bool(hb.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
    if armed:
        ctx.add(g, "CONN-04", "Arm durumu", FAIL,
                "ARAÇ ARM EDİLMİŞ! Uçuş öncesi kontroller yalnızca disarm "
                "durumda yapılmalıdır.")
    else:
        ctx.add(g, "CONN-04", "Arm durumu", PASS, "Disarm (güvenli)")

    ver = mav.autopilot_version
    if ver:
        sw = ver.flight_sw_version
        major, minor, patch = (sw >> 24) & 0xFF, (sw >> 16) & 0xFF, (sw >> 8) & 0xFF
        vs = "%d.%d.%d" % (major, minor, patch)
        fw_str = ""
        for (_, _, text, _n) in ctx.window_texts:
            if "ArduPlane" in text or "ArduCopter" in text:
                fw_str = " — " + text
                break
        if (major, minor) >= (4, 3):
            ctx.add(g, "CONN-05", "Firmware sürümü", PASS, "v%s%s" % (vs, fw_str))
        else:
            ctx.add(g, "CONN-05", "Firmware sürümü", WARN,
                    "v%s eski bir sürüm; pre-arm kontrolünü zorla çalıştırma "
                    "(MAV_CMD 401) desteklenmeyebilir." % vs)
        ctx.fw_version = (major, minor, patch)
    else:
        ctx.add(g, "CONN-05", "Firmware sürümü", INFO,
                "AUTOPILOT_VERSION alınamadı; sürüm doğrulanamadı.")
        ctx.fw_version = None

    sys_status = mav.get_msg("SYS_STATUS", max_age=5)
    if sys_status:
        drop = sys_status.drop_rate_comm
        if drop < 200:
            ctx.add(g, "CONN-06", "Telemetri bağlantı kalitesi", PASS,
                    "Paket kaybı %%%.1f" % (drop / 100.0))
        elif drop < 1000:
            ctx.add(g, "CONN-06", "Telemetri bağlantı kalitesi", WARN,
                    "Paket kaybı %%%.1f — telsiz bağlantısını kontrol edin."
                    % (drop / 100.0))
        else:
            ctx.add(g, "CONN-06", "Telemetri bağlantı kalitesi", FAIL,
                    "Paket kaybı %%%.1f — bağlantı güvenilmez." % (drop / 100.0))
        load = sys_status.load
        if load < 800:
            ctx.add(g, "CONN-07", "Otopilot işlemci yükü", PASS,
                    "%%%.0f" % (load / 10.0))
        elif load <= 900:
            ctx.add(g, "CONN-07", "Otopilot işlemci yükü", WARN,
                    "%%%.0f — yüksek işlemci yükü." % (load / 10.0))
        else:
            ctx.add(g, "CONN-07", "Otopilot işlemci yükü", FAIL,
                    "%%%.0f — işlemci aşırı yüklü." % (load / 10.0))
    else:
        ctx.add(g, "CONN-06", "Telemetri bağlantı kalitesi", SKIP,
                "SYS_STATUS alınamadı.")

    # CrashDump/internal-error messages persist from boot, so look at the
    # longer boot-time window rather than just this run's window.
    boot_low = [t[2] for t in ctx.boot_texts]
    bad = [s for s in boot_low if "internal errors" in s.lower()
           or "crashdump data detected" in s.lower()]
    bad += ctx.texts_matching("internal errors", "crashdump data detected")
    bad = list(dict.fromkeys(bad))
    if bad:
        ctx.add(g, "CONN-09", "Dahili hata / CrashDump", FAIL,
                "Otopilot dahili hata bildiriyor: " + "; ".join(bad[:3]) +
                " — uçuştan önce kesinlikle çözülmeli.")
    else:
        ctx.add(g, "CONN-09", "Dahili hata / CrashDump", PASS,
                "Dahili hata bildirimi yok.")

    if not ctx.params_ok:
        pp = ctx.mav.param_progress() or {}
        ctx.add(g, "CONN-10", "Parametre listesi", WARN,
                "Parametre listesi tam indirilemedi (%s/%s) — parametre "
                "tabanlı kontroller eksik olabilir; bağlantıyı kontrol edip "
                "tekrar çalıştırın." % (pp.get("got", "?"), pp.get("total")
                                        or "?"))
    return not armed


def check_sensors(ctx):
    g = "Sensör Sağlığı"
    ss = ctx.mav.get_msg("SYS_STATUS", max_age=5)
    if ss is None:
        ctx.add(g, "SENS-00", "Sensör durumu", FAIL,
                "SYS_STATUS mesajı alınamıyor; sensör sağlığı doğrulanamadı.")
        return
    present, enabled, health = (ss.onboard_control_sensors_present,
                                ss.onboard_control_sensors_enabled,
                                ss.onboard_control_sensors_health)
    for bit, name, level in SENSOR_BITS:
        cid = "SENS-%07X" % bit
        is_present = bool(present & bit)
        is_enabled = bool(enabled & bit)
        is_healthy = bool(health & bit)

        if level == "prearm":
            if is_healthy:
                ctx.add(g, cid, name, PASS,
                        "Otopilot kendi pre-arm kontrollerinden geçiyor.")
            else:
                ctx.add(g, cid, name, FAIL,
                        "Otopilot pre-arm kontrolleri GEÇMİYOR. Ayrıntılar "
                        "'Otopilot Pre-Arm Sonucu' bölümünde.")
            continue

        if not is_present:
            if level == "fail":
                ctx.add(g, cid, name, FAIL, "Sensör mevcut değil.")
            elif level == "mag" and ctx.param("COMPASS_USE", 1) >= 1:
                ctx.add(g, cid, name, WARN,
                        "Pusula takılı görünmüyor ama COMPASS_USE=1.")
            elif level == "airspeed" and ctx.param("ARSPD_USE", 0) >= 1:
                ctx.add(g, cid, name, FAIL,
                        "ARSPD_USE=1 ama hava hızı sensörü mevcut görünmüyor.")
            # if_present / warn_only / fence / battery absent -> skip silently
            continue

        if not is_enabled:
            if level in ("fail", "mag", "airspeed", "battery"):
                ctx.add(g, cid, name, WARN, "Sensör mevcut ama devre dışı.")
            continue

        if is_healthy:
            ctx.add(g, cid, name, PASS, "Sağlıklı")
        else:
            sev = FAIL
            if level == "warn_only":
                sev = WARN
            ctx.add(g, cid, name, sev, "SENSÖR SAĞLIKSIZ bildiriliyor.")


def check_ekf(ctx):
    g = "EKF / Konum Kestirimi"
    ekf = ctx.mav.get_msg("EKF_STATUS_REPORT", max_age=5)
    if ekf is None:
        ctx.add(g, "EKF-00", "EKF durumu", WARN,
                "EKF_STATUS_REPORT alınamadı; EKF sağlığı doğrulanamadı.")
        return
    flags = ekf.flags
    missing = [n for b, n in EKF_FLAG_NAMES.items()
               if b in (1, 2, 4) and not flags & b]
    if missing:
        ctx.add(g, "EKF-01", "EKF temel kestirimler", FAIL,
                "Eksik kestirimler: " + ", ".join(missing) +
                ". EKF henüz oturmamış olabilir; aracı sabit tutup bekleyin.")
    else:
        ctx.add(g, "EKF-01", "EKF temel kestirimler", PASS,
                "Duruş ve hız kestirimleri sağlıklı.")

    horiz_ok = bool(flags & 16) or bool(flags & 512)
    vert_ok = bool(flags & 32)
    if horiz_ok and vert_ok:
        ctx.add(g, "EKF-02", "EKF konum kestirimi", PASS,
                "Mutlak konum kestirimi mevcut.")
    else:
        ctx.add(g, "EKF-02", "EKF konum kestirimi", FAIL,
                "Mutlak konum kestirimi yok (GPS kilidi/ev konumu bekleniyor "
                "olabilir).")

    bad_modes = []
    if flags & 128:
        bad_modes.append("sabit konum modu")
    if flags & 1024:
        bad_modes.append("EKF başlatılmamış")
    if flags & 32768:
        bad_modes.append("GPS glitch")
    if bad_modes:
        ctx.add(g, "EKF-03", "EKF çalışma modu", FAIL,
                "Sorunlu durum: " + ", ".join(bad_modes))
    else:
        ctx.add(g, "EKF-03", "EKF çalışma modu", PASS, "Normal")

    variances = [("hız", ekf.velocity_variance),
                 ("yatay konum", ekf.pos_horiz_variance),
                 ("dikey konum", ekf.pos_vert_variance),
                 ("pusula", ekf.compass_variance)]
    worst = max(v for _, v in variances)
    detail = ", ".join("%s=%.2f" % (n, v) for n, v in variances)
    if worst < 0.5:
        ctx.add(g, "EKF-04", "EKF varyansları", PASS, detail)
    elif worst < 0.8:
        ctx.add(g, "EKF-04", "EKF varyansları", WARN,
                detail + " — 0.5 üzeri değerler sınırda.")
    else:
        ctx.add(g, "EKF-04", "EKF varyansları", FAIL,
                detail + " — 0.8 üzeri değerler failsafe eşiğinde.")

    incons = ctx.texts_matching("inconsistent", "vel error",
                                "waiting for home",
                                "not using configured AHRS")
    if incons:
        ctx.add(g, "EKF-06", "EKF tutarlılığı", FAIL,
                "Otopilot mesajları: " + "; ".join(incons[:3]))
    else:
        ctx.add(g, "EKF-06", "EKF tutarlılığı", PASS,
                "Tutarsızlık mesajı yok.")


def check_gps(ctx):
    g = "GPS"
    gps = ctx.mav.get_msg("GPS_RAW_INT", max_age=5)
    if gps is None:
        ctx.add(g, "GPS-00", "GPS verisi", FAIL, "GPS_RAW_INT alınamıyor.")
        return
    fix = gps.fix_type
    fix_name = GPS_FIX_NAMES.get(fix, str(fix))
    if fix >= 3:
        ctx.add(g, "GPS-01", "GPS fix", PASS, fix_name)
    else:
        ctx.add(g, "GPS-01", "GPS fix", FAIL,
                "%s — 3D fix bekleniyor. Açık alanda bekleyin." % fix_name)

    hdop_good = ctx.param("GPS_HDOP_GOOD", 140)
    eph = gps.eph
    if eph == 65535:
        ctx.add(g, "GPS-02", "HDOP", FAIL, "HDOP bilinmiyor.")
    elif eph <= hdop_good:
        ctx.add(g, "GPS-02", "HDOP", PASS, "%.2f" % (eph / 100.0))
    elif eph <= 200:
        ctx.add(g, "GPS-02", "HDOP", WARN,
                "%.2f — sınırda (eşik %.2f)." % (eph / 100.0, hdop_good / 100.0))
    else:
        ctx.add(g, "GPS-02", "HDOP", FAIL, "%.2f — zayıf GPS geometrisi." % (eph / 100.0))

    sats = gps.satellites_visible
    if sats == 255:
        ctx.add(g, "GPS-03", "Uydu sayısı", FAIL, "Bilinmiyor")
    elif sats >= 8:
        ctx.add(g, "GPS-03", "Uydu sayısı", PASS, "%d uydu" % sats)
    elif sats >= 6:
        ctx.add(g, "GPS-03", "Uydu sayısı", WARN, "%d uydu — az." % sats)
    else:
        ctx.add(g, "GPS-03", "Uydu sayısı", FAIL, "%d uydu — yetersiz." % sats)

    gps2 = ctx.mav.get_msg("GPS2_RAW", max_age=5)
    if gps2 and gps2.fix_type >= 2 and gps.fix_type >= 2:
        d = _haversine_m(gps.lat / 1e7, gps.lon / 1e7,
                         gps2.lat / 1e7, gps2.lon / 1e7)
        if d < 10:
            ctx.add(g, "GPS-05", "Çift GPS tutarlılığı", PASS, "%.1f m fark" % d)
        elif d < 50:
            ctx.add(g, "GPS-05", "Çift GPS tutarlılığı", WARN, "%.1f m fark" % d)
        else:
            ctx.add(g, "GPS-05", "Çift GPS tutarlılığı", FAIL, "%.1f m fark" % d)

    home = ctx.mav.get_msg("HOME_POSITION")
    if home is not None and (home.latitude or home.longitude):
        ctx.add(g, "GPS-07", "Ev (home) konumu", PASS,
                "%.6f, %.6f" % (home.latitude / 1e7, home.longitude / 1e7))
    else:
        ctx.add(g, "GPS-07", "Ev (home) konumu", FAIL,
                "Ev konumu ayarlanmamış — RTL çalışmaz. GPS kilidi sonrası "
                "otomatik ayarlanır.")

    diffs = ctx.texts_matching("GPS and AHRS differ")
    if diffs:
        ctx.add(g, "GPS-06", "GPS-EKF uyumu", FAIL, "; ".join(diffs[:2]))
    cfg = ctx.texts_matching("still configuring this GPS", "was not found",
                             "primary but TYPE 0")
    if cfg:
        ctx.add(g, "GPS-08", "GPS yapılandırması", FAIL, "; ".join(cfg[:2]))


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def check_compass(ctx):
    g = "Pusula"
    if ctx.param("COMPASS_USE", 1) < 1:
        ctx.add(g, "MAG-01", "Pusula kullanımı", INFO,
                "COMPASS_USE=0 — pusula devre dışı, kontroller atlandı.")
        return
    suffixes = [("", ""), ("2", "2"), ("3", "3")]
    any_checked = False
    for idx, sfx in suffixes:
        dev_id = ctx.param("COMPASS_DEV_ID" + idx)
        if dev_id == 0 or (idx and dev_id is None):
            continue  # compass not fitted (dev_id 0), or extra unit absent
        ofs = [ctx.param("COMPASS_OFS%s_%s" % (sfx, ax)) for ax in "XYZ"]
        if any(v is None for v in ofs):
            continue
        any_checked = True
        label = "Pusula %s ofsetleri" % (idx or "1")
        mag = math.sqrt(sum(v * v for v in ofs))
        offs_max = min(600.0, ctx.param("COMPASS_OFFS_MAX", 850))
        if all(v == 0 for v in ofs):
            ctx.add(g, "MAG-02-%s" % (idx or "1"), label, WARN,
                    "Ofsetler sıfır — pusula kalibrasyonu hiç yapılmamış "
                    "görünüyor. (Otopilotun kendi pre-arm sonucuna bakın.)")
        elif mag > offs_max:
            ctx.add(g, "MAG-03-%s" % (idx or "1"), label, FAIL,
                    "Ofset büyüklüğü %.0f > %.0f — yeniden kalibrasyon gerekli."
                    % (mag, offs_max))
        elif mag > 500:
            ctx.add(g, "MAG-03-%s" % (idx or "1"), label, WARN,
                    "Ofset büyüklüğü %.0f (500 üzeri sınırda)." % mag)
        else:
            ctx.add(g, "MAG-03-%s" % (idx or "1"), label, PASS,
                    "Ofset büyüklüğü %.0f" % mag)
        dia = [ctx.param("COMPASS_DIA%s_%s" % (sfx, ax)) for ax in "XYZ"]
        if all(v is not None for v in dia):
            if any(not (0.8 <= v <= 1.2) for v in dia):
                ctx.add(g, "MAG-05-%s" % (idx or "1"),
                        "Pusula %s ölçek (soft-iron)" % (idx or "1"), WARN,
                        "DIA değerleri 0.8–1.2 aralığı dışında: %s"
                        % ", ".join("%.2f" % v for v in dia))

    if not any_checked:
        dev1 = ctx.param("COMPASS_DEV_ID")
        if dev1 == 0:
            ctx.add(g, "MAG-04", "Pusula cihazı", FAIL,
                    "COMPASS_USE=1 ama hiçbir pusula algılanmadı "
                    "(COMPASS_DEV_ID=0).")
        elif dev1 is None:
            ctx.add(g, "MAG-04", "Pusula cihazı", SKIP,
                    "Pusula parametreleri okunamadı.")

    field = ctx.texts_matching("check mag field", "compasses inconsistent",
                               "compass not healthy", "compass not calibrated",
                               "compass offsets too high",
                               "compass calibration running")
    if field:
        ctx.add(g, "MAG-06", "Pusula alan/tutarlılık", FAIL,
                "Otopilot mesajları: " + "; ".join(field[:3]))
    else:
        ctx.add(g, "MAG-06", "Pusula alan/tutarlılık", PASS,
                "Manyetik alan sorunu bildirilmedi.")


def check_ins(ctx):
    g = "İvmeölçer / Jiroskop"
    for n, pfx in ((1, "INS_ACC"), (2, "INS_ACC2"), (3, "INS_ACC3")):
        acc_id = ctx.param("%s_ID" % pfx)
        if n > 1 and (acc_id is None or acc_id == 0):
            continue
        offs = [ctx.param("%sOFFS_%s" % (pfx, ax)) for ax in "XYZ"]
        scal = [ctx.param("%sSCAL_%s" % (pfx, ax)) for ax in "XYZ"]
        if any(v is None for v in offs):
            continue
        label = "İvmeölçer %d kalibrasyonu" % n
        if all(v == 0 for v in offs):
            ctx.add(g, "INS-01-%d" % n, label, WARN,
                    "Ofsetler sıfır — ivmeölçer kalibrasyonu yapılmamış "
                    "görünüyor. (Otopilotun kendi pre-arm sonucuna bakın.)")
        elif scal and all(v is not None for v in scal) and \
                any(not (0.8 <= v <= 1.2) for v in scal):
            ctx.add(g, "INS-02-%d" % n, label, FAIL,
                    "Ölçek değerleri 0.8–1.2 dışında: %s — yeniden "
                    "kalibrasyon gerekli."
                    % ", ".join("%.3f" % v for v in scal))
        else:
            ctx.add(g, "INS-01-%d" % n, label, PASS,
                    "Ofsetler: %s" % ", ".join("%.3f" % v for v in offs))

    bad = ctx.texts_matching("gyros not calibrated", "gyros not healthy",
                             "gyros inconsistent", "accels inconsistent",
                             "accels not healthy", "requires reboot")
    if bad:
        ctx.add(g, "INS-04", "INS sağlık/tutarlılık", FAIL,
                "Otopilot mesajları: " + "; ".join(bad[:3]))
    else:
        ctx.add(g, "INS-04", "INS sağlık/tutarlılık", PASS,
                "Tutarsızlık mesajı yok.")

    imu = ctx.mav.get_history("RAW_IMU", GATHER_SECONDS)
    if imu:
        max_gyro = max(max(abs(m.xgyro), abs(m.ygyro), abs(m.zgyro))
                       for m in imu) / 1000.0  # mrad/s -> rad/s
        deg = math.degrees(max_gyro)
        if deg < 4:
            ctx.add(g, "INS-05", "Araç hareketsizliği", PASS,
                    "Maks. dönüş hızı %.1f°/s" % deg)
        else:
            ctx.add(g, "INS-05", "Araç hareketsizliği", WARN,
                    "Araç hareket ediyor (%.1f°/s) — kontroller araç "
                    "sabitken yapılmalı." % deg)


def check_airspeed(ctx):
    g = "Hava Hızı"
    use = ctx.param("ARSPD_USE", None)
    atype = ctx.param("ARSPD_TYPE", None)
    if use is None and atype is None:
        ctx.add(g, "ASP-00", "Hava hızı sensörü", INFO,
                "ARSPD parametreleri yok — hava hızı sensörü desteklenmiyor.")
        return
    use = use or 0
    atype = atype or 0
    if atype == 0:
        if use >= 1:
            ctx.add(g, "ASP-01", "Sensör yapılandırması", FAIL,
                    "ARSPD_USE=%d ama ARSPD_TYPE=0 (sensör tanımlı değil)."
                    % int(use))
        else:
            ctx.add(g, "ASP-01", "Sensör yapılandırması", INFO,
                    "Hava hızı sensörü yapılandırılmamış (ARSPD_TYPE=0). "
                    "Uçuş sentetik hava hızıyla yapılır.")
        return
    if use == 0:
        ctx.add(g, "ASP-02", "Kullanım modu", WARN,
                "Sensör tanımlı ama ARSPD_USE=0 — yalnızca kayıt için "
                "kullanılıyor, uçuş kontrolünde KULLANILMIYOR.")
    elif use == 2:
        ctx.add(g, "ASP-02", "Kullanım modu", INFO,
                "ARSPD_USE=2 — yalnızca gaz rölantide kullanılır (planör modu).")
    else:
        ctx.add(g, "ASP-02", "Kullanım modu", PASS, "ARSPD_USE=1")

    ratio = ctx.param("ARSPD_RATIO")
    if ratio is not None:
        if abs(ratio - 1.9936) < 0.001:
            ctx.add(g, "ASP-03", "Hız oranı (ARSPD_RATIO)", WARN,
                    "%.4f — fabrika değeri; uçuşta kalibre edilmemiş." % ratio)
        elif 1.5 <= ratio <= 3.0:
            ctx.add(g, "ASP-03", "Hız oranı (ARSPD_RATIO)", PASS, "%.3f" % ratio)
        else:
            ctx.add(g, "ASP-03", "Hız oranı (ARSPD_RATIO)", FAIL,
                    "%.3f — normal aralık (1.5–3.0) dışında." % ratio)

    if ctx.param("ARSPD_AUTOCAL", 0) == 1:
        ctx.add(g, "ASP-04", "Otomatik kalibrasyon", WARN,
                "ARSPD_AUTOCAL=1 açık bırakılmış — kalibrasyon sonrası "
                "kapatılması önerilir.")

    skip_cal = ctx.param("ARSPD_SKIP_CAL", 0)
    offset = ctx.param("ARSPD_OFFSET", 0)
    if skip_cal >= 1 and offset == 0:
        ctx.add(g, "ASP-05", "Sıfır ofset", WARN,
                "ARSPD_SKIP_CAL=%d ve ofset 0 — bu açılışta elle 'pitot kapalı' "
                "kalibrasyonu yapılmalı." % int(skip_cal))

    vfr = ctx.mav.get_history("VFR_HUD", GATHER_SECONDS)
    if vfr and use >= 1:
        mx = max(m.airspeed for m in vfr)
        if mx <= 5:
            ctx.add(g, "ASP-08", "Durağan hava hızı okuması", PASS,
                    "En çok %.1f m/s (araç sabitken)." % mx)
        else:
            ctx.add(g, "ASP-08", "Durağan hava hızı okuması", WARN,
                    "Araç sabitken %.1f m/s okunuyor — sıfır kalibrasyonu "
                    "gerekli olabilir (pitot kapatılarak)." % mx)

    bad = ctx.texts_matching("airspeed 1 not healthy", "airspeed not healthy",
                             "airspeed cal")
    if bad:
        ctx.add(g, "ASP-07", "Sensör sağlığı (mesajlar)", FAIL,
                "; ".join(bad[:2]))


def check_battery(ctx):
    g = "Batarya / Güç"
    monitor = ctx.param("BATT_MONITOR")
    if monitor is None:
        ctx.add(g, "BAT-01", "Batarya izleme", SKIP,
                "BATT_MONITOR parametresi okunamadı.")
        return
    if monitor == 0:
        ctx.add(g, "BAT-01", "Batarya izleme", FAIL,
                "BATT_MONITOR=0 — batarya izlenmiyor, batarya failsafe "
                "ÇALIŞMAZ.")
        return
    ctx.add(g, "BAT-01", "Batarya izleme", PASS, "BATT_MONITOR=%d" % int(monitor))

    ss = ctx.mav.get_msg("SYS_STATUS", max_age=5)
    bs = ctx.mav.get_msg("BATTERY_STATUS", max_age=5)
    volt = None
    if ss and ss.voltage_battery not in (0, 65535):
        volt = ss.voltage_battery / 1000.0
    if volt is None and bs:
        vs = [v for v in bs.voltages if v not in (0, 65535)]
        if vs:
            volt = sum(vs) / 1000.0
    if volt is None:
        ctx.add(g, "BAT-04", "Batarya voltajı", WARN, "Voltaj okunamadı.")
    else:
        arm_volt = ctx.param("BATT_ARM_VOLT", 0)
        low_volt = ctx.param("BATT_LOW_VOLT", 0)
        crt_volt = ctx.param("BATT_CRT_VOLT", 0)
        cells = max(1, round(volt / 3.9)) if volt > 3 else 1
        per_cell = volt / cells
        detail = "%.2f V (~%dS, %.2f V/hücre)" % (volt, cells, per_cell)
        if crt_volt and volt <= crt_volt:
            ctx.add(g, "BAT-07", "Batarya voltajı", FAIL,
                    detail + " — KRİTİK eşiğin (%.1f V) altında!" % crt_volt)
        elif low_volt and volt <= low_volt:
            ctx.add(g, "BAT-07", "Batarya voltajı", FAIL,
                    detail + " — düşük voltaj eşiğinin (%.1f V) altında." % low_volt)
        elif arm_volt and volt < arm_volt:
            ctx.add(g, "BAT-04", "Batarya voltajı", FAIL,
                    detail + " — arm voltaj eşiğinin (%.1f V) altında." % arm_volt)
        elif per_cell < 3.7:
            ctx.add(g, "BAT-05", "Batarya voltajı", WARN,
                    detail + " — hücre voltajı düşük; tam dolu batarya ile "
                    "uçun.")
        else:
            ctx.add(g, "BAT-04", "Batarya voltajı", PASS, detail)

    if ss and ss.battery_remaining not in (-1, 255):
        rem = ss.battery_remaining
        if rem >= 80:
            ctx.add(g, "BAT-06", "Kalan kapasite", PASS, "%%%d" % rem)
        elif rem >= 50:
            ctx.add(g, "BAT-06", "Kalan kapasite", WARN, "%%%d" % rem)
        else:
            ctx.add(g, "BAT-06", "Kalan kapasite", FAIL,
                    "%%%d — uçuş öncesi için düşük." % rem)

    if bs:
        fault = getattr(bs, "fault_bitmask", 0)
        if fault:
            faults = [n for b, n in BATTERY_FAULTS.items() if fault & b]
            ctx.add(g, "BAT-03", "Batarya arıza bitleri", FAIL,
                    "Arızalar: " + ", ".join(faults))
        state = getattr(bs, "charge_state", 0)
        if state in (3, 4, 5, 6):
            ctx.add(g, "BAT-02", "Batarya şarj durumu", FAIL,
                    "Durum kodu %d (kritik/arızalı)." % state)
        elif state == 2:
            ctx.add(g, "BAT-02", "Batarya şarj durumu", WARN, "Düşük şarj.")

    pw = ctx.mav.get_msg("POWER_STATUS", max_age=5)
    if pw:
        vcc = pw.Vcc
        if 4800 <= vcc <= 5400:
            ctx.add(g, "BAT-08", "Kart 5V hattı", PASS, "%.2f V" % (vcc / 1000.0))
        elif 4300 <= vcc <= 5800:
            ctx.add(g, "BAT-08", "Kart 5V hattı", WARN,
                    "%.2f V — ideal aralık (4.8–5.4 V) dışında." % (vcc / 1000.0))
        else:
            ctx.add(g, "BAT-08", "Kart 5V hattı", FAIL,
                    "%.2f V — güvenli aralık dışında!" % (vcc / 1000.0))
        flags = pw.flags
        if flags & 8 or flags & 16:
            ctx.add(g, "BAT-10", "Güç çevre birimleri", FAIL,
                    "Aşırı akım bayrağı aktif!")
        if flags & 4:
            ctx.add(g, "BAT-11", "USB bağlantısı", WARN,
                    "USB bağlı görünüyor — uçuş öncesi USB kablosunu çıkarın.")


def check_rc(ctx):
    g = "RC Kumanda"
    rc = ctx.mav.get_msg("RC_CHANNELS", max_age=5)
    if rc is None or rc.chancount == 0:
        ctx.add(g, "RC-01", "RC sinyali", FAIL,
                "RC girişi yok. Kumandanın açık ve bağlı olduğundan emin olun.")
        return
    ctx.add(g, "RC-01", "RC sinyali", PASS, "%d kanal alınıyor." % rc.chancount)

    r3min = ctx.param("RC3_MIN")
    r3max = ctx.param("RC3_MAX")
    if r3min is None or r3max is None:
        ctx.add(g, "RC-02", "RC kalibrasyonu", SKIP,
                "RC3_MIN/MAX parametreleri okunamadı.")
    elif r3min == 1100 and r3max == 1900:
        ctx.add(g, "RC-02", "RC kalibrasyonu", WARN,
                "RC3_MIN/MAX fabrika değerlerinde (1100/1900) — RC "
                "kalibrasyonu yapılmamış olabilir.")
    else:
        ctx.add(g, "RC-02", "RC kalibrasyonu", PASS,
                "RC3: %d–%d" % (int(r3min), int(r3max)))

    bad_trim = []
    for n in range(1, 5):
        mn = ctx.param("RC%d_MIN" % n)
        tr = ctx.param("RC%d_TRIM" % n)
        mx = ctx.param("RC%d_MAX" % n)
        if None in (mn, tr, mx):
            continue
        if not (mn <= tr <= mx):
            bad_trim.append("RC%d (min=%d trim=%d max=%d)"
                            % (n, mn, tr, mx))
    if bad_trim:
        ctx.add(g, "RC-03", "RC trim aralıkları", FAIL,
                "Geçersiz: " + "; ".join(bad_trim))
    else:
        ctx.add(g, "RC-03", "RC trim aralıkları", PASS, "RC1–4 tutarlı.")

    out_of_range = []
    for n in range(1, min(rc.chancount, 16) + 1):
        v = getattr(rc, "chan%d_raw" % n, 0)
        if v and not (900 <= v <= 2100):
            out_of_range.append("k%d=%d" % (n, v))
    if out_of_range:
        ctx.add(g, "RC-04", "Kanal değerleri", FAIL,
                "900–2100 µs dışı: " + ", ".join(out_of_range))
    else:
        ctx.add(g, "RC-04", "Kanal değerleri", PASS, "Tüm kanallar aralıkta.")

    thr_ch = int(ctx.param("RCMAP_THROTTLE", 3))
    thr = getattr(rc, "chan%d_raw" % thr_ch, None)
    thr_fs = ctx.param("THR_FS_VALUE", 950)
    if thr:
        thr_min = ctx.param("RC%d_MIN" % thr_ch, 1100)
        if thr <= thr_fs:
            ctx.add(g, "RC-05", "Gaz konumu", FAIL,
                    "Gaz kanalı %d µs — failsafe eşiğinin (%d) altında; "
                    "alıcı failsafe durumunda olabilir." % (thr, int(thr_fs)))
        elif thr <= thr_min + 30:
            ctx.add(g, "RC-05", "Gaz konumu", PASS, "Gaz minimumda (%d µs)." % thr)
        else:
            ctx.add(g, "RC-05", "Gaz konumu", WARN,
                    "Gaz minimumda değil (%d µs, min %d). Kontroller gaz "
                    "kapalıyken yapılmalı." % (thr, int(thr_min)))

    if rc.rssi != 255:
        if rc.rssi > 50:
            ctx.add(g, "RC-07", "RC sinyal gücü (RSSI)", PASS, "%d/254" % rc.rssi)
        else:
            ctx.add(g, "RC-07", "RC sinyal gücü (RSSI)", WARN,
                    "%d/254 — zayıf sinyal." % rc.rssi)

    conf = ctx.texts_matching("RCx_OPTION conflict", "duplicate aux",
                              "disarm switch on",
                              "Multiple SERIAL ports configured for RC")
    if conf:
        ctx.add(g, "RC-09", "RC yapılandırma çakışmaları", FAIL,
                "; ".join(conf[:2]))


def check_failsafe_params(ctx):
    g = "Failsafe Parametreleri"
    p = ctx.param

    thr_fs = p("THR_FAILSAFE")
    if thr_fs is None:
        ctx.add(g, "FS-01", "RC failsafe (THR_FAILSAFE)", SKIP,
                "Parametre bulunamadı.")
    elif thr_fs == 0:
        ctx.add(g, "FS-01", "RC failsafe (THR_FAILSAFE)", FAIL,
                "THR_FAILSAFE=0 — RC bağlantısı koptuğunda failsafe "
                "TETİKLENMEZ! 1 yapılması şiddetle önerilir.")
    elif thr_fs == 2 and p("FS_GCS_ENABL", 0) == 0:
        ctx.add(g, "FS-01", "RC failsafe (THR_FAILSAFE)", WARN,
                "THR_FAILSAFE=2 (yalnızca bildirim) ve GCS failsafe kapalı.")
    else:
        ctx.add(g, "FS-01", "RC failsafe (THR_FAILSAFE)", PASS,
                "THR_FAILSAFE=%d" % int(thr_fs))

    fs_val = p("THR_FS_VALUE")
    r3min = p("RC3_MIN", 1100)
    if fs_val is not None:
        if fs_val >= r3min:
            ctx.add(g, "FS-02", "Failsafe PWM eşiği", FAIL,
                    "THR_FS_VALUE=%d ≥ RC3_MIN=%d — gaz rölantideyken bile "
                    "failsafe tetiklenir!" % (int(fs_val), int(r3min)))
        elif r3min - fs_val < 40:
            ctx.add(g, "FS-02", "Failsafe PWM eşiği", WARN,
                    "THR_FS_VALUE=%d, RC3_MIN=%d — aradaki fark 40 µs'den az."
                    % (int(fs_val), int(r3min)))
        else:
            ctx.add(g, "FS-02", "Failsafe PWM eşiği", PASS,
                    "THR_FS_VALUE=%d (RC3_MIN=%d)" % (int(fs_val), int(r3min)))

    s_act = p("FS_SHORT_ACTN")
    if s_act == 3:
        ctx.add(g, "FS-03", "Kısa failsafe eylemi", WARN,
                "FS_SHORT_ACTN=3 — kısa kopmalarda hiçbir eylem yapılmaz.")
    elif s_act is not None:
        ctx.add(g, "FS-03", "Kısa failsafe eylemi", PASS,
                "FS_SHORT_ACTN=%d" % int(s_act))

    l_act = p("FS_LONG_ACTN")
    if l_act is not None:
        if l_act == 0:
            ctx.add(g, "FS-04", "Uzun failsafe eylemi", WARN,
                    "FS_LONG_ACTN=0 — uzun kopmada yalnızca devam edilir; "
                    "RTL (1) önerilir.")
        elif l_act == 2:
            ctx.add(g, "FS-04", "Uzun failsafe eylemi", WARN,
                    "FS_LONG_ACTN=2 (süzülme) — motor kapanır ve araç süzülür.")
        elif l_act == 3:
            ctx.add(g, "FS-04", "Uzun failsafe eylemi", FAIL,
                    "FS_LONG_ACTN=3 (paraşüt) — paraşüt takılı değilse "
                    "tehlikeli.")
        else:
            ctx.add(g, "FS-04", "Uzun failsafe eylemi", PASS,
                    "FS_LONG_ACTN=%d" % int(l_act))

    s_to = p("FS_SHORT_TIMEOUT", 1.5)
    l_to = p("FS_LONG_TIMEOUT", 5)
    if l_to <= s_to:
        ctx.add(g, "FS-05", "Failsafe süreleri", FAIL,
                "FS_LONG_TIMEOUT (%.1f) ≤ FS_SHORT_TIMEOUT (%.1f) olmamalı."
                % (l_to, s_to))
    else:
        ctx.add(g, "FS-05", "Failsafe süreleri", PASS,
                "Kısa %.1f sn, uzun %.1f sn" % (s_to, l_to))

    low_v = p("BATT_LOW_VOLT", 0)
    crt_v = p("BATT_CRT_VOLT", 0)
    low_mah = p("BATT_LOW_MAH", 0)
    if low_v == 0 and crt_v == 0 and low_mah == 0:
        ctx.add(g, "FS-08", "Batarya failsafe eşikleri", WARN,
                "Hiçbir batarya failsafe eşiği ayarlanmamış "
                "(BATT_LOW_VOLT/CRT_VOLT/LOW_MAH hepsi 0).")
    elif low_v and crt_v and crt_v >= low_v:
        ctx.add(g, "FS-08", "Batarya failsafe eşikleri", FAIL,
                "BATT_CRT_VOLT (%.1f) ≥ BATT_LOW_VOLT (%.1f) — kritik eşik "
                "düşük eşikten küçük olmalı." % (crt_v, low_v))
    else:
        ctx.add(g, "FS-08", "Batarya failsafe eşikleri", PASS,
                "Düşük %.1f V, kritik %.1f V" % (low_v, crt_v))

    low_act = p("BATT_FS_LOW_ACT")
    crt_act = p("BATT_FS_CRT_ACT")
    if low_act is not None:
        if low_act == 0:
            ctx.add(g, "FS-09", "Batarya failsafe eylemi", WARN,
                    "BATT_FS_LOW_ACT=0 — düşük bataryada yalnızca uyarı "
                    "verilir; RTL (1) önerilir.")
        else:
            ctx.add(g, "FS-09", "Batarya failsafe eylemi", PASS,
                    "Düşük: %d, kritik: %s" % (int(low_act), _fmt(crt_act, 0)))

    gcs_fs = p("FS_GCS_ENABL")
    if gcs_fs is not None:
        ctx.add(g, "FS-07", "GCS failsafe", INFO,
                "FS_GCS_ENABL=%d %s" % (int(gcs_fs),
                "(GCS kopunca failsafe tetiklenir)" if gcs_fs else
                "(GCS kopması failsafe tetiklemez)"))


def check_arming_params(ctx):
    g = "Arming Parametreleri"
    p = ctx.param
    skipchk = p("ARMING_SKIPCHK")  # Plane 4.7+
    arm_check = p("ARMING_CHECK")
    if skipchk is not None:
        sk = int(skipchk)
        if sk == 0:
            ctx.add(g, "ARM-01", "Pre-arm kontrolleri", PASS,
                    "Tüm kontroller etkin (ARMING_SKIPCHK=0).")
        elif sk == -1:
            ctx.add(g, "ARM-01", "Pre-arm kontrolleri", FAIL,
                    "ARMING_SKIPCHK=-1 — TÜM pre-arm kontrolleri atlanıyor! "
                    "Bu çok tehlikelidir.")
        else:
            skipped = [n for b, n in ARMING_CHECK_BITS.items() if sk & b]
            ctx.add(g, "ARM-01", "Pre-arm kontrolleri", WARN,
                    "Atlanan kontroller: " + ", ".join(skipped))
    elif arm_check is not None:
        ac = int(arm_check)
        if ac == 1:
            ctx.add(g, "ARM-01", "Pre-arm kontrolleri", PASS,
                    "Tüm kontroller etkin (ARMING_CHECK=1).")
        elif ac == 0:
            ctx.add(g, "ARM-01", "Pre-arm kontrolleri", FAIL,
                    "ARMING_CHECK=0 — TÜM pre-arm kontrolleri kapalı! "
                    "Bu çok tehlikelidir.")
        else:
            active = [n for b, n in ARMING_CHECK_BITS.items() if b > 1 and ac & b]
            ctx.add(g, "ARM-01", "Pre-arm kontrolleri", WARN,
                    "Yalnızca bazı kontroller etkin: " + ", ".join(active))
    else:
        ctx.add(g, "ARM-01", "Pre-arm kontrolleri", SKIP,
                "ARMING_CHECK parametresi okunamadı.")

    req = p("ARMING_REQUIRE")
    if req is not None:
        if req >= 1:
            ctx.add(g, "ARM-02", "Arm zorunluluğu", PASS,
                    "ARMING_REQUIRE=%d" % int(req))
        else:
            ctx.add(g, "ARM-02", "Arm zorunluluğu", FAIL,
                    "ARMING_REQUIRE=0 — araç açılışta ARMLI başlar, gaz "
                    "CANLIDIR! Kesinlikle düzeltilmeli.")

    rud = p("ARMING_RUDDER")
    if rud == 2:
        ctx.add(g, "ARM-03", "Kumanda ile disarm", WARN,
                "ARMING_RUDDER=2 — uçuşta sol rudder + gaz kapalı tutulursa "
                "araç disarm olabilir.")

    if p("ARMING_OPTIONS", 0) and int(p("ARMING_OPTIONS", 0)) & 1:
        ctx.add(g, "ARM-05", "Pre-arm mesaj gösterimi", WARN,
                "ARMING_OPTIONS bit0 ayarlı — pre-arm mesajları bastırılıyor; "
                "bu araç hata ayrıntısı göstermeyebilir.")


def check_fence(ctx):
    g = "Geofence"
    p = ctx.param
    enable = p("FENCE_ENABLE")
    autoenable = p("FENCE_AUTOENABLE", 0)
    if enable is None:
        ctx.add(g, "FNC-01", "Geofence", SKIP,
                "FENCE_ENABLE parametresi okunamadı.")
        return
    if not enable and not autoenable:
        ctx.add(g, "FNC-01", "Geofence", INFO,
                "Geofence kapalı (FENCE_ENABLE=0). Sınır koruması "
                "istiyorsanız etkinleştirin.")
        return
    if autoenable in (1, 2):
        ctx.add(g, "FNC-02", "Otomatik etkinleştirme", WARN,
                "FENCE_AUTOENABLE=%d kullanım dışı (deprecated); 3 önerilir."
                % int(autoenable))
    action = p("FENCE_ACTION", 1)
    if action == 0:
        ctx.add(g, "FNC-03", "Fence eylemi", FAIL,
                "FENCE_ACTION=0 — ihlalde yalnızca rapor edilir, eylem "
                "yapılmaz.")
    else:
        ctx.add(g, "FNC-03", "Fence eylemi", PASS, "FENCE_ACTION=%d" % int(action))

    ftype = int(p("FENCE_TYPE", 0))
    problems = []
    if ftype & 1 and p("FENCE_ALT_MAX", 0) <= 0:
        problems.append("maks. irtifa fence'i seçili ama FENCE_ALT_MAX≤0")
    if ftype & 2 and p("FENCE_RADIUS", 0) <= 0:
        problems.append("dairesel fence seçili ama FENCE_RADIUS≤0")
    if ftype & 4 and p("FENCE_TOTAL", 0) <= 0:
        problems.append("poligon fence seçili ama yüklü nokta yok")
    if problems:
        ctx.add(g, "FNC-04", "Fence yapılandırması", FAIL, "; ".join(problems))
    else:
        ctx.add(g, "FNC-04", "Fence yapılandırması", PASS,
                "FENCE_TYPE=%d tutarlı." % ftype)

    alt_max = p("FENCE_ALT_MAX", 0)
    alt_min = p("FENCE_ALT_MIN", -10)
    rtl_alt = p("RTL_ALTITUDE")
    if ftype & 1 and alt_max > 0:
        if alt_min >= alt_max:
            ctx.add(g, "FNC-05", "Fence irtifa sınırları", FAIL,
                    "FENCE_ALT_MIN ≥ FENCE_ALT_MAX")
        elif rtl_alt is not None and rtl_alt >= alt_max:
            ctx.add(g, "FNC-05", "Fence irtifa sınırları", FAIL,
                    "RTL irtifası (%.0f m) fence tavanının (%.0f m) üzerinde —"
                    " RTL fence ihlali yaratır." % (rtl_alt, alt_max))
        else:
            ctx.add(g, "FNC-05", "Fence irtifa sınırları", PASS,
                    "%.0f–%.0f m" % (alt_min, alt_max))

    fence_txt = ctx.texts_matching("check fence", "fence requires position",
                                   "fences invalid", "outside fence")
    if fence_txt:
        ctx.add(g, "FNC-08", "Fence durumu (mesajlar)", FAIL,
                "; ".join(fence_txt[:2]))


def check_flight_params(ctx):
    """Plane-specific flight envelope parameters."""
    g = "Uçuş Zarfı Parametreleri"
    p = ctx.param
    amin = p("AIRSPEED_MIN")
    amax = p("AIRSPEED_MAX")
    cruise = p("AIRSPEED_CRUISE")
    if amin is not None and amax is not None:
        if amin < 5:
            ctx.add(g, "ENV-01", "Min. hava hızı", FAIL,
                    "AIRSPEED_MIN=%.1f m/s < 5 — geçersiz/tehlikeli." % amin)
        elif amax <= amin:
            ctx.add(g, "ENV-02", "Hız aralığı", FAIL,
                    "AIRSPEED_MAX (%.1f) ≤ AIRSPEED_MIN (%.1f)." % (amax, amin))
        else:
            note = ""
            if amax < 1.5 * amin:
                note = " (MAX < 1.5×MIN — dar aralık)"
            status = WARN if note else PASS
            ctx.add(g, "ENV-01", "Hız aralığı", status,
                    "Min %.1f / maks %.1f m/s%s" % (amin, amax, note))
        if cruise is not None and not (amin < cruise < amax):
            ctx.add(g, "ENV-03", "Seyir hızı", FAIL,
                    "AIRSPEED_CRUISE=%.1f, min–maks (%.1f–%.1f) aralığında "
                    "değil." % (cruise, amin, amax))
        elif cruise is not None:
            ctx.add(g, "ENV-03", "Seyir hızı", PASS, "%.1f m/s" % cruise)

    roll = p("ROLL_LIMIT_DEG")
    if roll is not None:
        if roll <= 0:
            ctx.add(g, "ENV-04", "Yatış limiti", FAIL,
                    "ROLL_LIMIT_DEG=%.0f — araç dönemez!" % roll)
        elif roll < 25:
            ctx.add(g, "ENV-04", "Yatış limiti", WARN,
                    "%.0f° — düşük; dönüş yarıçapı büyük olur." % roll)
        elif roll > 65:
            ctx.add(g, "ENV-04", "Yatış limiti", WARN,
                    "%.0f° — yüksek; dönüşte stall riski." % roll)
        else:
            ctx.add(g, "ENV-04", "Yatış limiti", PASS, "%.0f°" % roll)

    pmax = p("PTCH_LIM_MAX_DEG")
    pmin = p("PTCH_LIM_MIN_DEG")
    pitch_bad = False
    if pmax is not None and pmax <= 3:
        ctx.add(g, "ENV-05", "Burun yukarı limiti", FAIL,
                "PTCH_LIM_MAX_DEG=%.0f — araç tırmanamaz." % pmax)
        pitch_bad = True
    if pmin is not None and pmin >= 0:
        ctx.add(g, "ENV-06", "Burun aşağı limiti", FAIL,
                "PTCH_LIM_MIN_DEG=%.0f — geçersiz (negatif olmalı)." % pmin)
        pitch_bad = True
    if not pitch_bad and pmax is not None and pmin is not None:
        ctx.add(g, "ENV-05", "Yunuslama limitleri", PASS,
                "%.0f° / %.0f°" % (pmin, pmax))

    rtl_alt = p("RTL_ALTITUDE")
    if rtl_alt is not None:
        if rtl_alt < 0:
            ctx.add(g, "ENV-07", "RTL irtifası", WARN,
                    "RTL_ALTITUDE=-1 — mevcut irtifada döner; alçak irtifada "
                    "kopma riskli olabilir.")
        elif rtl_alt < 30:
            ctx.add(g, "ENV-07", "RTL irtifası", WARN,
                    "%.0f m — alçak; engellerin üzerinde kaldığından emin "
                    "olun." % rtl_alt)
        else:
            ctx.add(g, "ENV-07", "RTL irtifası", PASS, "%.0f m" % rtl_alt)


def check_vibration(ctx):
    g = "Titreşim"
    vibs = ctx.mav.get_history("VIBRATION", GATHER_SECONDS)
    if not vibs:
        ctx.add(g, "VIB-01", "Titreşim seviyesi", SKIP,
                "VIBRATION mesajı alınamadı.")
        return
    mx = max(max(m.vibration_x, m.vibration_y, m.vibration_z) for m in vibs)
    if mx < 30:
        ctx.add(g, "VIB-01", "Titreşim seviyesi", PASS,
                "En çok %.1f m/s² (eşik 30)." % mx)
    elif mx <= 60:
        ctx.add(g, "VIB-01", "Titreşim seviyesi", WARN,
                "%.1f m/s² — 30 üzeri; montajı kontrol edin." % mx)
    else:
        ctx.add(g, "VIB-01", "Titreşim seviyesi", FAIL,
                "%.1f m/s² — 60 üzeri; uçuş güvensiz." % mx)

    if ctx.clip_before is not None and ctx.clip_after is not None:
        diff = [a - b for a, b in zip(ctx.clip_after, ctx.clip_before)]
        if any(d > 0 for d in diff):
            ctx.add(g, "VIB-02", "İvmeölçer doyması (clipping)", FAIL,
                    "Araç sabitken clipping sayacı arttı (%s) — ciddi "
                    "titreşim/montaj sorunu." % diff)
        else:
            ctx.add(g, "VIB-02", "İvmeölçer doyması (clipping)", PASS,
                    "Sayaç artışı yok.")


def check_logging(ctx):
    g = "Kayıt (Logging)"
    ss = ctx.mav.get_msg("SYS_STATUS", max_age=5)
    bit = 0x1000000
    if ss and ss.onboard_control_sensors_present & bit:
        if not ss.onboard_control_sensors_enabled & bit:
            ctx.add(g, "LOG-01", "Kayıt sistemi", WARN, "Kayıt devre dışı.")
        elif ss.onboard_control_sensors_health & bit:
            ctx.add(g, "LOG-01", "Kayıt sistemi", PASS, "Kayıt sağlıklı.")
        else:
            ctx.add(g, "LOG-01", "Kayıt sistemi", FAIL,
                    "Kayıt SAĞLIKSIZ — SD kartı kontrol edin.")
    else:
        ctx.add(g, "LOG-01", "Kayıt sistemi", INFO,
                "Kayıt sistemi bildirilmiyor.")
    logtxt = ctx.texts_matching("logging failed", "no sd card", "no io thread "
                                "heartbeat", "logging not started",
                                "failed to create log")
    if logtxt:
        ctx.add(g, "LOG-02", "Kayıt mesajları", FAIL, "; ".join(logtxt[:2]))


def is_volatile_param(name):
    """Otopilotun kendi yönettiği / her açılışta değişen parametreler:
    referans karşılaştırmasında gürültü üretirler, dışarıda tutulur.
    INS_GYROFFS_/INS_GYR2OFFS_/INS_GYR3OFFS_ her boot'ta yeniden hesaplanır
    (INS_GYR_CAL=1); ARSPDn_OFFSET her hava hızı kalibrasyonunda değişir."""
    return (name.startswith("STAT_")
            or (name.startswith("INS_GYR") and "OFFS_" in name)
            or (name.startswith("ARSPD") and name.endswith("_OFFSET"))
            or name.endswith("_GND_PRESS")
            or name in ("GND_ABS_PRESS", "FORMAT_VERSION"))


def load_ref_params(path=REF_PARAMS_PATH):
    """Referans .param dosyasını oku. Desteklenen biçimler:
    Mission Planner 'NAME,VALUE', düz 'NAME VALUE' ve
    QGC 'sysid compid NAME VALUE [type]'. Yorum satırları (#, //, ;) atlanır.
    'NAME,1,9' gibi Türkçe ondalık-virgül satırları 1.9 olarak okunur."""
    ref = {}
    # utf-8-sig: BOM ilk parametre adına sızmasın
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(("#", "//", ";")):
                continue
            if "," in line and "\t" not in line:
                fields = [p.strip() for p in line.split(",")]
                if len(fields) == 3 and fields[1].lstrip("-").isdigit() \
                        and fields[2].isdigit():
                    fields = [fields[0], fields[1] + "." + fields[2]]
                elif len(fields) == 3 and fields[2] and \
                        set(fields[2]) <= set("0123456789eE+-."):
                    # "NAME,1,5E-3" gibi belirsiz satır: yanlış değer okumak
                    # yerine atla (sessiz 1.0 yorumu tehlikeli)
                    continue
                parts = [p for f2 in fields for p in f2.split() if p]
            else:
                parts = [p for p in line.replace("\t", " ").split() if p]
            if len(parts) >= 4 and parts[0].isdigit() and parts[1].isdigit():
                parts = parts[2:]          # QGC: sysid compid NAME VALUE [type]
            if len(parts) < 2:
                continue
            try:
                ref[parts[0].upper()] = float(parts[1])
            except ValueError:
                continue
    return ref


def param_diff(vehicle_params, ref):
    """(farklar, araçta olmayanlar, yoksayılan uçucu sayısı)."""
    diffs, missing, ignored = [], [], 0
    for name, rv in sorted(ref.items()):
        if is_volatile_param(name):
            ignored += 1
            continue
        if name not in vehicle_params:
            missing.append(name)
            continue
        vv = vehicle_params[name]
        if abs(vv - rv) > max(1e-4, abs(rv) * 1e-5):
            diffs.append({"name": name, "ref": rv, "cur": vv})
    return diffs, missing, ignored


def check_param_reference(ctx):
    g = "Parametre Referansı"
    if not os.path.exists(REF_PARAMS_PATH):
        ctx.add(g, "PRM-01", "Referans dosyası", SKIP,
                "ref_params.param yok — Param sekmesinden mevcut parametreleri "
                "referans olarak kaydedebilir ya da dosya yükleyebilirsiniz.")
        return
    try:
        ref = load_ref_params()
    except OSError as exc:
        ctx.add(g, "PRM-01", "Referans dosyası", WARN, "Okunamadı: %s" % exc)
        return
    if not ref:
        ctx.add(g, "PRM-01", "Referans dosyası", WARN,
                "Dosyada geçerli parametre satırı yok.")
        return
    if not ctx.params or not ctx.mav.param_fetch_done:
        # boş/yarım tabloya karşı sahte PASS verme
        ctx.add(g, "PRM-01", "Referans dosyası", SKIP,
                "Araç parametre tablosu eksik — karşılaştırma atlandı.")
        return
    diffs, missing, ignored = param_diff(ctx.params, ref)
    ctx.add(g, "PRM-01", "Referans dosyası", INFO,
            "%d parametre karşılaştırıldı%s." % (len(ref),
            (", %d uçucu yoksayıldı" % ignored) if ignored else ""))
    if not diffs:
        ctx.add(g, "PRM-02", "Parametre farkları", PASS,
                "Araç, referans dosyasıyla uyumlu.")
    else:
        sample = ", ".join("%s (ref %s ≠ araç %s)"
                           % (d["name"], _fmt(d["ref"], 4), _fmt(d["cur"], 4))
                           for d in diffs[:5])
        ctx.add(g, "PRM-02", "Parametre farkları", WARN,
                "%d fark: %s%s — tam liste Param sekmesinde."
                % (len(diffs), sample, " …" if len(diffs) > 5 else ""))
    if missing:
        ctx.add(g, "PRM-03", "Araçta olmayan parametreler", INFO,
                "%d referans parametresi araçta yok (firmware sürümü farklı "
                "olabilir): %s%s" % (len(missing), ", ".join(missing[:5]),
                                     " …" if len(missing) > 5 else ""))


def run_prearm(ctx, progress):
    """PRE-01: force the firmware's own prearm checks and collect verdicts."""
    g = "Otopilot Pre-Arm Sonucu"
    mav = ctx.mav
    progress("Otopilot pre-arm kontrolleri çalıştırılıyor...")
    mark = time.time() - 0.5
    acked, result = mav.run_command(401)  # MAV_CMD_RUN_PREARM_CHECKS
    if not acked or result == 3:  # UNSUPPORTED on old firmware
        # passive mode: ArduPilot rebroadcasts prearm failures every ~30 s
        ctx.add(g, "PRE-01", "Pre-arm tetikleme", INFO,
                "MAV_CMD_RUN_PREARM_CHECKS desteklenmiyor/yanıt yok; mevcut "
                "mesajlar pasif olarak değerlendirildi.")
        mark = time.time() - 35.0
        time.sleep(2.0)
    else:
        time.sleep(PREARM_WAIT)

    texts = mav.recent_statustexts(since=mark)
    prearm_msgs = []
    for (_, sev, text, _n) in texts:
        if text.startswith("PreArm:") or text.startswith("Arm:"):
            if text not in prearm_msgs:
                prearm_msgs.append(text)
    ctx.prearm_texts = prearm_msgs

    ss = mav.get_msg("SYS_STATUS", max_age=3)
    prearm_bit_ok = bool(ss and ss.onboard_control_sensors_health & 0x10000000)

    if not prearm_msgs and prearm_bit_ok:
        ctx.add(g, "PRE-02", "Otopilot pre-arm kararı", PASS,
                "Otopilot kendi tüm pre-arm kontrollerinden geçti — araç arm "
                "edilebilir durumda.")
    elif prearm_msgs:
        for i, msg in enumerate(prearm_msgs[:10]):
            ctx.add(g, "PRE-%02d" % (10 + i), "Otopilot mesajı", FAIL, msg)
        ctx.add(g, "PRE-02", "Otopilot pre-arm kararı", FAIL,
                "%d pre-arm hatası bildirildi. Not: Kontroller sırayla "
                "yapılır; bir hata giderildikten sonra yenisi görünebilir, "
                "düzeltip tekrar çalıştırın." % len(prearm_msgs))
    else:
        ctx.add(g, "PRE-02", "Otopilot pre-arm kararı", FAIL,
                "Pre-arm sağlık biti temizlenmemiş ama mesaj yakalanamadı; "
                "kontrolleri yeniden çalıştırın.")


# ===================================================================== #
# Engine entry points
# ===================================================================== #

GROUP_ORDER = [
    "Bağlantı / Sistem", "Otopilot Pre-Arm Sonucu", "Sensör Sağlığı",
    "EKF / Konum Kestirimi", "GPS", "Pusula", "İvmeölçer / Jiroskop",
    "Hava Hızı", "Batarya / Güç", "RC Kumanda", "Failsafe Parametreleri",
    "Arming Parametreleri", "Geofence", "Uçuş Zarfı Parametreleri",
    "Parametre Referansı", "Titreşim", "Kayıt (Logging)",
]


def run_all(mav, progress, options):
    ctx = Ctx(mav, options)

    # ensure params are loaded (needed by most checks)
    params_ok = mav.param_fetch_done
    if not params_ok:
        progress("Parametre listesi tamamlanıyor...")
        params_ok = mav.fetch_params(timeout=120)
        ctx.params = mav.params_snapshot()
    ctx.params_ok = params_ok

    # request one-shot messages
    mav.request_message(242)  # HOME_POSITION
    if mav.autopilot_version is None:
        mav.request_message(148)

    # gather telemetry window
    vib0 = mav.get_msg("VIBRATION")
    ctx.clip_before = (vib0.clipping_0, vib0.clipping_1, vib0.clipping_2) \
        if vib0 else None
    window_start = time.time()
    progress("Telemetri toplanıyor (%.0f sn)..." % GATHER_SECONDS)
    time.sleep(GATHER_SECONDS)
    vib1 = mav.get_msg("VIBRATION")
    ctx.clip_after = (vib1.clipping_0, vib1.clipping_1, vib1.clipping_2) \
        if vib1 else None
    ctx.params = mav.params_snapshot()

    # firmware's own prearm verdict (also feeds STATUSTEXT-based checks)
    run_prearm(ctx, progress)
    # Only evaluate STATUSTEXT from THIS run's window. The forced prearm run
    # makes ArduPilot re-emit every currently-failing PreArm message inside it,
    # so nothing current is lost — but stale 30-s rebroadcasts and transient
    # boot messages from minutes ago no longer cause false FAILs.
    ctx.window_texts = mav.recent_statustexts(since=window_start)
    # Persistent boot-time non-prearm messages, only for the CrashDump check.
    ctx.boot_texts = [t for t in mav.recent_statustexts(since=time.time() - 300)
                      if not (t[2].startswith("PreArm:")
                              or t[2].startswith("Arm:"))]

    progress("Kontroller değerlendiriliyor...")
    ok = check_connection(ctx)
    if ok:
        check_sensors(ctx)
        check_ekf(ctx)
        check_gps(ctx)
        check_compass(ctx)
        check_ins(ctx)
        check_airspeed(ctx)
        check_battery(ctx)
        check_rc(ctx)
        check_failsafe_params(ctx)
        check_arming_params(ctx)
        check_fence(ctx)
        check_flight_params(ctx)
        check_param_reference(ctx)
        check_vibration(ctx)
        check_logging(ctx)

    # group + summarize
    groups = {}
    for r in ctx.results:
        groups.setdefault(r["group"], []).append(r)
    ordered = [{"name": name, "checks": groups[name]}
               for name in GROUP_ORDER if name in groups]
    for name in groups:
        if name not in GROUP_ORDER:
            ordered.append({"name": name, "checks": groups[name]})

    counts = {s: 0 for s in (PASS, WARN, FAIL, INFO, SKIP)}
    for r in ctx.results:
        counts[r["status"]] += 1

    # attach a concrete remedy to every check and build a severity-sorted
    # problem list (the Status Center's focus: problem + how to fix it)
    from . import remedies
    problems = []
    for g in ordered:
        for c in g["checks"]:
            c["remedy"] = remedies.remedy_for(c)
            if c["status"] in (FAIL, WARN):
                problems.append({
                    "status": c["status"], "name": c["name"],
                    "detail": c["detail"], "remedy": c["remedy"],
                    "group": g["name"], "id": c["id"],
                })
    problems.sort(key=lambda p: 0 if p["status"] == FAIL else 1)

    info = vehicle_info(mav)
    return {
        "time": time.time(),
        "groups": ordered,
        "counts": counts,
        "verdict": FAIL if counts[FAIL] else (WARN if counts[WARN] else PASS),
        "vehicle": info,
        "prearm_texts": ctx.prearm_texts,
        "problems": problems,
    }


def vehicle_info(mav):
    hb = mav.get_msg("HEARTBEAT")
    ver = mav.autopilot_version
    info = {"sysid": mav.sysid}
    if hb:
        info["type"] = hb.type
        info["armed"] = bool(hb.base_mode & 128)
        try:
            info["mode"] = mavutil.mode_string_v10(hb)
        except Exception:
            info["mode"] = str(hb.custom_mode)
    if ver:
        sw = ver.flight_sw_version
        info["fw_version"] = "%d.%d.%d" % ((sw >> 24) & 0xFF, (sw >> 16) & 0xFF,
                                           (sw >> 8) & 0xFF)
    return info


def telemetry_summary(mav):
    """Live telemetry for the UI dashboard."""
    out = {}
    hb = mav.get_msg("HEARTBEAT", max_age=5)
    if hb:
        out["armed"] = bool(hb.base_mode & 128)
        try:
            out["mode"] = mavutil.mode_string_v10(hb)
        except Exception:
            out["mode"] = str(hb.custom_mode)
    gps = mav.get_msg("GPS_RAW_INT", max_age=5)
    if gps:
        out["gps"] = {
            "fix": gps.fix_type,
            "fix_name": GPS_FIX_NAMES.get(gps.fix_type, "?"),
            "sats": gps.satellites_visible,
            "hdop": (gps.eph / 100.0) if gps.eph != 65535 else None,
        }
    # Position + heading for the map (prefer fused GLOBAL_POSITION_INT)
    gpi = mav.get_msg("GLOBAL_POSITION_INT", max_age=5)
    if gpi and (gpi.lat or gpi.lon):
        out["pos"] = {
            "lat": gpi.lat / 1e7,
            "lon": gpi.lon / 1e7,
            "hdg": (gpi.hdg / 100.0) if gpi.hdg != 65535 else None,
            "rel_alt": gpi.relative_alt / 1000.0,
        }
    elif gps and gps.fix_type >= 2 and (gps.lat or gps.lon):
        out["pos"] = {
            "lat": gps.lat / 1e7,
            "lon": gps.lon / 1e7,
            "hdg": (gps.cog / 100.0) if getattr(gps, "cog", 65535) != 65535
            else None,
            "rel_alt": None,
        }
    home = mav.get_msg("HOME_POSITION")
    if home and (home.latitude or home.longitude):
        out["home"] = {"lat": home.latitude / 1e7, "lon": home.longitude / 1e7}
    # geofence (for the map circle): try new then legacy radius param name
    fence_en = mav.get_param("FENCE_ENABLE")
    if fence_en is not None:
        radius = mav.get_param("FENCE_RADIUS")
        out["fence"] = {"enabled": bool(fence_en),
                        "radius": radius if radius and radius > 0 else None,
                        "alt_max": mav.get_param("FENCE_ALT_MAX")}
    ss = mav.get_msg("SYS_STATUS", max_age=5)
    if ss:
        out["batt"] = {
            "volt": ss.voltage_battery / 1000.0 if ss.voltage_battery != 65535 else None,
            "current": ss.current_battery / 100.0 if ss.current_battery != -1 else None,
            "remaining": ss.battery_remaining if ss.battery_remaining != -1 else None,
        }
        out["prearm_ok"] = bool(ss.onboard_control_sensors_health & 0x10000000)
        out["prearm_enabled"] = bool(ss.onboard_control_sensors_enabled & 0x10000000)
    ekf = mav.get_msg("EKF_STATUS_REPORT", max_age=5)
    if ekf:
        out["ekf_ok"] = bool(ekf.flags & 1) and not (ekf.flags & 1024)
        out["ekf_var"] = {                      # canlı sağlık kartı için
            "vel": round(ekf.velocity_variance, 2),
            "pos_h": round(ekf.pos_horiz_variance, 2),
            "pos_v": round(ekf.pos_vert_variance, 2),
            "mag": round(ekf.compass_variance, 2),
        }
    vib = mav.get_msg("VIBRATION", max_age=5)
    if vib:
        out["vib"] = {
            "x": round(vib.vibration_x, 1), "y": round(vib.vibration_y, 1),
            "z": round(vib.vibration_z, 1),
            "clip": vib.clipping_0 + vib.clipping_1 + vib.clipping_2,
        }
    pw = mav.get_msg("POWER_STATUS", max_age=5)
    if pw and pw.Vcc:
        out["vcc"] = pw.Vcc / 1000.0
    rc = mav.get_msg("RC_CHANNELS", max_age=5)
    if rc:
        out["rc"] = {"chan": rc.chancount,
                     "rssi": rc.rssi if rc.rssi != 255 else None}
    att = mav.get_msg("ATTITUDE", max_age=5)
    if att:
        out["roll"] = round(math.degrees(att.roll), 1)
        out["pitch"] = round(math.degrees(att.pitch), 1)
    vfr = mav.get_msg("VFR_HUD", max_age=5)
    if vfr:
        out["airspeed"] = round(vfr.airspeed, 1)
        out["alt"] = round(vfr.alt, 1)
    return out
