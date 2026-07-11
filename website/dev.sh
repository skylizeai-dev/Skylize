#!/bin/bash
pkill -f "next dev" 2>/dev/null
lsof -ti:3000 | xargs kill -9 2>/dev/null
sleep 1
npm run dev
