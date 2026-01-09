import mido
from sequencer.models import MidiTrack, Event, Note

def import_midi_to_project(sequencer, filepath: str):
    """
    Imports a MIDI file into an existing project, creating new tracks and
    adjusting the note timings to the project's tempo.

    Args:
        sequencer: The main Sequencer instance.
        filepath: The path to the MIDI file to import.
    """
    try:
        midi_file = mido.MidiFile(filepath)
        ticks_per_beat = midi_file.ticks_per_beat or 480

        # --- Naming Logic ---
        # Determine the starting number for new track names
        existing_midi_import_tracks = [
            t.name for t in sequencer.song.tracks if t.name.startswith("midimp")
        ]
        start_num = 1
        while f"midimp{start_num}" in existing_midi_import_tracks:
            start_num += 1

        project_beats_per_second = sequencer.song.tempo / 60.0
        imported_track_count = 0

        for i, mido_track in enumerate(midi_file.tracks):
            new_track_name = f"midimp{start_num + imported_track_count}"
            new_track = MidiTrack(name=new_track_name)

            # --- Tempo Adjustment Logic ---
            absolute_time_seconds = 0.0
            current_midi_tempo_us = 500000  # Default MIDI tempo (120 BPM)
            open_notes = {}  # pitch -> (start_time_seconds, velocity)

            for msg in mido_track:
                # Calculate time delta in seconds based on the current MIDI tempo
                time_delta_seconds = mido.tick2second(msg.time, ticks_per_beat, current_midi_tempo_us)
                absolute_time_seconds += time_delta_seconds

                if msg.is_meta and msg.type == 'set_tempo':
                    current_midi_tempo_us = msg.tempo

                elif msg.type == 'note_on' and msg.velocity > 0:
                    open_notes[msg.note] = (absolute_time_seconds, msg.velocity)

                elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                    if msg.note in open_notes:
                        start_time_seconds, velocity = open_notes.pop(msg.note)
                        duration_seconds = absolute_time_seconds - start_time_seconds

                        if duration_seconds > 0:
                            # Convert seconds to project beats
                            start_time_beats = start_time_seconds * project_beats_per_second
                            duration_beats = duration_seconds * project_beats_per_second

                            note = Note(pitch=msg.note, velocity=velocity, duration=duration_beats)

                            # Check if an event already exists at this start time
                            existing_event = None
                            for event in new_track.events:
                                if abs(event.start_time - start_time_beats) < 1e-6: # Fuzz factor
                                    existing_event = event
                                    break

                            if existing_event:
                                existing_event.notes.append(note)
                            else:
                                new_event = Event(start_time=start_time_beats, notes=[note])
                                new_track.add_event(new_event)

            # Only add the track if it contains any events
            if new_track.events:
                sequencer.song.add_track(new_track)
                imported_track_count += 1

        sequencer.invalidate_song_length_cache()
        return {"status": "success", "message": f"Successfully imported {imported_track_count} MIDI track(s)."}

    except Exception as e:
        return {"status": "error", "message": f"Failed to import MIDI file: {e}"}
