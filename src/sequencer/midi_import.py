import mido
from .models import Track, Event, Note
from collections import defaultdict

def import_midi_to_track(filepath: str) -> Track:
    """
    Imports a MIDI file and converts its first track with note events into a Track object.

    :param filepath: Path to the MIDI file.
    :return: A Track object containing the imported musical data.
    """
    mid = mido.MidiFile(filepath)
    ticks_per_beat = mid.ticks_per_beat

    imported_track = None

    for i, midi_track in enumerate(mid.tracks):
        track_name = f"Track {i}"
        note_events = []

        # --- State for parsing ---
        absolute_time_ticks = 0
        open_notes = defaultdict(list) # key: pitch, value: list of (start_tick, velocity)

        for msg in midi_track:
            absolute_time_ticks += msg.time

            if msg.is_meta and msg.type == 'track_name':
                track_name = msg.name

            elif msg.type == 'note_on' and msg.velocity > 0:
                # Store the start of a note
                open_notes[msg.note].append((absolute_time_ticks, msg.velocity))

            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                # A note has ended, find its corresponding start event
                if open_notes[msg.note]:
                    start_tick, velocity = open_notes[msg.note].pop(0)
                    duration_ticks = absolute_time_ticks - start_tick

                    start_time_beats = start_tick / ticks_per_beat
                    duration_beats = duration_ticks / ticks_per_beat

                    if duration_beats > 0:
                        note = Note(
                            pitch=msg.note,
                            velocity=velocity,
                            duration=duration_beats
                        )
                        # For simplicity, each note becomes a new Event.
                        # A more complex importer might group simultaneous notes into a single Event.
                        note_events.append(Event(notes=[note], start_time=start_time_beats))

        # If we found any notes in this track, use it and stop.
        if note_events:
            imported_track = Track(name=track_name)
            # Sort events by start time before adding, as they are created out of order
            note_events.sort(key=lambda e: e.start_time)
            imported_track.events = note_events
            break # Stop after the first track with notes

    if imported_track is None:
        raise ValueError(f"No tracks with note events found in '{filepath}'")

    return imported_track
