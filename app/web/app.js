/* ArduCheck — status-first dashboard */
"use strict";

const $ = (id) => document.getElementById(id);
const api = {
  get: (p) => fetch(p).then((r) => r.json()),
  post: (p, b) => fetch(p, { method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify(b||{}) }).then((r) => r.json()),
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;" }[c]));

let connected = false;
let lastBusy = null;
let resultsShown = 0;
let resultsVerdict = null;
let checklistDef = null;
let manualState = JSON.parse(localStorage.getItem("arducheck_manual") || "{}");
let lastCalPhase = "idle";
let lastResults = null;     // son /api/results yükü (banner + bayatlık denetimi)
let serverBoot = null;      // sunucu süreç kimliği (yeniden başlatma sezimi)
let _prevConnected = null;
const tfmt = (t) => new Date(t * 1000).toLocaleTimeString("tr-TR");

/* ---- toast ---- */
let toastTimer = null;
function toast(msg, ms = 4200) {
  const el = $("toast"); el.textContent = msg; el.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.add("hidden"), ms);
}

/* ---- theme: kayıt yoksa sistem tercihi (güneşte açık tema) ---- */
const _savedTheme = localStorage.getItem("arducheck_theme");
if (_savedTheme ? _savedTheme === "light"
    : matchMedia("(prefers-color-scheme: light)").matches)
  document.body.classList.add("theme-light");
$("themeBtn").onclick = () => {
  document.body.classList.toggle("theme-light");
  localStorage.setItem("arducheck_theme", document.body.classList.contains("theme-light") ? "light" : "dark");
};

/* ---- tabs ---- */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.onclick = () => {
    const rail = btn.dataset.rail, tab = btn.dataset.tab;
    const pane = $("pane-"+tab);
    const wasActive = pane && pane.classList.contains("active");
    document.querySelectorAll(`.tab[data-rail="${rail}"]`).forEach((b) => b.classList.toggle("active", b === btn));
    ["durum","kalibrasyon","servo","param"].forEach((p) => { const el = $("pane-"+p); if (el) el.classList.toggle("active", p === tab); });
    if (tab === "servo" && !wasActive) loadServos();   // aktifken tekrar render etme
    if (tab === "param" && !wasActive) loadRefInfo();
  };
});

/* ---- connection ---- */
let activeCtab = "tcp";
document.querySelectorAll(".ctab").forEach((btn) => {
  btn.onclick = () => {
    activeCtab = btn.dataset.ctab;
    document.querySelectorAll(".ctab").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".cpane").forEach((p) => p.classList.toggle("hidden", p.id !== "cpane-"+activeCtab));
    if (activeCtab === "serial") refreshPorts();
  };
});
async function refreshPorts() {
  const data = await api.get("/api/ports");
  const sel = $("serialPort"); sel.innerHTML = "";
  if (!data.ports.length) { sel.innerHTML = "<option value=''>— port yok —</option>"; return; }
  for (const p of data.ports) {
    const o = document.createElement("option");
    o.value = p.device;
    o.textContent = p.device + (p.description ? " — "+p.description : "") + (p.likely_fc ? "  ✈" : "");
    sel.appendChild(o);
  }
}
$("refreshPorts").onclick = refreshPorts;
function connTarget() {
  if (activeCtab === "tcp") return "tcp:"+($("tcpHost").value||"127.0.0.1")+":"+($("tcpPort").value||"5760");
  if (activeCtab === "udp") return "udp:0.0.0.0:"+($("udpPort").value||"14550");
  const dev = $("serialPort").value;
  if (!dev) { toast("Seri port seçin."); return null; }
  return dev;
}
$("connectBtn").onclick = async () => {
  const target = connTarget(); if (!target) return;
  const r = await api.post("/api/connect", { target, baud: parseInt($("baud").value,10)||115200 });
  if (r.error) toast(r.error);
};
$("disconnectBtn").onclick = async () => {
  await api.post("/api/disconnect");
  resultsShown = 0; renderStatusCenter(null);
};

/* ---- MAP ---- */
let map, vehicle = null, trail = null, home = null, fence = null;
let osm, grid, offline = false, followVehicle = true, mapReady = false;
function initMap() {
  map = L.map("map", { zoomControl:true, attributionControl:true }).setView([39.0,35.0], 6);
  osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom:19, crossOrigin:true, errorTileUrl:"vendor/icons/tile-blank.png",
    attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });
  const GridLayer = L.GridLayer.extend({ createTile() {
    const t = document.createElement("canvas"), s = this.getTileSize();
    t.width = s.x; t.height = s.y; const x = t.getContext("2d");
    const cs = getComputedStyle(document.body);
    x.fillStyle = cs.getPropertyValue("--bg2").trim() || "#10151c"; x.fillRect(0,0,s.x,s.y);
    x.strokeStyle = cs.getPropertyValue("--grid").trim() || "#1d3b52";
    for (let i=0;i<=s.x;i+=32){x.beginPath();x.moveTo(i,0);x.lineTo(i,s.y);x.stroke();}
    for (let j=0;j<=s.y;j+=32){x.beginPath();x.moveTo(0,j);x.lineTo(s.x,j);x.stroke();}
    return t;
  }});
  grid = new GridLayer();
  osm.on("tileerror", () => { if (!offline) goOffline(true); });
  osm.addTo(map);
  trail = L.polyline([], { color:"#37b6ff", weight:3, opacity:.8 }).addTo(map);
  map.on("dragstart", () => { followVehicle = false; });
  mapReady = true;
}
let _resizeTimer = null;
function invalidateMapSoon() {
  if (!mapReady) return;
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(() => map.invalidateSize(), 220);
}
window.addEventListener("resize", invalidateMapSoon);
function goOffline(on) {
  offline = on; $("offlineNote").classList.toggle("hidden", !on);
  if (on) { if (map.hasLayer(osm)) map.removeLayer(osm); grid.addTo(map); }
  else { if (map.hasLayer(grid)) map.removeLayer(grid); osm.addTo(map); }
}
window.addEventListener("offline", () => mapReady && goOffline(true));
window.addEventListener("online", () => mapReady && goOffline(false));
const vehIcon = () => L.divIcon({ className:"veh-marker", iconSize:[34,34], iconAnchor:[17,17],
  html:'<img id="vehImg" src="vendor/icons/vehicle.svg" style="width:34px;height:34px;transform-origin:50% 50%">' });
function updateVehicle(pos) {
  if (!mapReady || !pos || pos.lat == null) return;
  const ll = [pos.lat, pos.lon];
  if (!vehicle) { vehicle = L.marker(ll, { icon:vehIcon(), zIndexOffset:1000 }).addTo(map); map.setView(ll, 16); }
  else vehicle.setLatLng(ll);
  const img = $("vehImg");
  if (img && pos.hdg != null) img.style.transform = "rotate("+pos.hdg+"deg)";
  const pts = trail.getLatLngs();
  if (!pts.length || map.distance(pts[pts.length-1], ll) > 1) {
    trail.addLatLng(ll);
    if (trail.getLatLngs().length > 800) trail.setLatLngs(trail.getLatLngs().slice(-800));
  }
  if (followVehicle) map.panTo(ll, { animate:true, duration:.5 });
}
function updateHomeFence(h, fenceInfo) {
  if (!mapReady || !h) return;
  const ll = [h.lat, h.lon];
  if (!home) home = L.marker(ll, { title:"Ev (Home)" }).addTo(map); else home.setLatLng(ll);
  const r = (fenceInfo && fenceInfo.radius) ? fenceInfo.radius : 0;
  if (r > 0) {
    const on = fenceInfo.enabled;
    if (!fence) fence = L.circle(ll, { radius:r }).addTo(map);
    else { fence.setLatLng(ll); fence.setRadius(r); }
    fence.setStyle({ color: on ? "#ff6b6b" : "#7f8c8d", weight:2, fillOpacity:.05, dashArray: on ? null : "5,6" });
    const tip = `Geofence: ${Math.round(r)} m yarıçap` +
      (fenceInfo.alt_max ? ` · azami irtifa ${Math.round(fenceInfo.alt_max)} m` : "") +
      (on ? "" : " (devre dışı)");
    if (fence._tipTxt !== tip) { fence._tipTxt = tip; fence.bindTooltip(tip); }
  } else if (fence) { map.removeLayer(fence); fence = null; }
}
$("recenterBtn").onclick = () => { followVehicle = true; if (vehicle) map.setView(vehicle.getLatLng(), 16); };
$("trailClearBtn").onclick = () => trail && trail.setLatLngs([]);

