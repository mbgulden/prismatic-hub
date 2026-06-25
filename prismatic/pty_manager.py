import os
import pty
import select
import shlex
import struct
import fcntl
import termios
import threading
import time
from collections import deque

class RingBuffer:
    def __init__(self, maxlen=10000):
        self.buffer = deque(maxlen=maxlen)
        self.lock = threading.Lock()

    def append(self, data):
        with self.lock:
            self.buffer.append(data)

    def get_all(self):
        with self.lock:
            return list(self.buffer)

class PtyProcess:
    def __init__(self, command="/bin/bash", env=None):
        self.command = command
        self.env = env or os.environ.copy()
        self.buffer = RingBuffer()
        self.master_fd = None
        self.pid = None
        self.thread = None
        self.running = False
        self.on_output = None # Callback for live output

    def start(self):
        self.pid, self.master_fd = pty.fork()
        if self.pid == 0: # Child
            # Use /bin/sh -c to support shell commands and path resolution
            try:
                argv = ["/bin/sh", "-c", self.command]
                os.execve(argv[0], argv, self.env)
            finally:
                os._exit(1)
        
        # Parent
        self.running = True
        # Set non-blocking
        attr = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, attr | os.O_NONBLOCK)
        
        self.thread = threading.Thread(target=self._read_loop, daemon=True)
        self.thread.start()

    def _read_loop(self):
        while self.running:
            try:
                rfds, _, _ = select.select([self.master_fd], [], [], 0.1)
                if self.master_fd in rfds:
                    data = os.read(self.master_fd, 4096)
                    if not data:
                        self.running = False
                        break
                    self.buffer.append(data)
                    if self.on_output:
                        self.on_output(data)
            except (OSError, EOFError):
                self.running = False
                break
        self.cleanup()

    def write(self, data):
        if self.master_fd:
            os.write(self.master_fd, data)

    def resize(self, rows, cols):
        if self.master_fd:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)

    def cleanup(self):
        self.running = False
        if self.master_fd:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        if self.pid:
            try:
                os.waitpid(self.pid, os.WNOHANG)
            except OSError:
                pass

class Run:
    def __init__(self, run_id, command="/bin/bash", ttl=1800):
        self.run_id = run_id
        self.command = command
        self.pty = PtyProcess(command)
        self.ttl = ttl
        self.last_activity = time.time()
        self.clients = set() # Set of output callbacks
        self.pty.on_output = self._broadcast_output

    def _broadcast_output(self, data):
        self.last_activity = time.time()
        for client_cb in list(self.clients):
            try:
                client_cb(data)
            except:
                self.clients.remove(client_cb)

    def start(self):
        self.pty.start()

    def attach(self, callback):
        # Use the buffer's lock to ensure we don't miss any output
        # between replaying the buffer and adding the client.
        with self.pty.buffer.lock:
            # Replay buffer
            for chunk in self.pty.buffer.buffer:
                callback(chunk)
            self.clients.add(callback)
        self.last_activity = time.time()

    def detach(self, callback):
        if callback in self.clients:
            self.clients.remove(callback)
        self.last_activity = time.time()

    def is_expired(self):
        if len(self.clients) > 0:
            return False
        return (time.time() - self.last_activity) > self.ttl

class RunManager:
    def __init__(self):
        self.runs = {}
        self.lock = threading.Lock()

    def create_run(self, run_id, command="/bin/bash", ttl=1800):
        with self.lock:
            run = Run(run_id, command, ttl)
            self.runs[run_id] = run
            run.start()
            return run

    def get_run(self, run_id):
        with self.lock:
            return self.runs.get(run_id)

    def cleanup_task(self):
        while True:
            with self.lock:
                to_delete = []
                for run_id, run in list(self.runs.items()):
                    if run.is_expired() or not run.pty.running:
                        to_delete.append(run_id)
                
                for run_id in to_delete:
                    print(f"Cleaning up run {run_id}")
                    run = self.runs.pop(run_id)
                    run.pty.cleanup()
            time.sleep(10) # Faster for prototype
