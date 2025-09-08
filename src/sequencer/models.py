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
class CCMessage:
    """Represents a single MIDI Control Change message."""
    control: int  # CC number (0-127)
    value: int    # CC value (0-127)

    def __post_init__(self):
        if not 0 <= self.control <= 127:
            raise ValueError("Control number must be between 0 and 127.")
        if not 0 <= self.value <= 127:
            raise ValueError("Value must be between 0 and 127.")

@dataclass
class ProgramChangeMessage:
    """Represents a single MIDI Program Change message."""
    program: int  # Program number (0-127)

    def __post_init__(self):
        if not 0 <= self.program <= 127:
            raise ValueError("Program number must be between 0 and 127.")

@dataclass
class Event:
    """Represents a musical event, which can contain multiple notes (e.g., a chord) and CC messages."""
    start_time: float  # Start time in beats from the beginning of the track
    notes: List[Note] = field(default_factory=list)
    cc_messages: List[CCMessage] = field(default_factory=list)
    program_change_messages: List[ProgramChangeMessage] = field(default_factory=list)

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
    pan: float = 0.0  # Pan (-1.0 left to 1.0 right, maps to 0-127)
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
    pan: float = 0.0 # (-1.0 for left, 0.0 for center, 1.0 for right)

@dataclass
class AutomationPoint:
    """Represents a single point in an automation curve."""
    start_time: float  # Start time in beats
    parameter: str  # e.g., "volume", "pan", "cc_10"
    value: float  # The value of the parameter at this point
    curve: str = "step"  # "step" or "linear"

    def __post_init__(self):
        if self.start_time < 0:
            raise ValueError("Start time cannot be negative.")
        if self.curve not in ["step", "linear"]:
            raise ValueError("Curve type must be 'step' or 'linear'.")

        # Validate parameter format
        param_lower = self.parameter.lower()
        if param_lower.startswith("cc"):
            try:
                cc_num = int(param_lower[2:])
                if not 0 <= cc_num <= 127:
                    raise ValueError("CC number must be between 0 and 127.")
            except (ValueError, IndexError):
                 raise ValueError(f"Invalid CC parameter format: {self.parameter}")
        elif param_lower not in ["vol", "pan", "vel", "prog"]:
             raise ValueError(f"Invalid parameter name: {self.parameter}")

@dataclass
class AutomationTrack(BaseTrack):
    """A track that contains automation data for another track."""
    target_track_index: int
    is_muted: bool = False
    is_solo: bool = False
    points: List[AutomationPoint] = field(default_factory=list)

    def add_point(self, point: AutomationPoint):
        """Adds an automation point and keeps the list sorted."""
        self.points.append(point)
        self.points.sort(key=lambda p: p.start_time)


# Using Union to allow the list to contain both MidiTrack and AudioTrack objects
AnyTrack = Union[MidiTrack, AudioTrack, AutomationTrack]

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