/* map size control */
let mapSize = localStorage.getItem("arducheck_mapsize") || "orta";
function applyMapSize(sz) {
  mapSize = sz; $("mapWidget").dataset.size = sz;
  document.querySelectorAll(".mw-sizes button").forEach((b) => b.classList.toggle("active", b.dataset.size === sz));
  localStorage.setItem("arducheck_mapsize", sz);
  invalidateMapSoon();
}
document.querySelectorAll(".mw-sizes button").forEach((b) => b.onclick = () => applyMapSize(b.dataset.size));

/* ---- HUD ---- */
function updateHUD(t) {
  const ppd = 2.7, roll = t.roll||0, pitch = t.pitch||0;
  const hdg = (t.pos && t.pos.hdg != null) ? t.pos.hdg : 0;
  $("hudWorld").setAttribute("transform", `rotate(${roll}) translate(0 ${pitch*ppd})`);
  $("hudRose").setAttribute("transform", `rotate(${-hdg})`);
  $("hudHdg").textContent = Math.round(hdg)+"°";
}
function updateReadout(t) {
  const rows = [];
  if (t.airspeed != null) rows.push(["Hız", t.airspeed.toFixed(1)+" m/s"]);
  if (t.alt != null) rows.push(["İrtifa", t.alt.toFixed(0)+" m"]);
  if (t.pos && t.pos.rel_alt != null) rows.push(["Röl.", t.pos.rel_alt.toFixed(0)+" m"]);
  $("readout").innerHTML = rows.map(([k,v]) => `<div class="ro"><span>${k}</span><b>${v}</b></div>`).join("");
}

/* ---- top bar + tiles ---- */
function tile(k, v, cls="") { return `<div class="tile ${cls}"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`; }
function renderTiles(t, link) {
  const out = [];
  out.push(tile("Uçuş Modu", t.mode ?? "—"));
  out.push(tile("Arm Durumu", t.armed ? "ARMLI!" : "Disarm", t.armed ? "bad" : "ok"));
  if ("prearm_ok" in t) out.push(tile("Pre-Arm", t.prearm_ok ? "Hazır" : "Geçmiyor", t.prearm_ok ? "ok":"bad"));
  if ("ekf_ok" in t) out.push(tile("EKF", t.ekf_ok ? "Sağlıklı" : "Hazır değil", t.ekf_ok ? "ok":"mid"));
  if (t.gps) { const c = t.gps.fix>=3?"ok":(t.gps.fix>=2?"mid":"bad");
    out.push(tile("GPS", `${t.gps.fix_name} · ${t.gps.sats}`, c));
    if (t.gps.hdop != null) out.push(tile("HDOP", t.gps.hdop.toFixed(2), t.gps.hdop<=1.4?"ok":(t.gps.hdop<=2?"mid":"bad"))); }
  if (t.batt && t.batt.volt != null) {
    let v = t.batt.volt.toFixed(2)+" V";
    if (t.batt.current != null) v += ` · ${t.batt.current.toFixed(1)} A`;
    if (t.batt.remaining != null) v += ` · %${t.batt.remaining}`;
    out.push(tile("Batarya", v, t.batt.remaining!=null ? (t.batt.remaining>=50?"ok":"bad"):"")); }
  if (t.airspeed != null) out.push(tile("Hava Hızı", t.airspeed.toFixed(1)+" m/s"));
  if (t.roll != null) out.push(tile("Yatış/Yunus.", `${t.roll}° / ${t.pitch}°`));
  if (link.msg_rate != null) out.push(tile("Telemetri", link.msg_rate+" msj/sn"));
  $("tiles").innerHTML = out.join("");
}
function setInd(id, val, cls="") {
  const el = $(id); el.querySelector(".v").textContent = val;
  el.classList.remove("ok","mid","bad"); if (cls) el.classList.add(cls);
}
/* ---- go/no-go: tek yetkili karar (pill ve banner aynı karardan beslenir) ----
   UÇUŞA HAZIR ancak şunların TÜMÜ ile: taze PASS koşusu + manuel liste tam +
   canlı prearm_ok. report.py'deki overall_ready mantığının canlı karşılığı. */
const RESULT_STALE_S = 15 * 60;
const MAV_TYPE_NAMES = { 1:"ArduPlane", 2:"ArduCopter", 13:"ArduCopter",
  14:"ArduCopter", 10:"ArduRover", 12:"ArduSub", 19:"ArduPlane VTOL",
  20:"ArduPlane VTOL", 21:"ArduPlane VTOL" };
function resultsAge() {
  return (lastResults && lastResults.time) ? (Date.now()/1000 - lastResults.time) : null;
}
function computeVerdict(st) {
  const t = st.telemetry || {};
  if (!connected)
    return st.busy === "connect"
      ? { cls:"caution", txt:"● BAĞLANIYOR…", reason:"connect" }
      : { cls:"disconnected", txt:"● BAĞLI DEĞİL", reason:"offline" };
  if (t.armed) return { cls:"armed", txt:"⚠ ARMLI", reason:"armed" };
  if (t.prearm_enabled === false)
    return { cls:"notready", txt:"✘ PRE-ARM KONTROLLERİ KAPALI", reason:"prearm-disabled" };
  if (st.busy === "checks")
    return { cls:"caution", txt:"● KONTROLLER ÇALIŞIYOR…", reason:"running" };
  const pp = st.param_progress;
  // param indirme yalnız connect-busy sırasında sürer; busy bittiyse ve hâlâ
  // done değilse indirme eksik bitmiştir — sonsuza dek "indiriliyor" deme
  if (st.busy === "connect" && pp && !pp.done)
    return { cls:"caution", txt:`● PARAMETRELER ${pp.got}${pp.total ? "/"+pp.total : ""}`, reason:"params" };
  if (resultsVerdict === "FAIL")
    return { cls:"notready", txt:"✘ HAZIR DEĞİL", reason:"fail" };
  if (t.prearm_ok === false)
    return { cls:"notready", txt:"✘ HAZIR DEĞİL", reason:"prearm" };
  if (!resultsVerdict) return { cls:"caution", txt:"● KONTROL BEKLİYOR", reason:"none" };
  const age = resultsAge();
  if (age != null && age > RESULT_STALE_S)
    return { cls:"caution", txt:"⚠ KONTROLLER ESKİDİ", reason:"stale" };
  if (resultsVerdict === "WARN") return { cls:"caution", txt:"⚠ DİKKAT", reason:"warn" };
  const mp = manualProgress();   // null = tanım henüz yüklenmedi → hazır deme
  if (!mp) return { cls:"caution", txt:"● KONTROL BEKLİYOR", reason:"none" };
  if (mp.done < mp.total)
    return { cls:"caution", txt:`⚠ MANUEL LİSTE ${mp.done}/${mp.total}`, reason:"manual" };
  if (t.prearm_ok !== true)
    return { cls:"caution", txt:"● PRE-ARM BEKLENİYOR", reason:"prearm-wait" };
  return { cls:"ready", txt:"✔ UÇUŞA HAZIR", reason:"ready" };
}

