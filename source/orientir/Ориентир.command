#!/bin/bash
# Запускает «Ориентир» на локальном сервере.
# Так браузер считает страницу полноценным сайтом и разрешает сохранение,
# чего он не делает для файла, открытого двойным кликом.

cd "$(dirname "$0")" || exit 1

PORT=8765
while lsof -i ":$PORT" >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

python3 -m http.server "$PORT" >/dev/null 2>&1 &
SERVER=$!

cleanup() { kill "$SERVER" 2>/dev/null; }
trap cleanup EXIT INT TERM

sleep 1
open "http://localhost:$PORT/index.html"

echo ""
echo "  Ориентир открыт:  http://localhost:$PORT"
echo ""
echo "  Это окно должно оставаться открытым, пока вы работаете."
echo "  Закройте его, когда закончите."
echo ""

wait "$SERVER"
