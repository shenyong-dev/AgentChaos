"""TCP-only fault proxy for owned connections. Never logs MySQL protocol bytes."""

import select
import socket
import threading


class FaultProxy:
    def __init__(self, port, target):
        self.target = target
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("0.0.0.0", port))
        self.listener.listen(64)
        self.listener.settimeout(0.2)
        self.lock = threading.Lock()
        self.connections = set()
        self.stopped = threading.Event()
        self.enabled = True
        self.thread = threading.Thread(target=self.accept, daemon=True, name="t17-sql-proxy")
        self.thread.start()

    def accept(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.listener.accept()
            except (OSError, TimeoutError):
                continue
            with self.lock:
                if not self.enabled:
                    client.close()
                    continue
                self.connections.add(client)
            threading.Thread(target=self.forward, args=(client,), daemon=True).start()

    def forward(self, client):
        upstream = None
        try:
            upstream = socket.create_connection(self.target, timeout=3)
            with self.lock:
                if not self.enabled:
                    return
                self.connections.add(upstream)
            while not self.stopped.is_set():
                ready, _, _ = select.select([client, upstream], [], [], 0.2)
                for source in ready:
                    value = source.recv(65536)
                    if not value:
                        return
                    (upstream if source is client else client).sendall(value)
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                self.connections.discard(client)
                self.connections.discard(upstream)
            client.close()
            if upstream:
                upstream.close()

    def pause(self):
        with self.lock:
            self.enabled = False
            for connection in self.connections:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                connection.close()
            self.connections.clear()

    def resume(self):
        with self.lock:
            self.enabled = True

    def close(self):
        self.pause()
        self.stopped.set()
        self.listener.close()
        self.thread.join(timeout=2)