function renderTopbar(st) {
  const t = st.telemetry || {}, link = st.link || {};
  const v = computeVerdict(st);
  const pill = $("verdictPill");
  pill.className = "verdict-pill " + v.cls; pill.textContent = v.txt;
  const veh = st.vehicle || {};
  if (connected && (veh.fw_version || veh.sysid != null)) {
    const name = MAV_TYPE_NAMES[veh.type] || "Araç";
    setInd("tbVeh", name + (veh.fw_version ? " v"+veh.fw_version : "") +
      " · #" + (veh.sysid ?? "?"));
    $("tbVeh").title = "Araç kimliği" + (link.target ? " · " + link.target : "");
  } else setInd("tbVeh", "—");
  setInd("tbMode", t.mode ?? "—", t.armed ? "bad" : "");
  if (t.gps) setInd("tbGps", `${t.gps.fix_name} · ${t.gps.sats}`, t.gps.fix>=3?"ok":(t.gps.fix>=2?"mid":"bad")); else setInd("tbGps","—");
  if (t.batt && t.batt.volt != null) setInd("tbBatt",
    t.batt.volt.toFixed(1)+"V"+(t.batt.remaining!=null?` ${t.batt.remaining}%`:""),
    t.batt.remaining!=null ? (t.batt.remaining>=50?"ok":"bad"):""); else setInd("tbBatt","—");
  setInd("tbLink", connected ? (link.msg_rate||0)+"/sn" : "—",
    connected ? (link.heartbeat_age!=null && link.heartbeat_age<3?"ok":"mid") : "bad");
}
/* ---- STATUS CENTER (problem + remedy) ---- */
const STATUS_TR = { PASS:"GEÇTİ", WARN:"UYARI", FAIL:"HATA", INFO:"BİLGİ", SKIP:"ATLANDI" };
function renderCatalog(groups) {
  if (!groups || !groups.length) return "";
  return `<div class="chk-catalog">` + groups.map((g) => {
    const n = { PASS:0, WARN:0, FAIL:0, INFO:0, SKIP:0 };
    g.checks.forEach((c) => { n[c.status] = (n[c.status]||0)+1; });
    const cnt =
      (n.PASS ? `<span class="gp">✓ ${n.PASS}</span>` : "") +
      (n.WARN ? `<span class="gw">⚠ ${n.WARN}</span>` : "") +
      (n.FAIL ? `<span class="gf">✘ ${n.FAIL}</span>` : "") +
      (n.INFO + n.SKIP ? `<span>${n.INFO + n.SKIP}</span>` : "");
    const rows = g.checks.map((c) =>
      `<div class="chk-row"><span class="st ${esc(c.status)}">${esc(STATUS_TR[c.status]||c.status)}</span><span class="nm">${esc(c.name)}</span><span class="dt">${esc(c.detail||"")}</span></div>`).join("");
    return `<details class="chk-group"${(n.FAIL||n.WARN)?" open":""}><summary>${esc(g.name)}<span class="gc">${cnt}</span></summary>${rows}</details>`;
  }).join("") + `</div>`;
}
let shownFails = 0, shownWarns = 0;   // banner ipucu için (PRE-1x süzülmüş)
let prevProblemKeys = null;           // koşular arası fark için önceki sorun id'leri
function renderStatusCenter(res) {
  const list = $("problemList");
  lastResults = res;
  if (!res || res.error) {
    resultsVerdict = res && res.error ? "FAIL" : null;
    shownFails = 0; shownWarns = 0;
    if (!res) prevProblemKeys = null;   // yeni oturum: diff tabanı sıfırlanır
    list.innerHTML = "";
    if (res && res.error) $("checkStatus").textContent = "";
    return;
  }
  resultsVerdict = res.verdict;
  // PRE-1x kartları atla: aynı mesajlar zaten üstteki canlı pre-arm bloğunda
  const problems = (res.problems || []).filter((p) =>
    !(p.group === "Otopilot Pre-Arm Sonucu" && /^PRE-1\d$/.test(p.id)));
  const c = res.counts || {};
  shownFails = problems.filter((p)=>p.status==="FAIL").length;
  shownWarns = problems.filter((p)=>p.status==="WARN").length;
  // koşular arası fark: çözüldü / yeni / sürüyor — badge() render sırasında
  // çağrıldığından önceki anahtarların YEREL kopyası kullanılır (closure
  // modül değişkenini okusa yeniden atamadan sonra hep "sürüyor" derdi)
  const prevKeys = prevProblemKeys;
  const curKeys = new Set(problems.map((p) => p.id));
  let diffHtml = "";
  const badge = (p) => !prevKeys ? "" :
    (prevKeys.has(p.id) ? '<span class="pc-badge persist">SÜRÜYOR</span>'
                        : '<span class="pc-badge new">YENİ</span>');
  if (prevKeys) {
    const resolved = [...prevKeys].filter((k) => !curKeys.has(k)).length;
    const fresh = problems.filter((p) => !prevKeys.has(p.id)).length;
    if (resolved || fresh)
      diffHtml = `<div class="diff-note">Önceki koşuya göre: <b class="res">✓ ${resolved} çözüldü</b> · ` +
        `<b class="new">${fresh} yeni</b> · ${problems.length - fresh} sürüyor</div>`;
  }
  prevProblemKeys = curKeys;
  let html = diffHtml + `<div class="sc-counts">` +
    [["PASS","Geçti","--pass"],["WARN","Uyarı","--warn"],["FAIL","Hata","--fail"],["INFO","Bilgi","--info"],["SKIP","Atlandı","--skip"]]
      .map(([k,l,v]) => `<div class="sumbox"><b style="color:var(${v})">${c[k]||0}</b><span>${l}</span></div>`).join("") + `</div>`;
  html += problems.map((p) => `
    <div class="problem-card ${p.status}">
      <div class="pc-icon">${p.status==="FAIL"?"✘":"⚠"}</div>
      <div class="pc-body">
        <div class="pc-title">${esc(p.name)} <span class="pc-tag">${esc(p.group)}</span> ${badge(p)}</div>
        <div class="pc-detail">${esc(p.detail)}</div>
        ${p.remedy ? `<div class="pc-remedy"><b>Çözüm:</b> ${esc(p.remedy)}</div>` : ""}
      </div>
    </div>`).join("");
  html += renderCatalog(res.groups);
  list.innerHTML = html;
  const ts = new Date(res.time*1000).toLocaleTimeString("tr-TR");
  $("checkStatus").textContent = "Son çalıştırma: "+ts;
}

/* ---- banner: poll-güdümlü, computeVerdict + kompozit özet ---- */
let _bannerCls = "", _bannerHTML = "";
function setBanner(cls, html) {
  if (cls === _bannerCls && html === _bannerHTML) return;
  _bannerCls = cls; _bannerHTML = html;
  const b = $("statusBanner");
  b.className = "status-banner " + cls;
  b.innerHTML = html;
}
function compositeLine(st) {
  const t = st.telemetry || {};
  const parts = [];
  if (lastResults && !lastResults.error)
    parts.push("Otomatik: " + (lastResults.verdict === "PASS" ? "✓"
      : (STATUS_TR[lastResults.verdict] || lastResults.verdict)) +
      " " + tfmt(lastResults.time));
  const mp = manualProgress();
  if (mp) parts.push(`Manuel: ${mp.done}/${mp.total}`);
  if ("prearm_ok" in t) parts.push("Pre-arm: " + (t.prearm_ok ? "✓" : "✘"));
  const pp = st.param_progress;
  if (pp && !pp.done && !st.busy)
    parts.push(`Parametreler: eksik (${pp.got}${pp.total ? "/"+pp.total : ""})`);
  if (t.batt && t.batt.volt != null) parts.push("Batarya: " + t.batt.volt.toFixed(1) + "V");
  if (t.gps) parts.push(`GPS: ${t.gps.fix_name} · ${t.gps.sats} uydu`);
  return parts.join("  ·  ");
}
const BANNER_HINTS = {
  "armed": "Araç arm edilmiş — kontroller ve kalibrasyon devre dışı.",
  "prearm-disabled": "ARMING_CHECK kapalı görünüyor — pre-arm korumasız uçuş önerilmez.",
  "prearm": "Otopilot pre-arm geçmiyor — engeller yukarıdaki canlı blokta.",
  "prearm-wait": "Otomatik ve manuel kontroller tamam — otopilot pre-arm bitinin oturması bekleniyor.",
  "manual": "Otomatik kontroller geçti — sağdaki manuel listeyi tamamlayın.",
  "ready": "Otomatik kontroller, manuel liste ve canlı pre-arm tamam.",
};
const CHECKS_EST_MS = 13000;   // 6 sn toplama + 2 sn + ~4 sn prearm penceresi
let checksStart = 0;
function renderBanner(st) {
  if (st.busy === "checks") {
    const pct = checksStart ?
      Math.min(94, Math.round((Date.now() - checksStart) / CHECKS_EST_MS * 100)) : 5;
    setBanner("running", '<span class="inline-spin"></span> ' +
      esc(st.progress || "Kontroller çalışıyor…") +
      `<div class="progress-wrap"><div class="progress-bar" style="width:${pct}%"></div></div>` +
      "<small>Sonuçlar koşu bitince güncellenecek.</small>");
    return;
  }
  const pp = st.param_progress;
  if (st.busy === "connect" && pp && !pp.done) {
    setBanner("running", '<span class="inline-spin"></span> Parametreler indiriliyor… ' +
      esc(`${pp.got}${pp.total ? "/"+pp.total : ""}`) +
      "<small>Kontroller, parametre indirme bitince çalıştırılabilir.</small>");
    return;
  }
  if (lastResults && lastResults.error) {
    setBanner("fail", "✘ Kontroller çalıştırılamadı<small>" + esc(lastResults.error) + "</small>");
    return;
  }
  if (!lastResults) {
    setBanner("idle", "Aracın uçuşa hazır olup olmadığını görmek için kontrolleri çalıştırın.");
    return;
  }
  const v = computeVerdict(st);
  const cls = v.cls === "ready" ? "ok" : (v.cls === "caution" ? "caution" : "fail");
  let hint = BANNER_HINTS[v.reason] || "";
  if (v.reason === "fail")
    hint = `${shownFails} sorun ve çözümleri aşağıda — düzeltip tekrar çalıştırın.`;
  else if (v.reason === "stale")
    hint = "Son koşu " + tfmt(lastResults.time) + " — kontrolleri yeniden çalıştırın.";
  else if (v.reason === "warn")
    hint = `${shownWarns} uyarı aşağıda; kritik hata yok.`;
  const rerun = (v.reason === "fail" || v.reason === "stale" || v.reason === "warn")
    ? ' <button id="bannerRerun" class="banner-btn">↻ Tekrar Çalıştır</button>' : "";
  setBanner(cls, esc(v.txt) + rerun + "<small>" + esc(hint ? hint + "  ·  " : "") +
    esc(compositeLine(st)) + "</small>");
}
// banner her poll yeniden kurulabildiğinden buton tek delegasyonla bağlanır
$("statusBanner").addEventListener("click", (e) => {
  if (e.target.id === "bannerRerun" && !$("runBtn").disabled) $("runBtn").click();
});

