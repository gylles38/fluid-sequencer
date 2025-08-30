import time
import mido
import threading
import json
from .models import Song, Track, Event, Note
from .midi_import import import_song
from .midi_export import export_to_midi

class Sequencer:
    def __init__(self, tempo: int = 120):
        self.song = Song(name="New Song", tempo=tempo)
        self.playback_state = "stopped"
        self.playback_thread = None
        self.open_ports = {}
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
        if tempo <= 0:
            raise ValueError("Tempo must be positive.")
        self.song.tempo = tempo
        print(f"Tempo set to {self.song.tempo} BPM.")

    def add_track(self, name: str, instrument: int = 0):
        track = Track(name=name, instrument=instrument)
        self.song.add_track(track)
        print(f"Track '{name}' added.")

    def delete_track(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return False
        track_name = self.song.tracks[track_index].name
        self.song.tracks.pop(track_index)
        print(f"Track '{track_name}' deleted.")
        return True

    def assign_port(self, track_index: int, port_name: str):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        self.song.tracks[track_index].output_port_name = port_name
        print(f"Assigned port '{port_name}' to track '{self.song.tracks[track_index].name}'.")

    def set_bank(self, track_index: int, msb: int, lsb: int = 0):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        if not 0 <= msb <= 127 and 0 <= lsb <= 127:
            print("Error: Bank values (MSB, LSB) must be between 0 and 127.")
            return

        track = self.song.tracks[track_index]
        track.bank_msb = msb
        track.bank_lsb = lsb
        print(f"Set bank for track '{track.name}' to MSB={msb}, LSB={lsb}.")

    def set_channel(self, track_index: int, channel: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        if not 1 <= channel <= 16:
            print("Error: MIDI channel must be between 1 and 16.")
            return

        track = self.song.tracks[track_index]
        track.channel = channel - 1 # Convert to 0-indexed for mido
        print(f"Set MIDI channel for track '{track.name}' to {channel}.")

    def set_program(self, track_index: int, program: int):
        if not 0 <= track_index < len(self.song.tracks):
            print("Error: Invalid track index.")
            return
        if not 0 <= program <= 127:
            print("Error: Program number must be between 0 and 127.")
            return

        track = self.song.tracks[track_index]
        track.instrument = program
        print(f"Set program for track '{track.name}' to {program + 1}.")

    def prime_all_tracks(self):
        """Sends the current program/bank state for all assigned tracks."""
        print("Priming all assigned tracks...")
        for track in self.song.tracks:
            if not track.output_port_name:
                continue

            port = None
            is_temp_port = False
            port_name = track.output_port_name
            try:
                # Find the port object (virtual or hardware)
                found_virtual = False
                for vp in self.virtual_ports:
                    if vp.name in port_name:
                        port = vp
                        found_virtual = True
                        break

                if not found_virtual:
                    port = mido.open_output(port_name)
                    is_temp_port = True

                if port:
                    print(f"  - Sending state for track '{track.name}' to '{port.name}' on Ch: {track.channel + 1}")
                    # Send bank select
                    if track.bank_msb is not None:
                        port.send(mido.Message('control_change', channel=track.channel, control=0, value=track.bank_msb))
                    if track.bank_lsb is not None:
                        port.send(mido.Message('control_change', channel=track.channel, control=32, value=track.bank_lsb))
                    # Send program change
                    port.send(mido.Message('program_change', channel=track.channel, program=track.instrument))
            except Exception as e:
                print(f"  - Could not send state to port '{port_name}': {e}")
            finally:
                if is_temp_port and port:
                    port.close()
        print("Priming complete.")

    def load_song(self, filepath: str):
        try:
            self.song = import_song(filepath)
            print(f"Successfully loaded song from '{filepath}'.")
        except Exception as e:
            print(f"Error loading MIDI file: {e}")

    def save_song(self, filepath: str):
        try:
            export_to_midi(self.song, filepath)
            print(f"Song successfully saved to '{filepath}'.")
        except Exception as e:
            print(f"Error saving MIDI file: {e}")

    def save_project(self, basename: str):
        midi_filepath = f"{basename}.mid"
        project_filepath = f"{basename}.proj.json"

        # 1. Save the MIDI data
        self.save_song(midi_filepath)

        # 2. Prepare and save the project configuration
        try:
            project_data = {
                "midi_file": midi_filepath,
                "virtual_ports": [vp.name for vp in self.virtual_ports],
                "track_assignments": [
                    {
                        "track_name": t.name,
                        "port_name": t.output_port_name
                    }
                    for t in self.song.tracks if t.output_port_name
                ]
            }
            with open(project_filepath, 'w') as f:
                json.dump(project_data, f, indent=4)

            print(f"Project configuration saved to '{project_filepath}'")

        except Exception as e:
            print(f"Error saving project file: {e}")

    def load_project(self, basename: str):
        project_filepath = f"{basename}.proj.json"
        try:
            with open(project_filepath, 'r') as f:
                project_data = json.load(f)

            # 1. Load the MIDI song data
            midi_file = project_data.get("midi_file")
            if not midi_file:
                print("Error: Project file is missing 'midi_file' key.")
                return
            self.load_song(midi_file)

            # 2. Recreate virtual ports
            self.close_virtual_ports() # Close any existing vports first
            self.virtual_ports = []
            for vp_name in project_data.get("virtual_ports", []):
                self.create_virtual_port(vp_name)

            # 3. Restore track assignments
            assignments = project_data.get("track_assignments", [])
            for assignment in assignments:
                track_name = assignment.get("track_name")
                port_name = assignment.get("port_name")
                if track_name and port_name:
                    # Find the track index by name
                    track_indices = [i for i, t in enumerate(self.song.tracks) if t.name == track_name]
                    if track_indices:
                        self.assign_port(track_indices[0], port_name)
                    else:
                        print(f"Warning: Could not find track '{track_name}' to assign port.")

            print(f"Successfully loaded project from '{project_filepath}'")

        except FileNotFoundError:
            print(f"Error: Project file not found at '{project_filepath}'")
        except Exception as e:
            print(f"Error loading project file: {e}")

    def list_tracks(self) -> str:
        if not self.song.tracks:
            return "No tracks in the song."
        lines = [f"Song: {self.song.name} | Tempo: {self.song.tempo} BPM"]
        lines.append("=" * 20)
        for i, track in enumerate(self.song.tracks):
            bank_info = ""
            if track.bank_msb is not None:
                bank_info = f", Bank: {track.bank_msb}:{track.bank_lsb or 0}"

            # Display channel and program as 1-indexed
            ch_info = f"Ch: {track.channel + 1}"
            prog_info = f"Prog: {track.instrument + 1}"

            port_info = f" -> Port: {track.output_port_name}" if track.output_port_name else ""
            lines.append(f"[{i}] {track.name} ({ch_info}, {prog_info}{bank_info}, {len(track.events)} events){port_info}")
        return "\n".join(lines)

    def list_ports(self) -> str:
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
            virtual_port_names = [vp.name for vp in self.virtual_ports]
            all_outputs = output_ports + virtual_port_names
            if all_outputs:
                for i, port in enumerate(all_outputs):
                    lines.append(f"  [{i}] {port}")
            else:
                lines.append("  (None found)")

            return "\n".join(lines)
        except Exception as e:
            return f"Error getting MIDI ports: {e}"

    def create_virtual_port(self, name: str):
        try:
            port = mido.open_output(name, virtual=True)
            self.virtual_ports.append(port)
            print(f"Created virtual MIDI port: '{name}'")
        except Exception as e:
            print(f"Error creating virtual port: {e}")

    def close_virtual_ports(self):
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
            input_ports = mido.get_input_names()
            if not input_ports:
                print("Error: No MIDI input ports found.")
                return
            print("Available MIDI input ports:")
            for i, port in enumerate(input_ports):
                print(f"  [{i}] {port}")
            inport_idx = int(input("Choose a port to record from: "))
            inport_name = input_ports[inport_idx]

            thru_choice = input("Enable MIDI Thru to an output port? [y/N] ").lower()
            if thru_choice == 'y':
                hardware_ports = mido.get_output_names()
                virtual_port_names = [vp.name for vp in self.virtual_ports]
                all_outputs = hardware_ports + virtual_port_names
                if not all_outputs:
                    print("No MIDI output ports found for Thru.")
                else:
                    print("Available MIDI output ports:")
                    for i, port in enumerate(all_outputs):
                        print(f"  [{i}] {port}")
                    outport_idx = int(input("Choose a port for MIDI Thru (or -1 to disable): "))
                    if 0 <= outport_idx < len(all_outputs):
                        outport_name = all_outputs[outport_idx]

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
        try:
            master_event_list = []
            ticks_per_beat = 480

            for track_idx, track in enumerate(self.song.tracks):
                if not track.output_port_name:
                    continue

                # Add bank select and program change messages at the beginning of the track
                if track.bank_msb is not None:
                    master_event_list.append({'tick': 0, 'track_idx': track_idx, 'message': mido.Message('control_change', channel=track.channel, control=0, value=track.bank_msb, time=0)})
                if track.bank_lsb is not None:
                    master_event_list.append({'tick': 0, 'track_idx': track_idx, 'message': mido.Message('control_change', channel=track.channel, control=32, value=track.bank_lsb, time=0)})

                program_change_msg = mido.Message('program_change', channel=track.channel, program=track.instrument, time=0)
                master_event_list.append({'tick': 0, 'track_idx': track_idx, 'message': program_change_msg})

                for event in track.events:
                    for note in event.notes:
                        start_tick = int(event.start_time * ticks_per_beat)
                        end_tick = start_tick + int(note.duration * ticks_per_beat)
                        note_on_msg = mido.Message('note_on', channel=track.channel, note=note.pitch, velocity=note.velocity)
                        note_off_msg = mido.Message('note_off', channel=track.channel, note=note.pitch, velocity=note.velocity)
                        master_event_list.append({'tick': start_tick, 'track_idx': track_idx, 'message': note_on_msg})
                        master_event_list.append({'tick': end_tick, 'track_idx': track_idx, 'message': note_off_msg})

            master_event_list.sort(key=lambda e: e['tick'])
            print(f"Playing on {len(self.open_ports)} port(s)...")
            last_tick = 0
            mido_tempo = mido.bpm2tempo(self.song.tempo)

            for event in master_event_list:
                self._run_event.wait()
                if self._stop_event.is_set(): break
                delta_ticks = event['tick'] - last_tick
                if delta_ticks > 0:
                    wait_time = mido.tick2second(delta_ticks, ticks_per_beat, mido_tempo)
                    time.sleep(wait_time)
                track = self.song.tracks[event['track_idx']]
                port = self.open_ports.get(track.output_port_name)
                if port:
                    port.send(event['message'])
                last_tick = event['tick']
        except Exception as e:
            print(f"\nError during playback: {e}")
        finally:
            self._all_notes_off()
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
            self.pause()
            return

        self.open_ports.clear()
        self.temporary_ports = []
        required_ports = {t.output_port_name for t in self.song.tracks if t.output_port_name}
        if not required_ports:
            print("No tracks have an assigned output port. Use 'assign' command first.")
            return

        for name in required_ports:
            found_virtual = False
            for vp in self.virtual_ports:
                if vp.name in name:
                    self.open_ports[name] = vp
                    print(f"Using existing virtual port: {name}")
                    found_virtual = True
                    break
            if not found_virtual:
                try:
                    temp_port = mido.open_output(name)
                    self.open_ports[name] = temp_port
                    self.temporary_ports.append(temp_port)
                    print(f"Opened temporary hardware port: {name}")
                except Exception as e:
                    print(f"Error opening hardware port '{name}': {e}")
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
