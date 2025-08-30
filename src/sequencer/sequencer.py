import time
import mido
import threading
from .models import Song, Track, Event, Note
from .midi_import import import_song
from .midi_export import export_to_midi

class Sequencer:
    def __init__(self, tempo: int = 120):
        self.song = Song(name="New Song", tempo=tempo)
        self.playback_state = "stopped"
        self.playback_thread = None
        self.open_ports = {}  # Changed from self.outport to a dict
        self.virtual_ports = []
        self.temporary_ports = []
        self._stop_event = threading.Event()
        self._run_event = threading.Event()
        self._run_event.set()

    def _all_notes_off(self):
        for port in self.open_ports.values():
            if port and not port.closed:
                for channel in range(16):
                    port.send(mido.Message('control_change', channel=channel, control=123, value=0))
        print("Sent all notes off to all open ports.")

    def set_tempo(self, tempo: int):
        # ... (no change)
        if tempo <= 0:
            raise ValueError("Tempo must be positive.")
        self.song.tempo = tempo
        print(f"Tempo set to {self.song.tempo} BPM.")

    def add_track(self, name: str, instrument: int = 0):
        # ... (no change)
        track = Track(name=name, instrument=instrument)
        self.song.add_track(track)
        print(f"Track '{name}' added.")

    def delete_track(self, track_index: int):
        # ... (no change)
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return False
        track_name = self.song.tracks[track_index].name
        self.song.tracks.pop(track_index)
        print(f"Track '{track_name}' deleted.")
        return True

    def assign_port(self, track_index: int, port_index: int):
        # ... (no change)
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        try:
            output_ports = mido.get_output_names()
            if not output_ports or not 0 <= port_index < len(output_ports):
                print("Error: Invalid port index.")
                return
            port_name = output_ports[port_index]
            self.song.tracks[track_index].output_port_name = port_name
            print(f"Assigned port '{port_name}' to track '{self.song.tracks[track_index].name}'.")
        except Exception as e:
            print(f"An error occurred while assigning port: {e}")

    def load_song(self, filepath: str):
        # ... (no change)
        try:
            self.song = import_song(filepath)
            print(f"Successfully loaded song from '{filepath}'.")
        except Exception as e:
            print(f"Error loading MIDI file: {e}")

    def save_song(self, filepath: str):
        # ... (no change)
        try:
            export_to_midi(self.song, filepath)
            print(f"Song successfully saved to '{filepath}'.")
        except Exception as e:
            print(f"Error saving MIDI file: {e}")

    def list_tracks(self) -> str:
        # ... (no change)
        if not self.song.tracks:
            return "No tracks in the song."
        lines = [f"Song: {self.song.name} | Tempo: {self.song.tempo} BPM"]
        lines.append("=" * 20)
        for i, track in enumerate(self.song.tracks):
            port_info = f" -> Port: {track.output_port_name}" if track.output_port_name else ""
            lines.append(f"[{i}] {track.name} (Instrument: {track.instrument}, {len(track.events)} events){port_info}")
        return "\n".join(lines)

    def list_ports(self) -> str:
        """Returns a formatted string of available MIDI input and output ports."""
        lines = []
        try:
            lines.append("Available MIDI Input Ports:")
            input_ports = mido.get_input_names()
            if input_ports:
                for i, port in enumerate(input_ports):
                    lines.append(f"  [{i}] {port}")
            else:
                lines.append("  (None found)")

            lines.append("\nAvailable MIDI Output Ports:")
            output_ports = mido.get_output_names()
            if output_ports:
                for i, port in enumerate(output_ports):
                    lines.append(f"  [{i}] {port}")
            else:
                lines.append("  (None found)")

            return "\n".join(lines)
        except Exception as e:
            return f"Error getting MIDI ports: {e}"

    def create_virtual_port(self, name: str):
        """Creates a virtual MIDI output port."""
        try:
            port = mido.open_output(name, virtual=True)
            self.virtual_ports.append(port)
            print(f"Created virtual MIDI port: '{name}'")
        except Exception as e:
            print(f"Error creating virtual port: {e}")

    def close_virtual_ports(self):
        """Closes all open virtual MIDI ports."""
        for port in self.virtual_ports:
            if not port.closed:
                port.close()
        print("Virtual ports closed.")

    def record_track(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return

        inport_name, outport_name = None, None
        try:
            # Select Input Port
            input_ports = mido.get_input_names()
            if not input_ports:
                print("Error: No MIDI input ports found.")
                return
            print("Available MIDI input ports:")
            for i, port in enumerate(input_ports):
                print(f"  [{i}] {port}")
            inport_idx = int(input("Choose a port to record from: "))
            inport_name = input_ports[inport_idx]

            # Select Output Port for MIDI Thru
            thru_choice = input("Enable MIDI Thru to an output port? [y/N] ").lower()
            if thru_choice == 'y':
                output_ports = mido.get_output_names()
                if not output_ports:
                    print("No MIDI output ports found for Thru.")
                else:
                    print("Available MIDI output ports:")
                    for i, port in enumerate(output_ports):
                        print(f"  [{i}] {port}")
                    outport_idx = int(input("Choose a port for MIDI Thru (or -1 to disable): "))
                    if 0 <= outport_idx < len(output_ports):
                        outport_name = output_ports[outport_idx]

        except (ValueError, IndexError):
            print("Error: Invalid selection.")
            return

        target_track = self.song.tracks[track_index]
        open_notes = {}
        start_beat = max((evt.start_time + evt.notes[0].duration for evt in target_track.events), default=0)

        outport = None
        try:
            with mido.open_input(inport_name) as inport:
                if outport_name:
                    outport = mido.open_output(outport_name)
                    print(f"Recording on '{inport_name}' with MIDI Thru to '{outport_name}'. Press Ctrl+C to stop.")
                else:
                    print(f"Recording on '{inport_name}'. Press Ctrl+C to stop.")

                input("Press Enter to start recording...")
                recording_start_time_sec = time.time()

                for msg in inport:
                    if outport:
                        outport.send(msg)

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
        except Exception as e:
            print(f"An error occurred during recording: {e}")
        finally:
            if outport:
                outport.close()
                print(f"Closed Thru port '{outport_name}'.")

    def _play_thread(self):
        """The actual playback logic that runs in a separate thread."""
        try:
            # 1. Build a master list of all MIDI messages from all tracks
            master_event_list = []
            ticks_per_beat = 480

            for track_idx, track in enumerate(self.song.tracks):
                if not track.output_port_name:
                    continue # Skip tracks without an assigned port

                # Add program change at the beginning of the track
                program_change_msg = mido.Message('program_change', channel=track_idx % 16, program=track.instrument, time=0)
                master_event_list.append({'tick': 0, 'track_idx': track_idx, 'message': program_change_msg})

                for event in track.events:
                    for note in event.notes:
                        start_tick = int(event.start_time * ticks_per_beat)
                        end_tick = start_tick + int(note.duration * ticks_per_beat)

                        note_on_msg = mido.Message('note_on', channel=track_idx % 16, note=note.pitch, velocity=note.velocity)
                        note_off_msg = mido.Message('note_off', channel=track_idx % 16, note=note.pitch, velocity=note.velocity)

                        master_event_list.append({'tick': start_tick, 'track_idx': track_idx, 'message': note_on_msg})
                        master_event_list.append({'tick': end_tick, 'track_idx': track_idx, 'message': note_off_msg})

            master_event_list.sort(key=lambda e: e['tick'])

            # 2. Play the master list
            print(f"Playing on {len(self.open_ports)} port(s)...")
            last_tick = 0
            mido_tempo = mido.bpm2tempo(self.song.tempo) # Microseconds per beat

            for event in master_event_list:
                self._run_event.wait()
                if self._stop_event.is_set(): break

                delta_ticks = event['tick'] - last_tick
                if delta_ticks > 0:
                    wait_time = mido.tick2second(delta_ticks, ticks_per_beat, mido_tempo)
                    # Interruptible sleep
                    while wait_time > 0:
                        self._run_event.wait()
                        if self._stop_event.is_set(): break
                        sleep_chunk = min(wait_time, 0.01)
                        time.sleep(sleep_chunk)
                        wait_time -= sleep_chunk

                if self._stop_event.is_set(): break

                # Route the message to the correct port
                track = self.song.tracks[event['track_idx']]
                port = self.open_ports.get(track.output_port_name)
                if port:
                    port.send(event['message'])

                last_tick = event['tick']

        except Exception as e:
            print(f"\nError during playback: {e}")
        finally:
            self._all_notes_off()
            # Close only the temporary ports that were opened for this playback
            for port in self.temporary_ports:
                if not port.closed:
                    port.close()
            self.temporary_ports = []
            self.open_ports.clear()
            self.playback_state = "stopped"
            print("Playback finished.")

    def play(self):
        if self.playback_state == "playing":
            print("Already playing.")
            return
        if self.playback_state == "paused":
            self.pause()  # Resume playback
            return

        self.open_ports.clear()
        self.temporary_ports = []

        required_ports = {t.output_port_name for t in self.song.tracks if t.output_port_name}
        if not required_ports:
            print("No tracks have an assigned output port. Use 'assign' command first.")
            return

        for name in required_ports:
            # Check if the required port name corresponds to one of our virtual ports
            found_virtual = False
            for vp in self.virtual_ports:
                if vp.name in name: # Substring check
                    self.open_ports[name] = vp
                    print(f"Using existing virtual port: {name}")
                    found_virtual = True
                    break

            if not found_virtual:
                # This is a hardware port, open it temporarily for this playback
                try:
                    temp_port = mido.open_output(name)
                    self.open_ports[name] = temp_port
                    self.temporary_ports.append(temp_port)
                    print(f"Opened temporary hardware port: {name}")
                except Exception as e:
                    print(f"Error opening hardware port '{name}': {e}")
                    # Clean up any other temporary ports that were successfully opened
                    for p in self.temporary_ports:
                        p.close()
                    self.temporary_ports = []
                    self.open_ports.clear()
                    return

        self._stop_event.clear()
        self._run_event.set()
        self.playback_state = "playing"
        self.playback_thread = threading.Thread(target=self._play_thread)
        self.playback_thread.start()

    def pause(self):
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
        if self.playback_state == "stopped":
            print("Already stopped.")
            return
        self._all_notes_off()
        self._stop_event.set()
        if self.playback_state == "paused":
            self._run_event.set()
        if self.playback_thread:
            self.playback_thread.join()
        self.playback_state = "stopped"
        print("Playback stopped.")