/* ---- canlı sağlık kartı: titreşim / EKF varyans / kart 5V / RC ----
   Eşikler checks.py ile aynı (VIB-01 30/60, EKF-04 0.5/0.8, BAT-08 4.8-5.4) */
function renderHealth(t) {
  const el = $("healthCard");
  const items = [];
  if (t.vib) {
    const mx = Math.max(t.vib.x, t.vib.y, t.vib.z);
    items.push({ k:"Titreşim", v:mx.toFixed(1)+" m/s²",
      cls: mx<30 ? "ok" : (mx<=60 ? "mid" : "bad"),
      sub:`x ${t.vib.x} · y ${t.vib.y} · z ${t.vib.z}` + (t.vib.clip ? ` · clip ${t.vib.clip}` : "") });
  }
  if (t.ekf_var) {
    const worst = Math.max(t.ekf_var.vel, t.ekf_var.pos_h, t.ekf_var.pos_v, t.ekf_var.mag);
    items.push({ k:"EKF varyans", v:worst.toFixed(2),
      cls: worst<0.5 ? "ok" : (worst<0.8 ? "mid" : "bad"),
      sub:`hız ${t.ekf_var.vel} · konum ${t.ekf_var.pos_h}/${t.ekf_var.pos_v} · pusula ${t.ekf_var.mag}` });
  }
  if (t.vcc != null) items.push({ k:"Kart 5V", v:t.vcc.toFixed(2)+" V",
    cls:(t.vcc>=4.8 && t.vcc<=5.4) ? "ok" : ((t.vcc>=4.3 && t.vcc<=5.8) ? "mid" : "bad"),
    sub:"ideal 4.8–5.4 V" });
  if (t.rc) items.push({ k:"RC Sinyali", v: t.rc.chan ? t.rc.chan+" kanal" : "yok",
    cls: t.rc.chan > 0 ? "ok" : "bad",
    sub: t.rc.rssi != null ? `RSSI ${t.rc.rssi}/254` : "" });
  if (!items.length) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden");
  const html = items.map((i) =>
    `<div class="htile ${i.cls}"><div class="k">${i.k}</div><div class="v">${esc(i.v)}</div>` +
    (i.sub ? `<small>${esc(i.sub)}</small>` : "") + `</div>`).join("");
  if (html !== el._lastHtml) { el._lastHtml = html; el.innerHTML = html; }
}

/* ---- canlı pre-arm bloğu: 1 Hz akıştan süzülen PreArm:/Arm: mesajları;
   prearm_ok biti yeşile dönünce kendiliğinden temizlenir ---- */
function renderPrearmLive(t) {
  const el = $("prearmLive");
  const now = Date.now() / 1000;
  for (const k in livePrearm) if (now - livePrearm[k] > 90) delete livePrearm[k];
  if (t.prearm_ok === true) {
    livePrearm = {};
    el.className = "prearm-block ok";
    el.innerHTML = '<div class="pb-head">✓ Otopilot pre-arm: geçiyor</div>' +
      '<div class="hint">Otopilotun kendi pre-arm kontrolleri canlı olarak temiz — araç arm edilebilir.</div>';
    return;
  }
  const texts = Object.keys(livePrearm);
  if (!texts.length && t.prearm_ok !== false) { el.className = "prearm-block hidden"; return; }
  el.className = "prearm-block bad";
  el.innerHTML = `<div class="pb-head">⛔ Otopilot pre-arm engelleri${texts.length ? " ("+texts.length+")" : ""}</div>` +
    (texts.length
      ? `<div class="pb-lines">${texts.map((x) => `<div>${esc(x)}</div>`).join("")}</div>`
      : '<div class="hint">Pre-arm geçmiyor — otopilot mesajı bekleniyor (≤30 sn); kontrolleri çalıştırmak yeniden tetikler.</div>');
}

$("runBtn").onclick = async () => { const r = await api.post("/api/run_checks"); if (r.error) toast(r.error); };
$("reportBtn").onclick = async () => {
  await api.post("/api/checklist", { items: manualState });
  let resp; try { resp = await fetch("/api/report"); } catch { toast("Rapor alınamadı."); return; }
  if (!resp.ok) { let m="Rapor alınamadı."; try { m=(await resp.json()).error||m; } catch{} toast(m); return; }
  const url = URL.createObjectURL(await resp.blob());
  const a = document.createElement("a"); a.href=url; a.download="arducheck-rapor.html";
  document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(url), 1000);
};

/* ---- manual checklist ---- */
function manualProgress() {
  if (!checklistDef) return null;
  let total = 0, done = 0;
  for (const sec of checklistDef) for (const it of sec.items) { total++; if (manualState[it.id]) done++; }
  return { total, done };
}
let _chkDefLoading = false;
async function renderChecklist() {
  if (!checklistDef) {
    if (_chkDefLoading) return;
    _chkDefLoading = true;
    try { checklistDef = (await api.get("/api/checklist_def")).sections; }
    catch { return; }
    finally { _chkDefLoading = false; }
  }
  const wrap = $("manualList"); wrap.innerHTML = "";
  for (const sec of checklistDef) {
    const h = document.createElement("div"); h.className = "man-section"; h.textContent = sec.section; wrap.appendChild(h);
    for (const it of sec.items) {
      const chk = !!manualState[it.id];
      const d = document.createElement("div"); d.className = "man-item"+(chk?" done":"");
      d.innerHTML = `<input type="checkbox" id="cb-${it.id}" ${chk?"checked":""}><label for="cb-${it.id}">${esc(it.text)}</label>`;
      d.querySelector("input").onchange = (e) => {
        // yalnız değişen satır güncellenir — tam render scroll'u sıçratıyordu
        manualState[it.id] = e.target.checked;
        saveManual();
        d.classList.toggle("done", e.target.checked);
        updateManualCount();
        syncPrePower();
      };
      wrap.appendChild(d);
    }
  }
  updateManualCount();
  renderPrePower();
}
function saveManual() {
  localStorage.setItem("arducheck_manual", JSON.stringify(manualState));
  api.post("/api/checklist", { items: manualState });
}
function updateManualCount() {
  const mp = manualProgress();
  if (mp) $("manualCount").textContent = `${mp.done}/${mp.total}`;
}

