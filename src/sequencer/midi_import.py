import mido
from .models import Song, Track, Event, Note
from collections import defaultdict

def import_song(filepath: str) -> Song:
    """
    Imports an entire MIDI file and converts it into a Song object.

    :param filepath: Path to the MIDI file.
    :return: A Song object containing all tracks and tempo from the file.
    """
    mid = mido.MidiFile(filepath)
    ticks_per_beat = mid.ticks_per_beat

    tempo = 120 # Default tempo

    # First, find the tempo from the first track, if it exists
    if mid.tracks:
        for msg in mid.tracks[0]:
            if msg.is_meta and msg.type == 'set_tempo':
                tempo = mido.tempo2bpm(msg.tempo)
                break

    song = Song(name=filepath.split('/')[-1], tempo=int(tempo))

    for midi_track in mid.tracks:
        # Don't create a track if it only contains metadata
        if not any(msg.type in ('note_on', 'note_off') for msg in midi_track):
            continue

        track_name = "Untitled Track"
        instrument = 0
        note_events = []

        absolute_time_ticks = 0
        open_notes = defaultdict(list)

        for msg in midi_track:
            absolute_time_ticks += msg.time

            if msg.is_meta and msg.type == 'track_name':
                track_name = msg.name
            elif msg.type == 'program_change':
                instrument = msg.program
            elif msg.type == 'note_on' and msg.velocity > 0:
                open_notes[msg.note].append((absolute_time_ticks, msg.velocity))
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if open_notes[msg.note]:
                    start_tick, velocity = open_notes[msg.note].pop(0)
                    duration_ticks = absolute_time_ticks - start_tick

                    start_time_beats = start_tick / ticks_per_beat
                    duration_beats = duration_ticks / ticks_per_beat

                    if duration_beats > 0:
                        note = Note(pitch=msg.note, velocity=velocity, duration=duration_beats)
                        note_events.append(Event(notes=[note], start_time=start_time_beats))

        if note_events:
            new_track = Track(name=track_name, instrument=instrument)
            note_events.sort(key=lambda e: e.start_time)
            new_track.events = note_events
            song.add_track(new_track)

    return song
