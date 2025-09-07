import mido
from .models import Song, MidiTrack, AudioTrack

def export_to_midi(song: Song, filename: str, ticks_per_beat: int = 480):
    """
    Exports a Song object to a MIDI file using mido.
    Audio tracks will be ignored.

    :param song: The Song object to export.
    :param filename: The path to the output MIDI file.
    :param ticks_per_beat: The number of ticks per beat (quarter note).
    """
    mid = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat)

    # Create a tempo track (required for type 1 files)
    tempo_track = mido.MidiTrack()
    mid.tracks.append(tempo_track)
    # Add time signature and tempo meta messages
    tempo_track.append(mido.MetaMessage('time_signature', numerator=song.time_signature_numerator, denominator=song.time_signature_denominator))
    tempo_track.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(song.tempo)))

    for i, track in enumerate(song.tracks):
        if isinstance(track, AudioTrack):
            print(f"Warning: Skipping audio track '{track.name}' during MIDI export.")
            continue

        if not isinstance(track, MidiTrack):
            print(f"Warning: Skipping unknown track type for track '{track.name}' during MIDI export.")
            continue

        midi_track = mido.MidiTrack()
        mid.tracks.append(midi_track)

        midi_track.append(mido.MetaMessage('track_name', name=track.name))
        # Use the channel stored in the track model.
        channel = track.channel
        midi_track.append(mido.Message('program_change', channel=channel, program=track.instrument, time=0))

        # --- Convert absolute time events to delta time MIDI messages ---

        # 1. Create a flat list of all MIDI messages with absolute times in ticks
        all_midi_events = []
        for event in track.events:
            start_tick = int(event.start_time * ticks_per_beat)

            # Add CC messages for this event
            for cc in event.cc_messages:
                all_midi_events.append({
                    'tick': start_tick,
                    'msg': mido.Message('control_change', channel=channel, control=cc.control, value=cc.value)
                })

            # Add note messages for this event
            for note in event.notes:
                end_tick = start_tick + int(note.duration * ticks_per_beat)
                # Apply track velocity multiplier and clamp
                final_velocity = int(note.velocity * track.velocity)
                final_velocity = max(0, min(127, final_velocity))

                all_midi_events.append({
                    'tick': start_tick,
                    'msg': mido.Message('note_on', channel=channel, note=note.pitch, velocity=final_velocity)
                })
                all_midi_events.append({
                    'tick': end_tick,
                    'msg': mido.Message('note_off', channel=channel, note=note.pitch, velocity=0)
                })

        # 2. Sort events by tick time
        all_midi_events.sort(key=lambda e: e['tick'])

        # 3. Convert to mido messages with delta times
        last_tick = 0
        for event in all_midi_events:
            delta_ticks = event['tick'] - last_tick
            event['msg'].time = delta_ticks
            midi_track.append(event['msg'])
            last_tick = event['tick']

    mid.save(filename)
