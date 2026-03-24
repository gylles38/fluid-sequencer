from dataclasses import dataclass, field
from typing import List, Optional, Union
from kivy.properties import BooleanProperty, StringProperty
from kivy.event import EventDispatcher

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
class Event:
    """Represents a musical event, which can contain multiple notes (e.g., a chord) and CC messages."""
    start_time: float  # Start time in beats from the beginning of the track
    notes: List[Note] = field(default_factory=list)
    cc_messages: List[CCMessage] = field(default_factory=list)

    def __post_init__(self):
        if self.start_time < 0:
            raise ValueError("Start time cannot be negative.")

@dataclass
class BaseTrack:
    """Base class for tracks. Cannot be instantiated directly."""
    name: str
    # is_muted and is_solo moved to child classes to solve non-default argument error

from kivy.properties import NumericProperty
from kivy.event import EventDispatcher


# Note: This class inherits from EventDispatcher to support Kivy's property-binding
# system. This allows the UI to automatically react to changes in track properties like 'volume'.
# The @dataclass decorator was removed as it is not compatible with this pattern.
class MidiTrack(BaseTrack, EventDispatcher):
    """Represents a MIDI track, which is a sequence of musical events."""
    volume = NumericProperty(0.8)
    is_solo = BooleanProperty(False)
    velocity = NumericProperty(1.0)

    def __init__(self, name: str, is_muted: bool = False, is_solo: bool = False, is_metronome: bool = False,
                 channel: int = 0, volume: float = 0.8, pan: float = 0.0, velocity: float = 1.0,
                 events: List[Event] = None, instrument: int = 0, bank_msb: Optional[int] = None,
                 bank_lsb: Optional[int] = None, output_port_name: Optional[str] = None,
                 input_port_name: Optional[str] = None, record_mode: str = 'OFF', **kwargs):
        BaseTrack.__init__(self, name=name)
        EventDispatcher.__init__(self, **kwargs)
        self.is_muted = is_muted
        self.is_solo = is_solo
        self.is_metronome = is_metronome
        self.channel = channel
        self.volume = volume
        self.pan = pan
        self.velocity = velocity
        self.events = events if events is not None else []
        self.instrument = instrument
        self.bank_msb = bank_msb
        self.bank_lsb = bank_lsb
        self.output_port_name = output_port_name
        self.input_port_name = input_port_name
        self.record_mode = record_mode
    
    def add_event(self, event: Event):
        """Adds a MIDI event to the track and keeps the event list sorted by start time."""
        self.events.append(event)
        self.events.sort(key=lambda e: e.start_time)

    def __repr__(self):
        return (f"MidiTrack(name='{self.name}', channel={self.channel}, "
                f"instrument={self.instrument}, volume={self.volume}, pan={self.pan}, "
                f"events=[...{len(self.events)} items...])")

# Note: This class inherits from EventDispatcher to support Kivy's property-binding
# system. This allows the UI to automatically react to changes in track properties like 'volume'.
# The @dataclass decorator was removed as it is not compatible with this pattern.
class AudioTrack(BaseTrack, EventDispatcher):
    """Represents an audio track, which is a single audio file."""
    volume = NumericProperty(0.5)
    is_solo = BooleanProperty(False)

    def __init__(self, name: str, filepath: str, is_muted: bool = False, is_solo: bool = False,
                 start_time: float = 0.0, volume: float = 0.5, pan: float = 0.0,
                 channels: int = 0, native_tempo: Optional[float] = None,
                 duration_beats: Optional[float] = None, **kwargs):
        BaseTrack.__init__(self, name=name)
        EventDispatcher.__init__(self, **kwargs)
        self.filepath = filepath
        self.is_muted = is_muted
        self.is_solo = is_solo
        self.start_time = start_time
        self.volume = volume
        self.pan = pan
        self.channels = channels
        self.native_tempo = native_tempo
        self.duration_beats = duration_beats

    def __repr__(self):
        return (f"AudioTrack(name='{self.name}', filepath='{self.filepath}', "
                f"volume={self.volume}, pan={self.pan})")

