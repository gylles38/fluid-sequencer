import mido
from .models import Song

def export_to_midi(song: Song, filename: str, ticks_per_beat: int = 480):
    """
    Exports a Song object to a MIDI file using mido.

    :param song: The Song object to export.
    :param filename: The path to the output MIDI file.
    :param ticks_per_beat: The number of ticks per beat (quarter note).
    """
    mid = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat)

    # Create a tempo track (required for type 1 files)
    tempo_track = mido.MidiTrack()
    mid.tracks.append(tempo_track)
    # mido tempo is in microseconds per beat
    tempo_track.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(song.tempo)))

    for i, track in enumerate(song.tracks):
        midi_track = mido.MidiTrack()
        mid.tracks.append(midi_track)

        midi_track.append(mido.MetaMessage('track_name', name=track.name))
        # Use channel `i` for track `i`. Channels are 0-15.
        channel = i % 16
        midi_track.append(mido.Message('program_change', channel=channel, program=track.instrument, time=0))

        # --- Convert absolute time events to delta time MIDI messages ---

        # 1. Create a flat list of all note on/off events with absolute times in ticks
        all_midi_events = []
        for event in track.events:
            for note in event.notes:
                start_tick = int(event.start_time * ticks_per_beat)
                end_tick = start_tick + int(note.duration * ticks_per_beat)

                all_midi_events.append({'tick': start_tick, 'type': 'note_on', 'pitch': note.pitch, 'velocity': note.velocity})
                all_midi_events.append({'tick': end_tick, 'type': 'note_off', 'pitch': note.pitch, 'velocity': note.velocity})

        # 2. Sort events by tick time
        all_midi_events.sort(key=lambda e: e['tick'])

        # 3. Convert to mido messages with delta times
        last_tick = 0
        for event in all_midi_events:
            delta_ticks = event['tick'] - last_tick
            midi_track.append(mido.Message(
                event['type'],
                channel=channel,
                note=event['pitch'],
                velocity=event['velocity'],
                time=delta_ticks
            ))
            last_tick = event['tick']

    mid.save(filename)
