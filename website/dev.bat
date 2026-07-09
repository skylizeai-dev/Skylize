@echo off
taskkill /F /IM node.exe /T 2>nul
timeout /t 1 /nobreak >nul
npm run dev