@dataclass
class AutomationPoint:
    """Represents a single point in an automation curve."""
    VALID_CURVES = ["none", "linear", "ease-in", "ease-out", "ease-in-out", "sine"]
    start_time: float  # Start time in beats
    parameter: str  # e.g., "volume", "pan", "cc_10"
    value: float  # The value of the parameter at this point
    curve: str = "none"  # "none", "linear", "ease-in", "ease-out", "ease-in-out", "sine"
    curve_value: float = 1.0    

    def __post_init__(self):
        if self.start_time < 0:
            raise ValueError("Start time cannot be negative.")

        if self.curve not in self.VALID_CURVES:
            raise ValueError(f"Curve type must be one of {self.VALID_CURVES}.")
            
        # Validation de la limite sine (on bride à 20 pour la stabilité)
        if self.curve == "sine":
            self.curve_value = max(0.5, min(20.0, self.curve_value))

        # Validate parameter format
        param_lower = self.parameter.lower()
        if param_lower == "volume": param_lower = "vol"
        if param_lower == "program": param_lower = "prog"
                
        if param_lower.startswith("cc"):
            try:
                cc_num = int(param_lower[2:])
                if not 0 <= cc_num <= 127:
                    raise ValueError("CC number must be between 0 and 127.")
            except (ValueError, IndexError):
                 raise ValueError(f"Invalid CC parameter format: {self.parameter}")
        elif param_lower not in ["vol", "pan", "vel", "prog", "input_routing", "pitch", "pb"]:
             raise ValueError(f"Invalid parameter name: {self.parameter}")
        self.parameter = param_lower

class AutomationTrack(BaseTrack, EventDispatcher):
    """A track that contains automation data for another track."""
    is_muted = BooleanProperty(False)
    is_solo = BooleanProperty(False)
    active_parameter = StringProperty('vol')

    def __init__(self, name: str, target_track_index: int, is_muted: bool = False,
                 is_solo: bool = False, points: List[AutomationPoint] = None,
                 active_parameter: str = 'vol', **kwargs):
        BaseTrack.__init__(self, name=name)
        EventDispatcher.__init__(self, **kwargs)
        self.target_track_index = target_track_index
        self.is_muted = is_muted
        self.is_solo = is_solo
        self._points = points if points is not None else []
        self.active_parameter = active_parameter
        self._cache_points_by_param = {}
        self._cache_dirty = True

    @property
    def points(self):
        return self._points

    @points.setter
    def points(self, value):
        self._points = value
        self._cache_dirty = True

    def add_point(self, point: AutomationPoint, sort: bool = True):
        """Adds an automation point and optionally keeps the list sorted."""
        self._points.append(point)
        self._cache_dirty = True
        if sort:
            self._points.sort(key=lambda p: p.start_time)

    def _ensure_cache(self):
        if self._cache_dirty:
            self._cache_points_by_param = {}
            for p in self._points:
                self._cache_points_by_param.setdefault(p.parameter, []).append(p)
            for param in self._cache_points_by_param:
                self._cache_points_by_param[param].sort(key=lambda x: x.start_time)
            self._cache_dirty = False

    def get_value_at(self, beat: float, parameter: str = 'vol') -> float:
        import math
        self._ensure_cache()
        pts = self._cache_points_by_param.get(parameter, [])

        if not pts:
            if parameter == 'pan': return 0.0
            if parameter == 'pitch' or parameter == 'pb': return 0.0
            return 1.0

        if beat <= pts[0].start_time: return pts[0].value
        if beat >= pts[-1].start_time: return pts[-1].value

        # 2. Recherche du segment correspondant au beat actuel
        for i in range(len(pts) - 1):
            p1, p2 = pts[i], pts[i+1]
            if p1.start_time <= beat <= p2.start_time:
                # t est la progression normalisée (0.0 à 1.0) entre les deux points
                duration = p2.start_time - p1.start_time
                if duration == 0: return p1.value # Sécurité division par zéro
                
                t = (beat - p1.start_time) / duration
                
                c = p1.curve.lower()
                
                if c == "none":
                    return p1.value
                
                # --- CALCUL DU FACTEUR D'INTERPOLATION (ti) ---
                if c == "linear":
                    ti = t
                elif c == "ease-in":
                    ti = t * t
                elif c == "ease-out":
                    ti = t * (2 - t)
                elif c == "ease-in-out":
                    ti = 0.5 * (1 - math.cos(t * math.pi))
                elif c == "sine":
                    # --- CORRECTION MAJEURE ICI ---
                    freq = getattr(p1, 'curve_value', 1.0)
                    ti = 0.5 * (1 - math.cos(t * math.pi * freq))
                else:
                    ti = t

                # --- APPLICATION DE LA VALEUR ---
                return p1.value + (p2.value - p1.value) * ti

        return 1.0

    def __repr__(self):
        return (f"AutomationTrack(name='{self.name}', target_track_index={self.target_track_index}, "
                f"points=[...{len(self.points)} items...])")


