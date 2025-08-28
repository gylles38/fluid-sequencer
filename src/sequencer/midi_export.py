from midiutil import MIDIFile
from .models import Song

def export_to_midi(song: Song, filename: str):
    """
    Exports a Song object to a MIDI file.

    :param song: The Song object to export.
    :param filename: The path to the output MIDI file.
    """
    midi_file = MIDIFile(len(song.tracks))

    # Set tempo
    midi_file.addTempo(track=0, time=0, tempo=song.tempo)

    for i, track in enumerate(song.tracks):
        # Add track name
        midi_file.addTrackName(track=i, time=0, trackName=track.name)
        # Set instrument
        midi_file.addProgramChange(tracknum=i, channel=i, time=0, program=track.instrument)

        for event in track.events:
            for note in event.notes:
                midi_file.addNote(
                    track=i,
                    channel=i,
                    pitch=note.pitch,
                    time=event.start_time,
                    duration=note.duration,
                    volume=note.velocity
                )

    with open(filename, "wb") as output_file:
        midi_file.writeFile(output_file)
