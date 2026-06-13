#!/usr/bin/env bash
# ArduCheck kurulum betiği (Ubuntu/Debian)
set -e
cd "$(dirname "$0")"

echo "== ArduCheck kurulumu =="

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python3 bulunamadı, kuruluyor..."
    sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
fi

# Sanal ortam: sistem Python'una dokunmadan kurulum
if [ ! -d .venv ]; then
    python3 -m venv .venv 2>/dev/null || {
        echo "python3-venv eksik, kuruluyor..."
        sudo apt-get update && sudo apt-get install -y python3-venv
        python3 -m venv .venv
    }
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

# Seri port erişimi için dialout grubu
if ! groups | grep -q dialout; then
    echo "Seri port erişimi için kullanıcı 'dialout' grubuna ekleniyor..."
    sudo usermod -aG dialout "$USER" || true
    echo "NOT: Grup üyeliğinin etkinleşmesi için oturumu kapatıp açın."
fi

echo
echo "Kurulum tamam. Başlatmak için:  ./baslat_ubuntu.sh"
