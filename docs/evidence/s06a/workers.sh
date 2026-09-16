#!/bin/bash
start_worker() { # start_worker <role> <instance> <tokfile> <tag>
  cd "$WT"
  nohup "$PYBIN" -m mco.orchestrator.score_canary_worker --role "$1" --instance "$2" \
    --token-file "$S6/home/.mco/$3" --gateway http://127.0.0.1:18997 > "$S6/logs/w-$4.log" 2>&1 &
  sleep 1
  P=$(powershell.exe -NoProfile -Command "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -match '--instance $2' } | Select-Object -First 1).ProcessId" | tr -d '\r\n ')
  echo "$P" > "$S6/logs/w-$4.pid"; echo "worker $2 pid=$P"
}
stop_worker() { P=$(cat "$S6/logs/w-$1.pid" 2>/dev/null); [ -n "$P" ] && powershell.exe -NoProfile -Command "Stop-Process -Id $P -Force -ErrorAction SilentlyContinue" >/dev/null 2>&1; echo "worker tag=$1 pid=$P killed"; }
