import mido
from sequencer.models import Song, MidiTrack
from typing import List

def export_midi_from_project(sequencer, track_indices: List[int], filepath: str):
    """
    Exports selected MIDI tracks from the current project to a .mid file.

    Args:
        sequencer: The main Sequencer instance.
        track_indices: A list of integer indices for the tracks to export.
        filepath: The path to save the MIDI file.
    """
    try:
        song = sequencer.song
        ticks_per_beat = song.ticks_per_beat

        # Create a new MIDI file (type 1 for multiple tracks)
        mid = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat)

        # --- Create a tempo track ---
        tempo_track = mido.MidiTrack()
        mid.tracks.append(tempo_track)
        tempo_track.append(mido.MetaMessage('time_signature',
                                           numerator=song.time_signature_numerator,
                                           denominator=song.time_signature_denominator))
        tempo_track.append(mido.MetaMessage('set_tempo',
                                           tempo=mido.bpm2tempo(song.tempo)))

        # --- Process and add selected tracks ---
        for track_index in track_indices:
            if not 0 <= track_index < len(song.tracks):
                continue # Skip invalid indices

            track = song.tracks[track_index]
            if not isinstance(track, MidiTrack):
                continue # Skip non-MIDI tracks

            mido_track = mido.MidiTrack()
            mido_track.append(mido.MetaMessage('track_name', name=track.name))

            # Add initial program change, bank select, etc.
            mido_track.append(mido.Message('program_change', channel=track.channel, program=track.instrument, time=0))
            if track.bank_msb is not None:
                mido_track.append(mido.Message('control_change', channel=track.channel, control=0, value=track.bank_msb, time=0))
            if track.bank_lsb is not None:
                mido_track.append(mido.Message('control_change', channel=track.channel, control=32, value=track.bank_lsb, time=0))

            # --- Convert all notes and CCs to a flat list with absolute ticks ---
            all_midi_events = []
            for event in track.events:
                start_tick = int(event.start_time * ticks_per_beat)

                # Add CC messages
                for cc in event.cc_messages:
                    all_midi_events.append({
                        'tick': start_tick,
                        'msg': mido.Message('control_change', channel=track.channel, control=cc.control, value=cc.value)
                    })

                # Add notes (as note_on and note_off pairs)
                for note in event.notes:
                    end_tick = start_tick + int(note.duration * ticks_per_beat)
                    final_velocity = int(note.velocity * track.velocity)
                    final_velocity = max(0, min(127, final_velocity))

                    all_midi_events.append({
                        'tick': start_tick,
                        'msg': mido.Message('note_on', channel=track.channel, note=note.pitch, velocity=final_velocity)
                    })
                    all_midi_events.append({
                        'tick': end_tick,
                        'msg': mido.Message('note_off', channel=track.channel, note=note.pitch, velocity=0)
                    })

            if not all_midi_events:
                continue # Don't add empty tracks

            # --- Sort events by tick and convert to relative time ---
            all_midi_events.sort(key=lambda e: e['tick'])

            last_tick = 0
            for event_dict in all_midi_events:
                delta_ticks = event_dict['tick'] - last_tick
                event_dict['msg'].time = delta_ticks
                mido_track.append(event_dict['msg'])
                last_tick = event_dict['tick']

            mid.tracks.append(mido_track)

        # Save the file
        mid.save(filepath)
        return {"status": "success", "message": f"Successfully exported selected tracks to {filepath}."}

    except Exception as e:
        return {"status": "error", "message": f"Failed to export MIDI file: {e}"}