/* bağlantı kartındaki "güç vermeden önce" mini listesi (aynı state) */
function renderPrePower() {
  if (!checklistDef) return;
  const sec = checklistDef.find((s) => /güç vermeden/i.test(s.section));
  if (!sec) return;
  const wrap = $("prePowerList"); wrap.innerHTML = "";
  for (const it of sec.items) {
    const chk = !!manualState[it.id];
    const d = document.createElement("div"); d.className = "man-item"+(chk?" done":"");
    d.innerHTML = `<input type="checkbox" id="pp-${it.id}" ${chk?"checked":""}><label for="pp-${it.id}">${esc(it.text)}</label>`;
    d.querySelector("input").onchange = (e) => {
      manualState[it.id] = e.target.checked;
      saveManual();
      d.classList.toggle("done", e.target.checked);
      const main = $("cb-"+it.id);
      if (main) { main.checked = e.target.checked;
        main.closest(".man-item").classList.toggle("done", e.target.checked); }
      updateManualCount();
    };
    wrap.appendChild(d);
  }
}
function syncPrePower() {
  document.querySelectorAll("#prePowerList input").forEach((inp) => {
    const id = inp.id.slice(3);
    inp.checked = !!manualState[id];
    inp.closest(".man-item").classList.toggle("done", inp.checked);
  });
}

/* ---- onay modalı (native confirm yerine); istekler sıralanır — ikinci
   soru açık olanın sözünü gömmesin, soruyu altından değiştirmesin ---- */
let _confirmResolve = null;
let _confirmQueue = Promise.resolve();
function askConfirm(title, body) {
  const show = () => new Promise((resolve) => {
    $("confirmTitle").textContent = title;
    $("confirmBody").textContent = body;
    $("confirmModal").classList.remove("hidden");
    // çift tıklama korunması: kuyruktaki soru boyanmadan cevaplanamasın
    ["confirmYes","confirmNo","confirmX"].forEach((id) => { $(id).disabled = true; });
    setTimeout(() => ["confirmYes","confirmNo","confirmX"].forEach((id) => { $(id).disabled = false; }), 350);
    _confirmResolve = resolve;
  });
  const p = _confirmQueue.then(show);
  _confirmQueue = p.then(() => {}, () => {});
  return p;
}
function _closeConfirm(val) {
  $("confirmModal").classList.add("hidden");
  if (_confirmResolve) { _confirmResolve(val); _confirmResolve = null; }
}
$("confirmYes").onclick = () => _closeConfirm(true);
$("confirmNo").onclick = () => _closeConfirm(false);
$("confirmX").onclick = () => _closeConfirm(false);

$("manualReset").onclick = async () => {
  if (!await askConfirm("Listeyi sıfırla",
    "Manuel kontrol listesindeki tüm işaretler silinsin mi?")) return;
  manualState = {}; saveManual(); renderChecklist();
};

/* yeni bağlantıda uçuş-başına bölümlerin sıfırlanmasını öner —
   dünün işaretlerinin bugüne taşınması klasik checklist-rehavet tehlikesi */
let _resetAsked = false;
let _resetGen = 0;
async function suggestPerFlightReset() {
  if (_resetAsked) return;
  _resetAsked = true;
  const gen = ++_resetGen;
  // tanım henüz inmediyse kısa süre bekle (poll arka planda yüklemeyi dener)
  for (let i = 0; i < 10 && !checklistDef; i++)
    await new Promise((r) => setTimeout(r, 1000));
  // bu çağrı beklerken yeni bağlantı turu başladıysa ya da link koptuysa sus
  if (!checklistDef || gen !== _resetGen || !connected) return;
  const ids = checklistDef.filter((s) => /her uçuş/i.test(s.section))
    .flatMap((s) => s.items.map((i) => i.id)).filter((id) => manualState[id]);
  if (!ids.length) return;
  if (await askConfirm("Yeni bağlantı",
    "Uçuş-başına maddeler (kontrol yüzeyleri) önceki oturumdan işaretli duruyor. " +
    "Yeni uçuş için sıfırlansın mı?")) {
    ids.forEach((id) => { manualState[id] = false; });
    saveManual(); renderChecklist();
  }
}

/* ---- message panel size presets ---- */
let msgSize = localStorage.getItem("arducheck_msgsize") || "yarim";
function applyMsgSize(sz) {
  msgSize = sz; $("msgPanel").dataset.size = sz;
  document.querySelectorAll(".mp-sizes button").forEach((b) => b.classList.toggle("active", b.dataset.size === sz));
  localStorage.setItem("arducheck_msgsize", sz);
  if (sz !== "kapali") { const w = $("messages"); w.scrollTop = w.scrollHeight; }
  else { newSince = 0; $("newMsgs").classList.add("hidden"); }  // pil toolbar'ı örtmesin
}
document.querySelectorAll(".mp-sizes button").forEach((b) => b.onclick = () => applyMsgSize(b.dataset.size));

/* ---- mesaj araçları: severity filtreleri, arama, duraklat, dışa aktar ---- */
let msgFilters = JSON.parse(localStorage.getItem("arducheck_msgfilters") ||
  '{"err":true,"warn":true,"info":true}');
