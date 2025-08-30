from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class Note:
    """Represents a single musical note."""
    pitch: int  # MIDI note number (0-127)
    velocity: int = 100  # MIDI velocity (0-127)
    duration: float = 1.0  # Duration in beats

    def __post_init__(self):
        if not 0 <= self.pitch <= 127:
            raise ValueError("Pitch must be between 0 and 127.")
        if not 0 <= self.velocity <= 127:
            raise ValueError("Velocity must be between 0 and 127.")
        if self.duration <= 0:
            raise ValueError("Duration must be positive.")

@dataclass
class Event:
    """Represents a musical event, which can contain multiple notes (e.g., a chord)."""
    notes: List[Note]
    start_time: float  # Start time in beats from the beginning of the track

    def __post_init__(self):
        if self.start_time < 0:
            raise ValueError("Start time cannot be negative.")

@dataclass
class Track:
    """Represents a track, which is a sequence of musical events."""
    name: str
    events: List[Event] = field(default_factory=list)
    instrument: int = 0  # MIDI program number (0-127)
    output_port_name: Optional[str] = None

    def add_event(self, event: Event):
        """Adds an event to the track and keeps the event list sorted by start time."""
        self.events.append(event)
        self.events.sort(key=lambda e: e.start_time)

@dataclass
class Song:
    """Represents a song, containing multiple tracks and global settings."""
    name: str
    tempo: int = 120  # Beats per minute (BPM)
    tracks: List[Track] = field(default_factory=list)

    def add_track(self, track: Track):
        """Adds a track to the song."""
        self.tracks.append(track)
