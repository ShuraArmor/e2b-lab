@echo off
title mini-KVM Linux Console
chcp 65001 >nul
echo Attaching to background VM console ...
echo If no session exists, a fresh VM will be started automatically.
wsl.exe -e bash -c "tmux attach -t vm 2>/dev/null || (cd /mnt/e/ProjBuild/QIUZHAO/Sandbox/e2b-lab/kvm-demo && ./scripts/vm.sh)"
echo.
echo Session ended.
pause
