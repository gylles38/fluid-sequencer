from dataclasses import dataclass, field
from typing import List, Optional, Union

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
class BaseTrack:
    """Base class for tracks. Cannot be instantiated directly."""
    name: str
    # is_muted and is_solo moved to child classes to solve non-default argument error

@dataclass
class MidiTrack(BaseTrack):
    """Represents a MIDI track, which is a sequence of musical events."""
    is_muted: bool = False
    is_solo: bool = False
    channel: int = 0  # MIDI channel (0-15)
    volume: float = 0.8 # Default volume (0.0 to 1.0, maps to 0-127)
    velocity: float = 1.0 # Velocity multiplier (0.0 to 2.0+)
    events: List[Event] = field(default_factory=list)
    instrument: int = 0  # MIDI program number (0-127)
    bank_msb: Optional[int] = None  # Bank Select MSB (CC#0)
    bank_lsb: Optional[int] = None  # Bank Select LSB (CC#32)
    output_port_name: Optional[str] = None

    def add_event(self, event: Event):
        """Adds a MIDI event to the track and keeps the event list sorted by start time."""
        self.events.append(event)
        self.events.sort(key=lambda e: e.start_time)

@dataclass
class AudioTrack(BaseTrack):
    """Represents an audio track, which is a single audio file."""
    filepath: str
    is_muted: bool = False
    is_solo: bool = False
    start_time: float = 0.0 # Start time in beats from the beginning of the track
    volume: float = 0.5 # (0.0 to 1.0)
    # pan: float = 0.0 # (-1.0 for left, 0.0 for center, 1.0 for right)


# Using Union to allow the list to contain both MidiTrack and AudioTrack objects
AnyTrack = Union[MidiTrack, AudioTrack]

@dataclass
class Song:
    """Represents a song, containing multiple tracks and global settings."""
    name: str
    tempo: int = 120  # Beats per minute (BPM)
    time_signature_numerator: int = 4
    time_signature_denominator: int = 4
    ticks_per_beat: int = 480
    tracks: List[AnyTrack] = field(default_factory=list)
    metronome_enabled: bool = False
    metronome_port_name: Optional[str] = None

    def add_track(self, track: AnyTrack):
        """Adds a track to the song, assigning a default channel if it's a MIDI track."""
        if isinstance(track, MidiTrack):
            # Assign channel based on the number of existing MIDI tracks
            midi_track_count = sum(1 for t in self.tracks if isinstance(t, MidiTrack))
            if midi_track_count < 16:
                track.channel = midi_track_count
            else:
                track.channel = 15 # Default to last channel if more than 16 tracks
        self.tracks.append(track)
