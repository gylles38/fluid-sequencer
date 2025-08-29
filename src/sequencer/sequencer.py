import time
import mido
import threading
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
        self.playback_state = "stopped"  # "stopped", "playing", "paused"
        self.playback_thread = None
        self.outport = None
        self._stop_event = threading.Event()
        self._run_event = threading.Event()
        self._run_event.set()

    def _all_notes_off(self):
        """Sends an 'all notes off' message to all channels on the current output port."""
        if self.outport and not self.outport.closed:
            for channel in range(16):
                self.outport.send(mido.Message('control_change', channel=channel, control=123, value=0))
            print("Sent all notes off.")

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
        # ... (record_track implementation remains the same)
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
        open_notes = {}
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
                            beats_per_second = self.song.tempo / 60
                            start_time_beats = start_beat + (start_time_sec - recording_start_time_sec) * beats_per_second
                            duration_beats = duration_sec * beats_per_second
                            note = Note(pitch=msg.note, velocity=velocity, duration=duration_beats)
                            event = Event(notes=[note], start_time=start_time_beats)
                            target_track.add_event(event)
                            print(f"Recorded note: {note.pitch}, duration: {duration_beats:.2f} beats")
            except KeyboardInterrupt:
                print("\nRecording stopped.")

    def _play_thread(self):
        """The actual playback logic that runs in a separate thread."""
        try:
            temp_mid = mido.MidiFile(type=1, ticks_per_beat=480)
            # ... (MIDI file generation logic remains the same)
            tempo_track = mido.MidiTrack()
            temp_mid.tracks.append(tempo_track)
            tempo_track.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(self.song.tempo)))
            for i, track in enumerate(self.song.tracks):
                midi_track = mido.MidiTrack()
                temp_mid.tracks.append(midi_track)
                channel = i % 16
                midi_track.append(mido.Message('program_change', channel=channel, program=track.instrument, time=0))
                all_midi_events = []
                for event in track.events:
                    for note in event.notes:
                        start_tick = int(event.start_time * 480)
                        end_tick = start_tick + int(note.duration * 480)
                        all_midi_events.append({'tick': start_tick, 'type': 'note_on', 'pitch': note.pitch, 'velocity': note.velocity})
                        all_midi_events.append({'tick': end_tick, 'type': 'note_off', 'pitch': note.pitch, 'velocity': note.velocity})
                all_midi_events.sort(key=lambda e: e['tick'])
                last_tick = 0
                for event in all_midi_events:
                    delta_ticks = event['tick'] - last_tick
                    midi_track.append(mido.Message(event['type'], channel=channel, note=event['pitch'], velocity=event['velocity'], time=delta_ticks))
                    last_tick = event['tick']

            print(f"Playing on '{self.outport.name}'...")
            for msg in temp_mid:
                self._run_event.wait()
                if self._stop_event.is_set():
                    break
                if msg.time > 0:
                    time.sleep(msg.time)
                if not msg.is_meta:
                    self.outport.send(msg)
        except Exception as e:
            print(f"\nError during playback: {e}")
        finally:
            self._all_notes_off()
            self.outport.close()
            self.outport = None
            self.playback_state = "stopped"
            print("Playback finished.")

    def play(self):
        """Starts playback of the song in a new thread."""
        if self.playback_state == "playing":
            print("Already playing.")
            return
        if self.playback_state == "paused":
            self.pause()
            return
        if not self.song.tracks:
            print("The song is empty. Add or load a track first.")
            return
        try:
            output_ports = mido.get_output_names()
            if not output_ports:
                print("Error: No MIDI output ports found.")
                return
            print("Available MIDI output ports:")
            for i, port in enumerate(output_ports):
                print(f"[{i}] {port}")
            port_index = int(input("Choose a port to play on: "))
            port_name = output_ports[port_index]
            self.outport = mido.open_output(port_name)
        except (ValueError, IndexError, mido.PortNotOpenError) as e:
            print(f"Error opening port: {e}")
            return
        self._stop_event.clear()
        self._run_event.set()
        self.playback_state = "playing"
        self.playback_thread = threading.Thread(target=self._play_thread)
        self.playback_thread.start()

    def pause(self):
        """Pauses or resumes playback."""
        if self.playback_state == "stopped":
            print("Nothing to pause.")
            return
        if self.playback_state == "playing":
            self._all_notes_off()
            self._run_event.clear()
            self.playback_state = "paused"
            print("Playback paused.")
        elif self.playback_state == "paused":
            self._run_event.set()
            self.playback_state = "playing"
            print("Resuming playback...")

    def stop(self):
        """Stops playback."""
        if self.playback_state == "stopped":
            print("Already stopped.")
            return
        self._all_notes_off()
        self._stop_event.set()
        if self.playback_state == "paused":
            self._run_event.set()
        self.playback_thread.join()
        self.playback_state = "stopped"
        print("Playback stopped.")
