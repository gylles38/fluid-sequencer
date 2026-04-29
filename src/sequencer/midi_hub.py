import threading
import mido
import queue
import time
from typing import List, Optional

class MidiInputHub:
    def __init__(self, port_name: str):
        self.port_name = port_name
        self.subscribers: List[queue.Queue] = []
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue()
        with self._lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None

    def _run(self):
        try:
            with mido.open_input(self.port_name) as inport:
                while not self._stop_event.is_set():
                    # Process all pending messages
                    for msg in inport.iter_pending():
                        with self._lock:
                            for q in self.subscribers:
                                q.put(msg)
                    time.sleep(0.005)
        except Exception as e:
            print(f"MidiInputHub error on port {self.port_name}: {e}")
