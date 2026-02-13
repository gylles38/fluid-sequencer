import json
import sys
from dataclasses import is_dataclass, fields

# It's better to import the models directly to avoid circular dependencies
# if this module were to be used by the models themselves in the future.
from .models import Song, MidiTrack, AudioTrack, AutomationTrack

class CustomSongEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Song):
            return {
                '__type__': 'Song',
                'name': o.name,
                'tempo': o.tempo,
                'time_signature_numerator': o.time_signature_numerator,
                'time_signature_denominator': o.time_signature_denominator,
                'ticks_per_beat': o.ticks_per_beat,
                'sync_offset_sec': o.sync_offset_sec,
                'tracks': o.tracks,
                'midi_mappings': o.midi_mappings,
                'metronome_enabled': o.metronome_enabled,
                'metronome_port_name': o.metronome_port_name,
                'metronome_volume': o.metronome_volume,
                'metronome_pan': o.metronome_pan,
                'carla_project_path': o.carla_project_path,
                'aj_snapshot_path': o.aj_snapshot_path,
            }
        if isinstance(o, MidiTrack):
            return {
                '__type__': 'MidiTrack',
                'name': o.name,
                'is_muted': o.is_muted,
                'is_solo': o.is_solo,
                'is_metronome': o.is_metronome,
                'channel': o.channel,
                'volume': o.volume,
                'pan': o.pan,
                'velocity': o.velocity,
                'events': o.events,
                'instrument': o.instrument,
                'bank_msb': o.bank_msb,
                'bank_lsb': o.bank_lsb,
                'output_port_name': o.output_port_name,
                'input_port_name': o.input_port_name,
                'record_mode': o.record_mode,
            }
        if isinstance(o, AudioTrack):
            return {
                '__type__': 'AudioTrack',
                'name': o.name,
                'filepath': o.filepath,
                'is_muted': o.is_muted,
                'is_solo': o.is_solo,
                'start_time': o.start_time,
                'volume': o.volume,
                'pan': o.pan,
                'channels': o.channels,
                'native_tempo': o.native_tempo,
                'duration_beats': o.duration_beats,
            }
        if isinstance(o, AutomationTrack):
            return {
                '__type__': 'AutomationTrack',
                'name': o.name,
                'target_track_index': o.target_track_index,
                'is_muted': o.is_muted,
                'is_solo': o.is_solo,
                'points': o.points,
            }
        if is_dataclass(o):
            d = {f.name: getattr(o, f.name) for f in fields(o)}
            d['__type__'] = o.__class__.__name__
            return d
        return super().default(o)

def song_decoder(d):
    if '__type__' in d:
        type_name = d.pop('__type__')

        if type_name == 'Song':
            # Remove input_routing from dictionary as it is now an init=False field
            # and will be re-linked from the tracks list during startup.
            d.pop('input_routing', None)

        # The classes are defined in the 'sequencer.models' module.
        # We need to look there to find the class definitions.
        module = sys.modules.get('sequencer.models')
        if module:
            cls = getattr(module, type_name, None)
            if cls:
                return cls(**d)

    return d