let msgQuery = "";
let msgPaused = false;
let newSince = 0;
const sevCat = (sev) => (sev <= 3 ? "err" : (sev === 4 ? "warn" : "info"));
function applyMsgFilterTo(n) {
  const show = msgFilters[sevCat(Number(n.dataset.sev))] &&
    (!msgQuery || n.dataset.text.toLowerCase().includes(msgQuery));
  n.classList.toggle("f-hidden", !show);
}
function refilterAll() { $("messages").querySelectorAll(".msg").forEach(applyMsgFilterTo); }
document.querySelectorAll(".mft").forEach((b) => {
  b.classList.toggle("active", !!msgFilters[b.dataset.f]);
  b.onclick = () => {
    msgFilters[b.dataset.f] = !msgFilters[b.dataset.f];
    b.classList.toggle("active", msgFilters[b.dataset.f]);
    localStorage.setItem("arducheck_msgfilters", JSON.stringify(msgFilters));
    refilterAll();
  };
});
let _searchTimer = null;
$("msgSearch").oninput = (e) => {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => {
    msgQuery = e.target.value.trim().toLowerCase(); refilterAll();
  }, 150);
};
function jumpToNewest() {
  const w = $("messages"); w.scrollTop = w.scrollHeight;
  newSince = 0; $("newMsgs").classList.add("hidden");
}
function setPaused(v) {
  msgPaused = v;
  $("msgPause").classList.toggle("active", v);
  $("msgPause").title = v ? "Otomatik kaydırmayı sürdür" : "Otomatik kaydırmayı duraklat";
}
$("msgPause").onclick = () => { setPaused(!msgPaused); if (!msgPaused) jumpToNewest(); };
$("newMsgs").onclick = () => { setPaused(false); jumpToNewest(); };
$("messages").addEventListener("scroll", () => {
  const w = $("messages");
  if (!msgPaused && w.scrollTop + w.clientHeight >= w.scrollHeight - 20) {
    newSince = 0; $("newMsgs").classList.add("hidden");
  }
});
function exportLines() {
  return [...$("messages").querySelectorAll(".msg")].map((n) =>
    tfmt(Number(n.dataset.t)) + " " +
    n.querySelector(".sv").textContent.padEnd(9) + " " + n.dataset.text +
    (Number(n.dataset.count) > 1 ? ` ×${n.dataset.count}` : "")).join("\n");
}
$("msgCopy").onclick = async () => {
  try { await navigator.clipboard.writeText(exportLines()); toast("Mesajlar panoya kopyalandı."); }
  catch { toast("Kopyalanamadı — tarayıcı pano izni vermedi."); }
};
$("msgSave").onclick = () => {
  const url = URL.createObjectURL(new Blob([exportLines()], { type:"text/plain;charset=utf-8" }));
  const a = document.createElement("a"); a.href = url; a.download = "arducheck-mesajlar.txt";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
$("msgClear").onclick = () => {
  // sunucu sayaç tabanlarını sakla: temizlik sonrası ilk tekrar, temizlik
  // öncesi kümülatif ×N ile değil o günden bu yana sayımla görünsün
  msgIndex.forEach((n, k) => clearedSrv.set(k, Number(n.dataset.srv) || 0));
  $("messages").innerHTML = ""; msgIndex.clear(); lastNode = null;
  newSince = 0; $("newMsgs").classList.add("hidden");
  $("msgCount").textContent = "0";
  toast("Görünüm temizlendi — yeni mesajlar gelmeye devam eder.");
};

/* ---- messages: incremental full-session log ----
   Sunucudan yalnız lastMsgT sonrası çekilir (?msgs_since=) ve istemcide
   birikir; düğümler bir kez eklenir, seçim/scroll 1 Hz poll'dan etkilenmez.
   Sunucu ardışık tekrarları tek girdide birleştirir (count) — aynı metinli
   güncelleme son düğümün ×N rozetini tazeler. */
let lastMsgT = 0;
const MSG_DOM_MAX = 1500;
const TOAST_COOLDOWN = 120;  // s — ArduPilot aynı PreArm mesajını ~30 sn'de bir tekrarlar
const MERGE_WINDOW = 120;    // s — bu pencerede yinelenen metin yeni satır açmaz
let toastedAt = {};          // text -> son toast zamanı (istemci saati)
let livePrearm = {};         // text -> son görülme t (canlı pre-arm bloğu)
let lastNode = null;         // sunucu ardışık birleştirmesi için son düğüm
let msgIndex = new Map();    // "sev\x00text" -> düğüm (yinelenme birleştirme)
const clearedSrv = new Map(); // msgClear anındaki sunucu sayaç tabanları
function updateRep(d, m, count) {
  d.querySelector(".t").textContent = tfmt(m.t);
  d.dataset.t = m.t; d.dataset.count = count;
  if (count > 1) {
    const r = d.querySelector(".rep");
    r.textContent = "×" + count; r.classList.remove("hidden");
  }
}
function msgNode(m) {
  const d = document.createElement("div");
  d.className = "msg sev" + m.sev;
  if (/^(PreArm|Arm):/i.test(m.text)) d.classList.add("prearm");
  d.dataset.key = m.sev + "\x00" + m.text;
  d.dataset.sev = m.sev; d.dataset.text = m.text;
  d.innerHTML = `<span class="t"></span><span class="sv">${esc(m.sev_name ?? m.sev)}</span>${esc(m.text)}<span class="rep hidden"></span>`;
  updateRep(d, m, m.count || 1);
  applyMsgFilterTo(d);
  return d;
}
function renderMessages(msgs, busy) {
  const wrap = $("messages");
  msgs = (msgs || []).filter((m) => m.t > lastMsgT);
  if (!msgs.length) return;
  const atBottom = wrap.scrollTop + wrap.clientHeight >= wrap.scrollHeight - 20;
  const now = Date.now() / 1000;
  const criticals = [];
  let freshVisible = 0;   // pile yalnız gerçekten yeni/yer değiştiren görünür satırlar sayılır
  for (const m of msgs) {
    const key = m.sev + "\x00" + m.text;
    const mc = m.count || 1;
    const node = msgIndex.get(key);
    // dataset.count = ekranda gösterilen toplam; dataset.srv = bu metnin SON
    // sunucu girdisinde görülen sayaç (delta hesabı çift saymayı önler)
    if (node && node === lastNode && mc > Number(node.dataset.srv)) {
      // aynı sunucu girdisinin ardışık-birleştirme güncellemesi
      updateRep(node, m, Number(node.dataset.count) + mc - Number(node.dataset.srv));
      node.dataset.srv = mc;
    } else if (node && m.t - Number(node.dataset.t) < MERGE_WINDOW) {
      // araya başka mesaj girmiş tekrar yayını (A,B,A,B…): birikimli say,
      // düğümü en alta taşı — log kopya duvarı olmasın
      updateRep(node, m, Number(node.dataset.count) + mc);
      node.dataset.srv = mc;
      wrap.appendChild(node); lastNode = node;
      if (!node.classList.contains("f-hidden")) freshVisible++;
    } else {
      const base = clearedSrv.get(key) || 0;
      if (base) clearedSrv.delete(key);
      lastNode = wrap.appendChild(msgNode(
        base && mc > base ? { ...m, count: mc - base } : m));
      lastNode.dataset.srv = mc;
      msgIndex.set(key, lastNode);
      if (!lastNode.classList.contains("f-hidden")) freshVisible++;
    }
    if (/^(PreArm|Arm):/i.test(m.text)) livePrearm[m.text] = m.t;
    // eskalasyon atlananlar: backfill (lastMsgT 0), kontrol koşusu (pre-arm
    // bloğu zaten gösterir) ve cooldown içindeki aynı metin
    if (lastMsgT > 0 && m.sev <= 3 && busy !== "checks" &&
        !(toastedAt[m.text] > now - TOAST_COOLDOWN)) {
      toastedAt[m.text] = now;
      criticals.push(m);
    }
  }
  lastMsgT = msgs[msgs.length - 1].t;
  updateTicker(msgs[msgs.length - 1]);
  while (wrap.childNodes.length > MSG_DOM_MAX && wrap.firstChild !== lastNode) {
    const n = wrap.firstChild;
    if (msgIndex.get(n.dataset.key) === n) msgIndex.delete(n.dataset.key);
    wrap.removeChild(n);
  }
  $("msgCount").textContent = wrap.childNodes.length +
    (wrap.childNodes.length >= MSG_DOM_MAX ? "+" : "");
  if (atBottom && msgSize !== "kapali" && !msgPaused) wrap.scrollTop = wrap.scrollHeight;
  else if (msgSize !== "kapali" && freshVisible) {
    // kullanıcı yukarıda ya da duraklatmış: kaçanları yüzen pille bildir
    newSince += freshVisible;
    $("newMsgsN").textContent = newSince;
    $("newMsgs").classList.remove("hidden");
  }
  if (criticals.length) {
    const m = criticals[criticals.length - 1];
    toast((m.sev_name || "KRİTİK") + ": " + m.text +
      (criticals.length > 1 ? ` (+${criticals.length - 1} kritik mesaj)` : ""), 6000);
    flashTicker();
    const crit2 = criticals.filter((x) => x.sev <= 2);
    if (crit2.length) showCritBanner(crit2);
  }
}

/* ---- ticker: en son mesaj her zaman görünür ---- */
function updateTicker(m) {
  const tk = $("msgTicker");
  for (let i = 0; i <= 7; i++) tk.classList.remove("sev" + i);
  tk.classList.add("sev" + m.sev);
  $("tickerSev").textContent = m.sev_name ?? m.sev;
  $("tickerText").textContent = tfmt(m.t) + "  " + m.text;
}
let _flashTimer = null;
function flashTicker() {
  const tk = $("msgTicker");
  tk.classList.remove("flash"); void tk.offsetWidth;   // animasyonu yeniden başlat
  tk.classList.add("flash");
  clearTimeout(_flashTimer);
  _flashTimer = setTimeout(() => tk.classList.remove("flash"), 2600);
}
$("msgTicker").onclick = () => {
  if (msgSize === "kapali") applyMsgSize("yarim");
  const w = $("messages"); w.scrollTop = w.scrollHeight;
};

/* ---- sticky critical banner (sev<=2), onaylanana dek kalır ----
   olay değil TEKİL metin sayar: tek inatçı mesaj sayacı şişirmesin */
let critTexts = new Set();
function showCritBanner(list) {
  list.forEach((x) => critTexts.add(x.text));
  const m = list[list.length - 1];
  $("critText").textContent = (m.sev_name || "KRİTİK") + ": " + m.text +
    (critTexts.size > 1 ? `  (${critTexts.size} farklı kritik)` : "");
  $("critBanner").classList.remove("hidden");
}
$("critClose").onclick = () => { critTexts.clear(); $("critBanner").classList.add("hidden"); };

/* ---- calibration ---- */
document.querySelectorAll(".cal-btn").forEach((btn) => {
  btn.onclick = async () => { const r = await api.post("/api/cal/start", { kind: btn.dataset.cal }); if (r.error) toast(r.error); };
});
$("calClose").onclick = () => {
  const terminal = ["success","failed","cancelled","error","idle"].includes(lastCalPhase);
  api.post("/api/cal/action", { action: terminal ? "close" : "cancel" });
};
function accelGlyph(step) {
  const rot = { 1:0, 2:-90, 3:90, 4:60, 5:-60, 6:180 }[step] || 0;
  return `<svg viewBox="0 0 120 120"><g transform="rotate(${rot} 60 60)" stroke="var(--accent)"
    stroke-width="4" fill="var(--info-bg)" stroke-linejoin="round">
    <polygon points="60,12 70,52 70,70 110,90 110,98 70,84 70,100 78,112 78,116 60,110 42,116 42,112 50,100 50,84 10,98 10,90 50,70 50,52"/>
    </g></svg>`;
}
let rebootNeeded = false;
function renderCalibration(c) {
  const modal = $("calModal");
  if (!c || c.phase === "idle") { modal.classList.add("hidden"); lastCalPhase = "idle"; return; }
  modal.classList.remove("hidden");
  // canlı mesaj akışı: kalibrasyon STATUSTEXT'leri modalın arkasında kalmasın
  // (dataset'ten kurulur — textContent ayraçsız yapışık çıkıyordu)
  const feed = $("calMsgs");
  const rows = [...$("messages").children].slice(-5);
  const feedHtml = rows.map((n) =>
    `<div>${esc(tfmt(Number(n.dataset.t)))} ${esc(n.querySelector(".sv").textContent)} ${esc(n.dataset.text)}` +
    (Number(n.dataset.count) > 1 ? ` ×${n.dataset.count}` : "") + `</div>`).join("");
  if (feedHtml !== feed._lastHtml) {   // değişmedikçe dokunma: seçim/scroll yaşasın
    feed._lastHtml = feedHtml; feed.innerHTML = feedHtml;
  }
  feed.classList.toggle("hidden", !rows.length);
  // pusula/ivmeölçer sonrası reboot şart — unutulursa sonraki koşuda kafa
  // karıştırıcı INS-04 FAIL olarak çıkıyordu. Türkçe İ (U+0130) JS /i
  // bayrağıyla eşleşmez; tr küçük harfe çevirip duyarlı arıyoruz.
  const calTxt = ((c.message || "") + " " + (c.report || "")).toLocaleLowerCase("tr");
  if (c.phase === "success" && lastCalPhase !== "success" &&
      /yeniden başlat|reboot/.test(calTxt)) {
    rebootNeeded = true; $("rebootNote").classList.remove("hidden");
  }
  $("calTitle").textContent = c.title || "Kalibrasyon";
  $("calMessage").textContent = c.message || "";
  $("calApPrompt").textContent = c.ap_prompt || "";
  $("calApPrompt").classList.toggle("hidden", !c.ap_prompt);
  const ind = $("calStepIndicator");
  if (c.total_steps > 1) {
    ind.classList.remove("hidden");
    let h = "";
    for (let i=1;i<=c.total_steps;i++) {
      const cls = i < c.step ? "done" : (i === c.step ? "current" : "");
      h += `<div class="cal-step ${cls}">${i<c.step?"✓":i}</div>`;
    }
    ind.innerHTML = h;
  } else ind.classList.add("hidden");
  const vis = $("calVisual");
  if (c.kind === "compass") {
    const p = c.progress || 0;
    vis.innerHTML = `<div class="progress-wrap"><div class="progress-bar" style="width:${p}%"></div><div class="progress-label">${p}%</div></div>`;
  } else if (c.kind === "accel" && c.phase === "waiting" && c.step >= 1) {
    vis.innerHTML = `<div class="accel-icon">${accelGlyph(c.step)}</div>`;
  } else if (c.phase === "running") {
    vis.innerHTML = `<div class="spin-center"><div class="spinner"></div></div>`;
  } else vis.innerHTML = "";
  $("calReport").textContent = c.report || "";
  $("calReport").classList.toggle("hidden", !c.report);
  const banner = $("calBanner");
  const term = { success:["success","✔ Başarılı"], failed:["failed","✘ Başarısız"],
    cancelled:["cancelled","🛑 İptal edildi"], error:["failed","✘ Hata"] };
  if (term[c.phase]) { banner.className = "cal-banner "+term[c.phase][0]; banner.textContent = term[c.phase][1]; banner.classList.remove("hidden"); }
  else banner.classList.add("hidden");
  const act = $("calActions"); act.innerHTML = "";
  const isTerminal = ["success","failed","cancelled","error"].includes(c.phase);
  if (isTerminal) act.innerHTML = `<button class="primary" data-act="close">Kapat</button>`;
  else if (c.can_confirm) act.innerHTML = `<button class="ghost" data-act="cancel">İptal</button><button class="primary" data-act="confirm">${esc(c.confirm_label||"Onayla")}</button>`;
  else act.innerHTML = `<button class="ghost" data-act="cancel">İptal</button>`;
  act.querySelectorAll("button").forEach((b) => b.onclick = () => api.post("/api/cal/action", { action: b.dataset.act }));
  lastCalPhase = c.phase;
}

/* ---- Servo & Param sekmeleri ---- */
let _sfConfirmed = false;
document.querySelectorAll(".sf-btn").forEach((b) => {
  b.onclick = async () => {
    if (!_sfConfirmed) {
      const ok = await askConfirm("Yüzey yön testi",
        "Araç DISARM ve MANUAL modda olmalı; pervane çıkarılmış ya da motor " +
        "gücü kesilmiş olmalı. Komut ~1.5 sn kumanda girişi olarak uygulanır. " +
        "Devam edilsin mi?");
      if (!ok) return;
      _sfConfirmed = true;
    }
    // İstek arka plan worker'ını başlatır ve ANINDA döner; "running" vurgusu
    // ile butonun açık/kapalı durumunu poll (st.servo_test) yönetir. Buton
    // açık kalır ki çalışan test başka yöne basılarak devralınabilsin.
    try {
      const r = await api.post("/api/surface/test",
        { axis: b.dataset.axis, dir: b.dataset.dir });
      if (r.error) toast(r.error);
    } catch { toast("İstek başarısız."); }
  };
});

let _trimPending = 0;   // trim yanıtı beklerken yeniden render etme (yarış)
async function loadServos() {
  if (_trimPending) return;
  const wrap = $("servoList");
  let r;
  try { r = await api.get("/api/servos"); }
  catch { wrap.classList.add("hint"); wrap.textContent = "Yüklenemedi."; return; }
  if (r.error) { wrap.classList.add("hint"); wrap.textContent = r.error; return; }
  if (!r.servos.length) {
    wrap.classList.add("hint");
    wrap.textContent = "Kontrol yüzeyi fonksiyonlu servo bulunamadı — " +
      "parametre indirme sürüyorsa bitince ⟳ Yenile'ye basın.";
    return;
  }
  if (_trimPending) return;   // GET dönerken trim başladıysa eski veriyi basma
  wrap.classList.remove("hint");
  wrap.innerHTML = "";
  for (const s of r.servos) {
    const d = document.createElement("div"); d.className = "servo-row";
    d.innerHTML =
      `<div class="sr-name">S${s.n} · ${esc(s.function)}${s.reversed ? " · TERS" : ""}</div>` +
      `<button class="tr-btn" data-d="-10">−10</button>` +
      `<button class="tr-btn" data-d="-1">−1</button>` +
      `<span class="sr-val">${s.trim != null ? Math.round(s.trim) : "—"}</span>` +
      `<button class="tr-btn" data-d="1">+1</button>` +
      `<button class="tr-btn" data-d="10">+10</button>`;
    d.querySelectorAll(".tr-btn").forEach((tb) => tb.onclick = async () => {
      tb.disabled = true; _trimPending++;
      try {
        const r2 = await api.post("/api/servo/trim",
          { servo: s.n, delta: parseInt(tb.dataset.d, 10) });
        if (r2.error) toast(r2.error);
        else d.querySelector(".sr-val").textContent = Math.round(r2.value);
      } catch { toast("İstek başarısız."); }
      finally { _trimPending--; }   // butonları sonraki poll koşula göre açar
    });
    wrap.appendChild(d);
  }
}
$("servoRefresh").onclick = loadServos;

const fmtNum = (v) =>
  Math.abs(v - Math.round(v)) < 1e-9 ? String(Math.round(v)) : String(+v.toFixed(6));
async function loadRefInfo() {
  let r;
  try { r = await api.get("/api/refparams"); } catch { return; }
  $("refInfo").innerHTML = r.exists
    ? `📄 <b>${esc(r.path)}</b> · ${r.count ?? "?"} parametre` +
      (r.mtime ? ` · ${tfmt(r.mtime)}` : "") +
      (r.error ? ` · <span style="color:var(--fail)">${esc(r.error)}</span>` : "")
    : "Referans dosyası yok. Mevcut parametreleri kaydedin ya da dosya yükleyin.";
}
$("refSaveBtn").onclick = async () => {
  if (!await askConfirm("Referans kaydet",
    "Aracın ŞU ANKİ parametre tablosu ref_params.param dosyasına yazılacak " +
    "(varsa eskisinin üzerine). Devam edilsin mi?")) return;
  try {
    const r = await api.post("/api/refparams/save_current");
    toast(r.error || `Referans kaydedildi (${r.count} parametre).`);
  } catch { toast("İstek başarısız."); }
  loadRefInfo();
};
$("refUploadBtn").onclick = () => $("refFile").click();
$("refFile").onchange = async (e) => {
  const file = e.target.files[0]; if (!file) return;
  try {
    const text = await file.text();
    const r = await api.post("/api/refparams/upload", { text });
    toast(r.error || `Referans yüklendi (${r.count} parametre).`);
    loadRefInfo();
  } catch { toast("İstek başarısız."); }
  finally { e.target.value = ""; }   // aynı dosya yeniden seçilebilsin
};
$("diffBtn").onclick = async () => {
  const out = $("diffOut");
  out.innerHTML = '<span class="inline-spin"></span>';
  let r;
  try { r = await api.get("/api/param_diff"); }
  catch { out.textContent = "İstek başarısız."; return; }
  if (r.error) { out.innerHTML = `<div class="hint">${esc(r.error)}</div>`; return; }
  let html = "";
  if (!r.diffs.length)
    html += `<div class="diff-ok">✓ Fark yok — araç referansla uyumlu (${r.ref_count} parametre).</div>`;
  else {
    html += `<div class="diff-head"><span>Parametre (${r.diffs.length} fark)</span><span>ref → araç</span></div>`;
    html += r.diffs.map((d) =>
      `<div class="diff-row"><span class="dn">${esc(d.name)}</span>` +
      `<span class="dr">${esc(fmtNum(d.ref))}</span><span>→</span>` +
      `<span class="dc">${esc(fmtNum(d.cur))}</span></div>`).join("");
  }
  if (r.missing.length)
    html += `<div class="hint" style="margin-top:6px">Araçta olmayan ${r.missing.length} ` +
      `referans parametresi: ${esc(r.missing.slice(0, 12).join(", "))}` +
      (r.missing.length > 12 ? " …" : "") + `</div>`;
  if (r.ignored_volatile)
    html += `<div class="hint" style="margin-top:4px">${r.ignored_volatile} uçucu parametre ` +
      `(STAT_*, gyro offset vb.) karşılaştırma dışı tutuldu.</div>`;
  if (r.param_fetch_done === false)
    html += `<div class="hint" style="margin-top:4px;color:var(--warn)">⚠ Parametre indirme ` +
      `tamamlanmadı — sonuç eksik olabilir.</div>`;
  out.innerHTML = html;
};

/* ---- poll ---- */
async function poll() {
  let st;
  try { st = await api.get("/api/state?msgs_since=" + lastMsgT); }
  catch { setInd("tbLink","sunucu?","bad"); return; }
  const link = st.link || {}, t = st.telemetry || {};
  connected = !!link.connected;
  renderTopbar(st);
  if (connected !== _prevConnected) {
    const wasOffline = _prevConnected === false;
    _prevConnected = connected; invalidateMapSoon();
    if (!connected) {
      // otopilot reboot'u link düşmesi olarak görünür: not artık geçersiz
      rebootNeeded = false; $("rebootNote").classList.add("hidden");
      _resetAsked = false;
    }
    if (connected && wasOffline) suggestPerFlightReset();
  }
  document.body.classList.toggle("disconnected", !connected);
  $("disconnectBtn").classList.toggle("hidden", !connected && st.busy !== "connect");
  $("connectBtn").disabled = !!st.busy;
  $("connectBtn").textContent = st.busy === "connect" ? "Bağlanıyor…" : "Bağlan";

  $("railL").classList.toggle("hidden", !connected);
  $("center").classList.toggle("hidden", !connected);
  $("railR").classList.toggle("hidden", !connected);
  $("msgTicker").classList.toggle("hidden", !connected && !lastMsgT);
  $("hudBox").classList.toggle("hidden", !connected || t.roll == null);

  let cmsg = "";
  if (st.busy === "connect") cmsg = st.progress || "Bağlanılıyor…";
  else if (st.connect_info && st.connect_info.error) cmsg = "Hata: "+st.connect_info.error;
  const pp = st.param_progress;
  if (connected && pp && !pp.done) cmsg = `Parametreler: ${pp.got}${pp.total?"/"+pp.total:""}`;
  $("connStatus").innerHTML = cmsg ? (st.busy?'<span class="inline-spin"></span>':"")+esc(cmsg) : "";

  // param indirme connect-busy altında sürer; busy zaten kilitliyor — eksik
  // biten indirme run'ı kalıcı kilitlemesin (run_all eksikleri kendisi çeker)
  $("runBtn").disabled = !connected || !!st.busy;
  if (st.busy === "checks" && lastBusy !== "checks") checksStart = Date.now();
  $("problemList").classList.toggle("stale-run", st.busy === "checks");
  $("reportBtn").disabled = !connected || !st.have_results || !!st.busy;
  if (st.busy === "checks")
    $("checkStatus").innerHTML = '<span class="inline-spin"></span>'+esc(st.progress||"");
  document.querySelectorAll(".cal-btn").forEach((b) => b.disabled = !connected || (!!st.busy && st.busy !== "calibrate") || t.armed);
  const writeGate = !connected || !!st.busy || t.armed;
  // Yüzey testi butonları: çalışan bir test (busy==='servo') sırasında AÇIK
  // kalır ki yön değiştirilip test devralınabilsin (donma yok); etkin yön
  // st.servo_test'ten vurgulanır. Diğer işlemlerde (connect/checks/cal) kapalı.
  const stv = st.servo_test;
  const sfGate = !connected || t.armed || (!!st.busy && st.busy !== "servo");
  document.querySelectorAll(".sf-btn").forEach((b) => {
    b.disabled = sfGate;
    b.classList.toggle("running",
      !!stv && stv.axis === b.dataset.axis && stv.dir === b.dataset.dir);
  });
  document.querySelectorAll("#servoList .tr-btn").forEach((b) => {
    b.disabled = writeGate || _trimPending > 0;   // override sırasında param yazma yok
  });
  $("servoRefresh").disabled = !connected;
  const ppGate = !connected || !!st.busy || !!(pp && !pp.done);
  $("refSaveBtn").disabled = ppGate;   // yarım tabloyu referans yapma
  $("diffBtn").disabled = ppGate;

  // sunucu yeniden başladıysa sunucu-sayaç izleri geçersiz: srv'leri sıfırla
  // ki ilk görüş delta yerine birikimli dalı seçsin (×N eksik saymasın)
  if (st.boot && st.boot !== serverBoot) {
    if (serverBoot != null) { msgIndex.forEach((n) => { n.dataset.srv = 0; }); clearedSrv.clear(); }
    serverBoot = st.boot;
  }
  // mesajlar kopukken de işlenir: tampon sunucuda yaşıyor, kopuşu açıklayan
  // mesajlar ticker'da görünür kalır ve log birikmeye devam eder
  renderMessages(st.messages, st.busy);
  if (connected) {
    if (!checklistDef) renderChecklist();   // ilk yükleme başarısızsa tekrar dene
    renderTiles(t, link);
    renderPrearmLive(t);
    renderHealth(t);
    renderBanner(st);
    if (t.roll != null) updateHUD(t);
    updateReadout(t);
    if (t.pos) updateVehicle(t.pos);
    if (t.home) updateHomeFence(t.home, t.fence);
    renderCalibration(st.calibration);
  } else renderCalibration({ phase:"idle" });

  // results_time tabanlı senkron: koşuyu başka istemci tetiklese de bir poll
  // döngüsünde yakınsanır (eski lastBusy sezgisi bunu kaçırıyordu)
  if (st.have_results && st.busy !== "checks" &&
      st.results_time && st.results_time !== resultsShown) {
    const data = await api.get("/api/results");
    if (data.results && data.results.time !== resultsShown) {
      resultsShown = data.results.time; renderStatusCenter(data.results);
    }
  }
  if (!st.have_results && connected && st.busy !== "checks") renderStatusCenter(null);
  lastBusy = st.busy;
}

/* ---- init ---- */
initMap();
applyMapSize(mapSize);
applyMsgSize(msgSize);
renderChecklist();
setInterval(poll, 1000);
poll();
