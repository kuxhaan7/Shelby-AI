@echo off
rem OpenClaw Gateway (v2026.5.12)
set "TMPDIR=C:\Users\kusha\AppData\Local\Temp"
set "OPENCLAW_GATEWAY_PORT=18789"
set "OPENCLAW_SYSTEMD_UNIT=openclaw-gateway.service"
set "OPENCLAW_WINDOWS_TASK_NAME=OpenClaw Gateway"
set "OPENCLAW_SERVICE_MARKER=openclaw"
set "OPENCLAW_SERVICE_KIND=gateway"
set "OPENCLAW_SERVICE_VERSION=2026.5.12"
"C:\Program Files\nodejs\node.exe" C:\Users\kusha\AppData\Roaming\npm\node_modules\openclaw\dist\index.js gateway --port 18789
