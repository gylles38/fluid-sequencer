import bisect
from dataclasses import dataclass, field
from typing import List

@dataclass
class Note:
    pitch: int
    velocity: int = 100
    duration: float = 1.0

@dataclass
class Event:
    start_time: float
    notes: List[Note] = field(default_factory=list)

events = []
e1 = Event(start_time=1.0, notes=[Note(pitch=60)])
bisect.insort(events, e1, key=lambda e: e.start_time)
e2 = Event(start_time=0.5, notes=[Note(pitch=62)])
bisect.insort(events, e2, key=lambda e: e.start_time)
print(events)
