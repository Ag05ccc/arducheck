#!/usr/bin/env bash
# SITL (ArduPlane simülatörü) başlatır — ArduCheck'i gerçek araç olmadan
# denemek için. Ayrı bir terminalde çalıştırın; açık bıraktığınız sürece
# 127.0.0.1:5760 (TCP) üzerinden bağlanılabilir.
#
# Kullanım:  scripts/sitl_baslat.sh
set -e

ARDUPILOT="${ARDUPILOT:-$HOME/ardupilot}"
BIN="$ARDUPILOT/build/sitl/bin/arduplane"
DEFAULTS="$ARDUPILOT/Tools/autotest/models/plane.parm"
HOME_POS="${HOME_POS:--35.363261,149.165230,584,353}"
WORKDIR="${SITL_WORKDIR:-/tmp/arducheck_sitl}"

if [ ! -x "$BIN" ]; then
  echo "HATA: SITL derlemesi bulunamadı: $BIN"
  echo "ArduPilot'u derleyin ya da ARDUPILOT değişkenini ayarlayın:"
  echo "  ARDUPILOT=/yol/ardupilot scripts/sitl_baslat.sh"
  exit 1
fi

mkdir -p "$WORKDIR"; cd "$WORKDIR"
echo "SITL başlıyor (TCP 127.0.0.1:5760). Durdurmak için Ctrl-C."
echo "Parametreler $WORKDIR/eeprom.bin içinde saklanır (silmeden temiz başlatma için klasörü silin)."
exec "$BIN" --model plane --speedup 1 --defaults "$DEFAULTS" --home "$HOME_POS"