# Using Union to allow the list to contain both MidiTrack and AudioTrack objects
AnyTrack = Union[MidiTrack, AudioTrack, AutomationTrack]

def is_midi_track(track) -> bool:
    """Robustly checks if a track is a MIDI track."""
    if track is None: return False
    # Note: Avoid checking just 'hasattr(track, "events")' because Kivy's EventDispatcher
    # has an internal 'events()' method.
    return (getattr(track, 'is_midi', False) is True or
            track.__class__.__name__ == 'MidiTrack' or
            (hasattr(track, 'events') and isinstance(getattr(track, 'events'), list)))

def is_audio_track(track) -> bool:
    """Robustly checks if a track is an audio track."""
    if track is None: return False
    return (getattr(track, 'is_audio', False) is True or
            track.__class__.__name__ == 'AudioTrack' or
            (hasattr(track, 'filepath') and isinstance(getattr(track, 'filepath'), str)))

@dataclass
class MidiMapping:
    """Represents a mapping from a MIDI CC message to a sequencer action."""
    channel: int
    control: int
    action: str
    track_index: int

@dataclass
class Song:
    """Represents a song, containing multiple tracks and global settings."""
    name: str
    tempo: int = 120  # Beats per minute (BPM)
    time_signature_numerator: int = 4
    time_signature_denominator: int = 4
    ticks_per_beat: int = 480
    sync_offset_sec: float = 1.0 # Time in seconds for MIDI/audio sync correction
    tracks: List[AnyTrack] = field(default_factory=list)
    midi_mappings: List[MidiMapping] = field(default_factory=list)
    metronome_enabled: bool = False
    metronome_port_name: Optional[str] = None
    metronome_volume: float = 1.0
    metronome_pan: float = 0.0
    carla_project_path: Optional[str] = None
    aj_snapshot_path: Optional[str] = None
    input_routing: Optional[AutomationTrack] = None
    track_display_order: List[int] = field(default_factory=list)

    def __post_init__(self):
        # Initialize display order if empty (e.g. on new song or loading legacy project)
        if not self.track_display_order and self.tracks:
            self.track_display_order = list(range(len(self.tracks)))

    def add_track(self, track: AnyTrack):
        """Adds a track to the song, assigning a default channel if it's a MIDI track."""
        if isinstance(track, MidiTrack):
            # Assign channel based on the number of existing MIDI tracks
            midi_track_count = sum(1 for t in self.tracks if isinstance(t, MidiTrack))
            if midi_track_count < 16:
                track.channel = midi_track_count
            else:
                track.channel = 15 # Default to last channel if more than 16 tracks
        self.track_display_order.append(len(self.tracks))
        self.tracks.append(track)
