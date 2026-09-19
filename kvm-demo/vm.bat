@echo off
title mini-KVM Linux
chcp 65001 >nul
echo Starting mini-KVM Linux VM in WSL2 ...
echo Exit hotkey: Ctrl+A then X
wsl.exe -e bash -c "cd /mnt/e/ProjBuild/QIUZHAO/Sandbox/e2b-lab/kvm-demo && ./scripts/vm.sh"
echo.
echo VM session ended.
pause
