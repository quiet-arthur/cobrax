#!/usr/bin/env bash
# Atualiza o Cobrax no VPS com o que está na main.
# Uso (no servidor):  sudo /opt/cobrax/deploy/deploy.sh
set -euo pipefail

APP=/opt/cobrax
as_app() { sudo -u cobrax env HOME=/var/lib/cobrax bash -ec "cd $APP && $1"; }

echo "==> Código"
as_app "git fetch -q origin main && git reset -q --hard origin/main && git log --oneline -1"

echo "==> Dependências (versões do uv.lock)"
as_app "uv sync --locked --no-dev -q"

echo "==> Serviços"
install -m 644 "$APP"/deploy/systemd/cobrax-*.service "$APP"/deploy/systemd/cobrax-*.timer /etc/systemd/system/
install -m 644 "$APP"/deploy/Caddyfile /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now cobrax-outbox.timer >/dev/null
systemctl enable cobrax-webhook.service >/dev/null
systemctl restart cobrax-webhook.service
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl reload caddy

echo "==> Saúde"
for _ in $(seq 1 20); do
	if curl -fs http://127.0.0.1:8000/health >/dev/null 2>&1; then
		echo "webhook: ok"
		exit 0
	fi
	sleep 0.5
done
echo "webhook NÃO respondeu — veja: journalctl -u cobrax-webhook -n 50" >&2
exit 1
