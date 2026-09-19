#!/usr/bin/env python3
# 微型 TCP 转发器：WSL:8080 -> 虚拟机 10.0.2.15:80
# 让 Windows 浏览器能访问 LEMU 虚拟机里的 httpd
import socket, threading

LISTEN = ("0.0.0.0", 8080)
TARGET = ("10.0.2.15", 80)

def pipe(a, b):
    try:
        while True:
            d = a.recv(65536)
            if not d: break
            b.sendall(d)
    except OSError:
        pass
    a.close(); b.close()

srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(LISTEN); srv.listen(16)
print(f"转发器: {LISTEN[0]}:{LISTEN[1]} -> {TARGET[0]}:{TARGET[1]}")
while True:
    c, addr = srv.accept()
    try:
        r = socket.create_connection(TARGET, timeout=3)
        threading.Thread(target=pipe, args=(c, r), daemon=True).start()
        threading.Thread(target=pipe, args=(r, c), daemon=True).start()
    except OSError as e:
        print(f"连接 {addr} 失败: {e}"); c.close()
