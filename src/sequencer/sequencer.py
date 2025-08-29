import time
import mido
from .models import Song, Track, Event, Note
from .midi_import import import_midi_to_track
from .midi_export import export_to_midi

class Sequencer:
    """
    The main class for the interactive sequencer.
    It manages the song, tracks, and provides an API for high-level operations.
    """
    def __init__(self, tempo: int = 120):
        self.song = Song(name="New Song", tempo=tempo)

    def set_tempo(self, tempo: int):
        """Sets the tempo of the song."""
        if tempo <= 0:
            raise ValueError("Tempo must be positive.")
        self.song.tempo = tempo
        print(f"Tempo set to {self.song.tempo} BPM.")

    def add_track(self, name: str, instrument: int = 0):
        """Adds a new, empty track to the song."""
        track = Track(name=name, instrument=instrument)
        self.song.add_track(track)
        print(f"Track '{name}' added.")

    def load_track_from_file(self, filepath: str):
        """Loads a MIDI file as a new track in the song."""
        try:
            new_track = import_midi_to_track(filepath)
            self.song.add_track(new_track)
            print(f"Loaded track '{new_track.name}' from '{filepath}'.")
        except Exception as e:
            print(f"Error loading MIDI file: {e}")

    def save_song(self, filepath: str):
        """Saves the entire song to a MIDI file."""
        try:
            export_to_midi(self.song, filepath)
            print(f"Song successfully saved to '{filepath}'.")
        except Exception as e:
            print(f"Error saving MIDI file: {e}")

    def list_tracks(self) -> str:
        """Returns a string listing all tracks in the song."""
        if not self.song.tracks:
            return "No tracks in the song."

        lines = [f"Song: {self.song.name} | Tempo: {self.song.tempo} BPM"]
        lines.append("=" * 20)
        for i, track in enumerate(self.song.tracks):
            lines.append(f"[{i}] {track.name} (Instrument: {track.instrument}, {len(track.events)} events)")
        return "\n".join(lines)

    def record_track(self, track_index: int):
        """Records MIDI from a selected input port into a specified track."""
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        try:
            input_ports = mido.get_input_names()
            if not input_ports:
                print("Error: No MIDI input ports found.")
                return

            print("Available MIDI input ports:")
            for i, port in enumerate(input_ports):
                print(f"[{i}] {port}")

            port_index = int(input("Choose a port to record from: "))
            port_name = input_ports[port_index]

        except (ValueError, IndexError):
            print("Error: Invalid selection.")
            return

        target_track = self.song.tracks[track_index]
        open_notes = {} # key: pitch, value: (start_time_sec, velocity)

        # Determine the starting beat for the new recording
        # For simplicity, we append after the last event in the track.
        start_beat = 0
        if target_track.events:
            last_event = target_track.events[-1]
            start_beat = last_event.start_time + last_event.notes[0].duration

        input("Press Enter to start recording...")

        with mido.open_input(port_name) as inport:
            print(f"Recording on '{port_name}'. Press Ctrl+C to stop.")
            recording_start_time_sec = time.time()

            try:
                for msg in inport:
                    now = time.time()
                    if msg.type == 'note_on' and msg.velocity > 0:
                        if msg.note not in open_notes:
                            open_notes[msg.note] = (now, msg.velocity)

                    elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                        if msg.note in open_notes:
                            start_time_sec, velocity = open_notes.pop(msg.note)
                            duration_sec = now - start_time_sec

                            # Convert time to beats
                            beats_per_second = self.song.tempo / 60
                            start_time_beats = start_beat + (start_time_sec - recording_start_time_sec) * beats_per_second
                            duration_beats = duration_sec * beats_per_second

                            note = Note(pitch=msg.note, velocity=velocity, duration=duration_beats)
                            event = Event(notes=[note], start_time=start_time_beats)
                            target_track.add_event(event)

                            print(f"Recorded note: {note.pitch}, duration: {duration_beats:.2f} beats")

            except KeyboardInterrupt:
                print("\nRecording stopped.")
