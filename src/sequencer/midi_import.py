import mido
from .models import Song, MidiTrack, Event, Note, CCMessage
from collections import defaultdict

def import_song(filepath: str) -> Song:
    """
    Imports an entire MIDI file and converts it into a Song object,
    including notes and control change messages.

    :param filepath: Path to the MIDI file.
    :return: A Song object containing all tracks, events, and tempo from the file.
    """
    mid = mido.MidiFile(filepath)
    ticks_per_beat = mid.ticks_per_beat if mid.ticks_per_beat > 0 else 480

    tempo = 120  # Default tempo
    time_sig_num = 4
    time_sig_den = 4

    # First, find tempo and time signature from the first track (or any track)
    for track in mid.tracks:
        for msg in track:
            if msg.is_meta:
                if msg.type == 'set_tempo':
                    tempo = mido.tempo2bpm(msg.tempo)
                elif msg.type == 'time_signature':
                    time_sig_num = msg.numerator
                    time_sig_den = msg.denominator

    song = Song(
        name=filepath.split('/')[-1].replace('.mid', '').replace('.midi', ''),
        tempo=int(tempo),
        time_signature_numerator=time_sig_num,
        time_signature_denominator=time_sig_den,
        ticks_per_beat=ticks_per_beat
    )

    for i, midi_track in enumerate(mid.tracks):
        # Skip tracks that only contain metadata and no musical events
        if not any(msg.type in ('note_on', 'control_change') for msg in midi_track):
            continue

        track_name = f"Track {i}"
        instrument = 0
        channel = 0 # Default channel

        # A dictionary to hold events, keyed by their start tick.
        # The value will be a dictionary holding lists of notes and ccs.
        events_by_tick = defaultdict(lambda: {'notes': [], 'cc_messages': []})
        open_notes = defaultdict(list)
        absolute_time_ticks = 0

        # First pass: process all messages to populate events_by_tick
        for msg in midi_track:
            absolute_time_ticks += msg.time
            channel = msg.channel if hasattr(msg, 'channel') else channel

            if msg.is_meta and msg.type == 'track_name':
                track_name = msg.name
            elif msg.type == 'program_change':
                instrument = msg.program
            elif msg.type == 'control_change':
                cc = CCMessage(control=msg.control, value=msg.value)
                events_by_tick[absolute_time_ticks]['cc_messages'].append(cc)
            elif msg.type == 'note_on' and msg.velocity > 0:
                open_notes[msg.note].append((absolute_time_ticks, msg.velocity))
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if open_notes[msg.note]:
                    start_tick, velocity = open_notes[msg.note].pop(0)
                    duration_ticks = absolute_time_ticks - start_tick
                    duration_beats = duration_ticks / ticks_per_beat

                    if duration_beats >= 0: # Allow zero-duration notes for grace notes etc.
                        note = Note(pitch=msg.note, velocity=velocity, duration=duration_beats)
                        events_by_tick[start_tick]['notes'].append(note)

        # If there are any events, create a track for them
        if events_by_tick:
            new_track = MidiTrack(
                name=track_name,
                instrument=instrument,
                channel=channel
            )
            # Second pass: convert the grouped dictionary into Event objects
            for tick, event_data in sorted(events_by_tick.items()):
                if event_data['notes'] or event_data['cc_messages']:
                    start_time_beats = tick / ticks_per_beat
                    event = Event(
                        start_time=start_time_beats,
                        notes=event_data['notes'],
                        cc_messages=event_data['cc_messages']
                    )
                    new_track.add_event(event)

            song.add_track(new_track)

    return song
