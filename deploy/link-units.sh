#!/usr/bin/env bash
# Corre como root, despues de que /opt/panel ya sea el checkout del repo.
# Simlinkea las unidades systemd (asi que un git pull + este script otra vez
# es lo unico necesario para recoger cambios de unit files).
set -euo pipefail

SRC="/opt/panel/deploy/systemd"
DST="/etc/systemd/system"

for unit in panel-infra.service "tmux@.service" "ttyd@.service"; do
  ln -sf "${SRC}/${unit}" "${DST}/${unit}"
done

systemctl daemon-reload
systemctl enable --now panel-infra.service
