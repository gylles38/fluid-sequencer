import mido
from .models import Song, MidiTrack, AudioTrack, AutomationTrack
import numpy as np
from typing import List

def _generate_automation_events_for_export(auto_track: 'AutomationTrack', song: 'Song') -> List[dict]:
    """
    Generates a list of concrete MIDI/audio events from an automation track.
    """
    generated_events = []
    points = sorted(auto_track.points, key=lambda p: p.start_time)
    if not points:
        return []

    target_track_index = auto_track.target_track_index
    if not 0 <= target_track_index < len(song.tracks):
        return []
    target_track = song.tracks[target_track_index]

    param_map = {
        "volume": {"type": "midi_cc", "control": 7},
        "pan": {"type": "midi_cc", "control": 10},
        "program": {"type": "program_change"},
        **{f"cc{i}": {"type": "midi_cc", "control": i} for i in range(128)}
    }

    for i, start_point in enumerate(points):
        param_config = param_map.get(start_point.parameter.lower())
        if not param_config:
            continue

        generated_events.append({
            "time": start_point.start_time,
            "param_config": param_config,
            "value": start_point.value
        })

        if start_point.curve == "linear" and i + 1 < len(points):
            end_point = points[i+1]
            if start_point.parameter != end_point.parameter:
                continue

            start_time = start_point.start_time
            end_time = end_point.start_time
            start_val = start_point.value
            end_val = end_point.value
            time_diff = end_time - start_time
            if time_diff <= 0:
                continue

            granularity = 1.0 / 16.0
            num_steps = int(time_diff / granularity)

            if num_steps > 1:
                time_steps = np.linspace(start_time, end_time, num_steps, endpoint=False)
                value_steps = np.linspace(start_val, end_val, num_steps, endpoint=False)
                for step_time, step_value in zip(time_steps[1:], value_steps[1:]):
                    generated_events.append({
                        "time": step_time,
                        "param_config": param_config,
                        "value": step_value
                    })
    return generated_events


def export_to_midi(song: Song, filename: str, ticks_per_beat: int = 480):
    mid = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat)

    tempo_track = mido.MidiTrack()
    mid.tracks.append(tempo_track)
    tempo_track.append(mido.MetaMessage('time_signature', numerator=song.time_signature_numerator, denominator=song.time_signature_denominator))
    tempo_track.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(song.tempo)))

    # Generate all automation events first, grouped by target track index
    automation_events_by_track = {}
    for i, track in enumerate(song.tracks):
        if isinstance(track, AutomationTrack):
            # We assume the target is a MIDI track for MIDI export
            target_idx = track.target_track_index
            if target_idx not in automation_events_by_track:
                automation_events_by_track[target_idx] = []

            gen_events = _generate_automation_events_for_export(track, song)
            automation_events_by_track[target_idx].extend(gen_events)

    # Map track indices to the actual mido tracks we create
    midi_track_map = {}

    for i, track in enumerate(song.tracks):
        if not isinstance(track, MidiTrack):
            if isinstance(track, AudioTrack):
                print(f"Warning: Skipping audio track '{track.name}' during MIDI export.")
            elif not isinstance(track, AutomationTrack):
                 print(f"Warning: Skipping unknown track type for track '{track.name}' during MIDI export.")
            continue

        midi_track = mido.MidiTrack()
        # We add the track to the file later, after we know it's not empty
        midi_track_map[i] = midi_track

        midi_track.append(mido.MetaMessage('track_name', name=track.name))
        channel = track.channel

        # We send initial program change, but automation might override it
        midi_track.append(mido.Message('program_change', channel=channel, program=track.instrument, time=0))
        if track.bank_msb is not None:
             midi_track.append(mido.Message('control_change', channel=channel, control=0, value=track.bank_msb, time=0))
        if track.bank_lsb is not None:
             midi_track.append(mido.Message('control_change', channel=channel, control=32, value=track.bank_lsb, time=0))


        all_midi_events = []
        for event in track.events:
            start_tick = int(event.start_time * ticks_per_beat)
            for cc in event.cc_messages:
                all_midi_events.append({'tick': start_tick, 'msg': mido.Message('control_change', channel=channel, control=cc.control, value=cc.value)})
            for pc in getattr(event, 'program_change_messages', []):
                 all_midi_events.append({'tick': start_tick, 'msg': mido.Message('program_change', channel=channel, program=pc.program)})
            for note in event.notes:
                end_tick = start_tick + int(note.duration * ticks_per_beat)
                final_velocity = int(note.velocity * track.velocity)
                final_velocity = max(0, min(127, final_velocity))
                all_midi_events.append({'tick': start_tick, 'msg': mido.Message('note_on', channel=channel, note=note.pitch, velocity=final_velocity)})
                all_midi_events.append({'tick': end_tick, 'msg': mido.Message('note_off', channel=channel, note=note.pitch, velocity=0)})

        # Add generated automation events for this specific track
        if i in automation_events_by_track:
            for auto_event in automation_events_by_track[i]:
                start_tick = int(auto_event['time'] * ticks_per_beat)
                param_config = auto_event['param_config']
                value = auto_event['value']
                msg = None

                if param_config['type'] == 'midi_cc':
                    control = param_config['control']
                    cc_value = 0
                    if control == 7: # Volume
                        cc_value = int(max(0.0, min(1.0, value)) * 127)
                    elif control == 10: # Pan
                        cc_value = int((max(-1.0, min(1.0, value)) + 1.0) / 2.0 * 127)
                    else:
                        cc_value = int(max(0, min(127, value)))
                    msg = mido.Message('control_change', channel=channel, control=control, value=cc_value)

                elif param_config['type'] == 'program_change':
                    program = int(max(0, min(127, value)))
                    msg = mido.Message('program_change', channel=channel, program=program)

                if msg:
                    all_midi_events.append({'tick': start_tick, 'msg': msg})

        if not all_midi_events and len(midi_track) <= 3: # Only metadata
            # Don't add empty tracks to the MIDI file
            print(f"Info: Skipping empty MIDI track '{track.name}'.")
            continue

        mid.tracks.append(midi_track)
        all_midi_events.sort(key=lambda e: e['tick'])

        last_tick = 0
        for event in all_midi_events:
            delta_ticks = event['tick'] - last_tick
            event['msg'].time = delta_ticks
            midi_track.append(event['msg'])
            last_tick = event['tick']

    mid.save(filename)
