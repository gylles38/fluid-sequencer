from .midi_export import export_to_midi
from .midi_import import import_song
from .midi_import_project import import_midi_to_project
from .midi_export_project import export_midi_from_project
from .models import AnyTrack, AudioTrack, AutomationTrack, AutomationPoint, CCMessage, Event, MidiTrack, Note, Song, MidiMapping
from .config import MidiConfig
from .config_manager import ConfigManager
from .terminal_input import cancellable_input, UserInputCancelled
from copy import deepcopy
from dataclasses import dataclass, asdict, is_dataclass, fields
import json
import math
import mido
import jack
from mido import get_input_names, get_output_names, open_output # type: ignore
import numpy as np
from pydub import AudioSegment
import os
import signal
import queue
import sys
import subprocess
import tempfile
import socket
import threading
import time
from typing import List, Optional, Any, Dict
from functools import wraps
from contextlib import contextmanager

@contextmanager
def suppress_stdout_stderr():
    """A context manager for suppressing stdout and stderr."""
    with open(os.devnull, 'w') as fnull:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = fnull
        sys.stderr = fnull
        try:
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

@dataclass
class ActiveAudioProcess:
    process: subprocess.Popen
    socket_path: str
    track_index: int
    temp_filepath: Optional[str] = None # No longer mixing to a temp file


class CustomSongEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Song):
            return {
                '__type__': 'Song',
                'name': o.name,
                'tempo': o.tempo,
                'time_signature_numerator': o.time_signature_numerator,
                'time_signature_denominator': o.time_signature_denominator,
                'ticks_per_beat': o.ticks_per_beat,
                'sync_offset_sec': o.sync_offset_sec,
                'tracks': o.tracks,
                'midi_mappings': o.midi_mappings,
                'metronome_enabled': o.metronome_enabled,
                'metronome_port_name': o.metronome_port_name,
                'metronome_volume': o.metronome_volume,
                'metronome_pan': o.metronome_pan,
                'carla_project_path': o.carla_project_path,
                'aj_snapshot_path': o.aj_snapshot_path,
            }
        if isinstance(o, MidiTrack):
            return {
                '__type__': 'MidiTrack',
                'name': o.name,
                'is_muted': o.is_muted,
                'is_solo': o.is_solo,
                'is_metronome': o.is_metronome,
                'channel': o.channel,
                'volume': o.volume,
                'pan': o.pan,
                'velocity': o.velocity,
                'events': o.events,
                'instrument': o.instrument,
                'bank_msb': o.bank_msb,
                'bank_lsb': o.bank_lsb,
                'output_port_name': o.output_port_name,
                'input_port_name': o.input_port_name,
                'record_mode': o.record_mode,
            }
        if isinstance(o, AudioTrack):
            return {
                '__type__': 'AudioTrack',
                'name': o.name,
                'filepath': o.filepath,
                'is_muted': o.is_muted,
                'is_solo': o.is_solo,
                'start_time': o.start_time,
                'volume': o.volume,
                'pan': o.pan,
                'channels': o.channels,
                'native_tempo': o.native_tempo,
                'duration_beats': o.duration_beats,
            }
        if isinstance(o, AutomationTrack):
            return {
                '__type__': 'AutomationTrack',
                'name': o.name,
                'target_track_index': o.target_track_index,
                'is_muted': o.is_muted,
                'is_solo': o.is_solo,
                'points': o.points,
            }
        if is_dataclass(o):
            d = {f.name: getattr(o, f.name) for f in fields(o)}
            d['__type__'] = o.__class__.__name__
            return d
        return super().default(o)

def song_decoder(d):
    if '__type__' in d:
        type_name = d.pop('__type__')
        cls = None
        # Check current module (sequencer.sequencer)
        if __name__ in sys.modules:
            cls = getattr(sys.modules[__name__], type_name, None)

        # Fallback to models module if not found in current
        if not cls and 'sequencer.models' in sys.modules:
            cls = getattr(sys.modules['sequencer.models'], type_name, None)

        if cls:
            return cls(**d)
    return d


from kivy.properties import NumericProperty, StringProperty, BooleanProperty
from kivy.event import EventDispatcher
from kivy.clock import Clock

class JackManager:
    def __init__(self, sequencer: 'Sequencer'):
        self.sequencer = sequencer
        self.jack_client = None
        self.is_running = False
        self.last_beat = 0.0
        self.last_transport_state = jack.STOPPED
        self.open_ports = {}
        self.next_event_indices = []
        self._active_notes = {} # key: (track_idx, note_pitch), value: end_beat
        self._metronome_notes_to_turn_off = []
        self.active_audio_processes: List[ActiveAudioProcess] = []
        self.process_lock = threading.Lock()
        self.sync_lock = threading.Lock()
        self._display_thread = None
        self._display_stop_event = threading.Event()
        self.automation_events = []
        self.next_automation_event_index = 0
        self.event_to_ignore: Optional[dict] = None

        # --- Dynamic Audio Correction ---
        self.CORRECTION_GAIN = 0.02
        self.CORRECTION_THRESHOLD = 0.03 # 30ms
        self._correction_thread = None
        self._correction_stop_event = threading.Event()
        self.last_applied_speeds = {}

    def find_port_by_name(self, pattern):
        """
        Cherche un port JACK complet qui contient le 'pattern' donné.
        Retourne le nom complet du premier port trouvé, ou None.
        """
        try:
            # On demande à JACK/PipeWire la liste de tous les ports
            result = subprocess.run(["jack_lsp"], capture_output=True, text=True, check=False)
            all_ports = result.stdout.splitlines()

            for port in all_ports:
                # On cherche une correspondance partielle (ex: "RtMidiOut" dans le nom complet)
                if pattern in port:
                    return port.strip() # On nettoie les espaces/sauts de ligne

            return None
        except FileNotFoundError:
            print("Erreur: commande 'jack_lsp' introuvable.", file=sys.stderr)
            return None

    def auto_connect_dynamic(self, src_keyword, dest_keyword):
        """
        Connecte deux ports en utilisant des mots-clés partiels.
        """
        print(f"--- Attempting auto-connect: '{src_keyword}' -> '{dest_keyword}' ---")

        # 1. Recherche des noms complets
        full_source = self.find_port_by_name(src_keyword)
        full_dest = self.find_port_by_name(dest_keyword)

        if not full_source:
            print(f"Info: Source port not found with keyword: '{src_keyword}'")
            return
        if not full_dest:
            print(f"Info: Destination port not found with keyword: '{dest_keyword}'")
            return

        print(f"Ports identified:\n   Source: {full_source}\n   Dest  : {full_dest}")

        # 2. Tentative de connexion via jack_connect
        try:
            res = subprocess.run(
                ["jack_connect", full_source, full_dest],
                capture_output=True,
                text=True,
                check=False
            )

            if res.returncode == 0:
                print("Connection successful!")
            else:
                # If error (often because already connected), we display the message
                # PipeWire often returns an error if it's already connected, it's not serious.
                if "exists" in res.stderr:
                     print("Already connected.")
                else:
                     print(f"Connection warning: {res.stderr.strip()}")

        except FileNotFoundError:
            print("Erreur: commande 'jack_connect' introuvable.", file=sys.stderr)

    def disconnect_dynamic(self, src_keyword, dest_keyword):
        """
        Disconnects two ports using partial keywords.
        """
        if not src_keyword or not dest_keyword:
            return

        full_source = self.find_port_by_name(src_keyword)
        full_dest = self.find_port_by_name(dest_keyword)

        if not full_source or not full_dest:
            return

        try:
            subprocess.run(
                ["jack_disconnect", full_source, full_dest],
                capture_output=True,
                text=True,
                check=False
            )
        except FileNotFoundError:
            print("Erreur: commande 'jack_disconnect' introuvable.", file=sys.stderr)

    def get_midi_input_ports(self):
        """
        Retourne une liste de tous les ports d'entrée MIDI JACK disponibles (se terminant par :events-in).
        """
        try:
            result = subprocess.run(["jack_lsp"], capture_output=True, text=True, check=False)
            all_ports = result.stdout.splitlines()
            midi_input_ports = [port.strip() for port in all_ports if port.strip().endswith(':events-in')]
            return midi_input_ports
        except FileNotFoundError:
            print("Erreur: commande 'jack_lsp' introuvable.", file=sys.stderr)
            return []

    def open_midi_port(self, port_name: str):
        """Opens a MIDI port if it's not already open."""
        if port_name in self.open_ports and not self.open_ports[port_name].closed:
            return  # Port is already open

        vp = next((p for p in self.sequencer.virtual_ports if p.name == port_name), None)
        if vp:
            self.open_ports[port_name] = vp
        else:
            try:
                self.open_ports[port_name] = mido.open_output(port_name)
                print(f"Successfully opened MIDI port '{port_name}'")
            except Exception as e:
                print(f"Could not open MIDI port '{port_name}': {e}")

    def close_midi_port(self, port_name: str):
        """Closes a MIDI port if it's open and not used by other tracks."""
        # First, check if any other track is still using this port
        for track in self.sequencer.song.tracks:
            if isinstance(track, MidiTrack) and track.output_port_name == port_name:
                return  # Port is still in use, do not close

        if port_name in self.open_ports:
            port = self.open_ports[port_name]
            # Don't close virtual ports managed elsewhere
            if not port.closed and port not in self.sequencer.virtual_ports:
                try:
                    port.close()
                    print(f"Successfully closed MIDI port '{port_name}'")
                except Exception as e:
                    print(f"Error closing MIDI port '{port_name}': {e}")
            # Remove from the dictionary regardless
            del self.open_ports[port_name]

    def _audio_correction_loop(self):
        """
        A loop in a separate thread that periodically checks the audio playback position
        against the master JACK transport and applies a speed correction if they drift.
        This is a non-real-time loop to avoid blocking the JACK audio callback.
        """
        while not self._correction_stop_event.is_set():
            try:
                if (not self.jack_client or
                        not self.is_running or
                        self.jack_client.transport_state != jack.ROLLING):
                    time.sleep(0.5)
                    continue

                # --- Get Master Time from JACK ---
                state, pos_struct = self.jack_client.transport_query_struct()
                pos_dict = jack.position2dict(pos_struct)
                frame = pos_dict.get('frame', 0)
                samplerate = self.jack_client.samplerate
                if samplerate <= 0:
                    time.sleep(0.5)
                    continue

                master_time_sec = frame / samplerate
                beats_per_second = self.sequencer.song.tempo / 60.0
                if beats_per_second <= 0:
                    time.sleep(0.5)
                    continue

                # --- Compare each audio track to the master time ---
                with self.process_lock:
                    # Create a copy to avoid issues if the list changes during iteration
                    audio_processes = list(self.active_audio_processes)

                for ap in audio_processes:
                    track = self.sequencer.song.tracks[ap.track_index]
                    if not isinstance(track, AudioTrack):
                        continue

                    # Query mpv for its current playback time
                    query_time = {"command": ["get_property", "playback-time"]}
                    time_response = self._query_ipc_command(ap.socket_path, query_time, timeout=0.05)

                    if not (time_response and time_response.get("error") == "success"):
                        continue # Skip if we can't get the time

                    current_mpv_time = time_response.get("data", 0.0)
                    if current_mpv_time is None: continue

                    # Calculate where mpv *should* be
                    track_start_sec = (track.start_time / beats_per_second)
                    expected_mpv_time = master_time_sec - track_start_sec

                    if expected_mpv_time < 0:
                        continue # This track hasn't started yet

                    # --- Calculate Error and Apply Correction ---
                    error_sec = expected_mpv_time - current_mpv_time

                    base_speed = 1.0
                    if track.native_tempo is not None and track.native_tempo > 0:
                        base_speed = self.sequencer.song.tempo / track.native_tempo

                    new_speed = base_speed

                    if abs(error_sec) > self.CORRECTION_THRESHOLD:
                        # Apply proportional correction
                        speed_correction = 1.0 + (error_sec * self.CORRECTION_GAIN)
                        # Clamp the correction to prevent extreme, audible speed changes
                        speed_correction = max(0.95, min(1.05, speed_correction))
                        new_speed = base_speed * speed_correction

                    # Round to avoid floating point noise causing unnecessary IPC commands
                    new_speed = round(new_speed, 5)

                    last_speed = self.last_applied_speeds.get(ap.track_index)

                    if last_speed is None or not math.isclose(last_speed, new_speed, rel_tol=1e-4):
                        command = {"command": ["set_property", "speed", new_speed]}
                        if self._send_ipc_command(ap.socket_path, command):
                            self.last_applied_speeds[ap.track_index] = new_speed

            except jack.JackError:
                # This can happen during shutdown, it's safe to just exit the loop
                break
            except Exception as e:
                print(f"\nError in audio correction loop: {e}", file=sys.stderr)

            # The interval at which the correction is checked and applied
            time.sleep(0.5)

    def _display_loop(self):
        """A loop in a separate thread to display the current transport position."""
        last_pos_str = ""
        while not self._display_stop_event.is_set():
            try:
                if self.jack_client and self.jack_client.transport_state == jack.ROLLING:
                    # Display the sequencer's internal beat counter, which drives the notes.
                    current_beat = self.last_beat
                    pos_str = self.sequencer._format_beats_to_position(current_beat)

                    if pos_str != last_pos_str:
                        sys.stdout.write(f"\r  {pos_str}  ")
                        sys.stdout.flush()
                        last_pos_str = pos_str
                else:
                    if last_pos_str != "":
                        # Clear the line when transport stops
                        sys.stdout.write("\r" + " " * (len(last_pos_str) + 4) + "\r")
                        sys.stdout.flush()
                        last_pos_str = ""

            except jack.JackError:
                break
            except Exception as e:
                print(f"\nError in display loop: {e}", file=sys.stderr)
                break

            time.sleep(0.05)

    def _prepare_automation_events(self):
        """Generates and sorts all automation events for the song."""
        self.automation_events.clear()
        tracks = self.sequencer.song.tracks
        is_any_track_soloed = any(t.is_solo for t in tracks if hasattr(t, 'is_solo'))
        for track in tracks:
            if isinstance(track, AutomationTrack):
                # Only process automation for tracks that should be audible
                target_track_index = track.target_track_index
                if 0 <= target_track_index < len(tracks):
                    target_track = tracks[target_track_index]

                    # An automation track itself can be muted/soloed
                    auto_track_should_play = (not hasattr(track, 'is_solo') or track.is_solo or not is_any_track_soloed) and \
                                             (not hasattr(track, 'is_muted') or not track.is_muted)

                    # The target track can also be muted/soloed
                    target_track_should_play = (not hasattr(target_track, 'is_solo') or target_track.is_solo or not is_any_track_soloed) and \
                                               (not hasattr(target_track, 'is_muted') or not target_track.is_muted)

                    if auto_track_should_play and target_track_should_play:
                        generated = self.sequencer._generate_automation_events(track)
                        self.automation_events.extend(generated)

        self.automation_events.sort(key=lambda e: e['time'])

    def start(self):
            if self.is_running:
                print("JACK client is already running.")
                return

            try:
                self.jack_client = jack.Client(f"{self.sequencer.song.name}-sequencer")
                tracks = self.sequencer.song.tracks

                # --- MIDI Port Setup ---
                self.open_ports.clear()
                required_ports = {track.output_port_name for track in tracks if isinstance(track, MidiTrack) and track.output_port_name}
                if self.sequencer.song.metronome_port_name:
                    required_ports.add(self.sequencer.song.metronome_port_name)

                for name in required_ports:
                    vp = next((p for p in self.sequencer.virtual_ports if p.name == name), None)
                    if vp:
                        self.open_ports[name] = vp
                    else:
                        try:
                            self.open_ports[name] = mido.open_output(name)
                        except Exception as e:
                            print(f"Could not open MIDI port '{name}': {e}")

                # --- Audio Track Setup ---
                with self.process_lock:
                    self.active_audio_processes.clear()
                    for i, track in enumerate(tracks):
                        if isinstance(track, AudioTrack):
                            self._launch_audio_track_player(track, i)

                # --- Automation Setup ---
                self._prepare_automation_events()

                # --- Wait for audio players to be ready ---
                if sys.platform != "win32":
                    print("Waiting for audio player sockets...")
                    with self.process_lock:
                        audio_processes = list(self.active_audio_processes)

                    for ap in audio_processes:
                        track = tracks[ap.track_index]
                        max_wait_time = 5.0  # 5 secondes par socket
                        start_time = time.time()
                        is_ready = False
                        while time.time() - start_time < max_wait_time:
                            if self._is_socket_responsive(ap.socket_path):
                                print(f"  - Socket for track '{track.name}' is responsive.")
                                is_ready = True
                                break
                            time.sleep(0.1)

                        if not is_ready:
                            print(f"Warning: Timed out waiting for audio player for track '{track.name}'. Playback may fail for this track.", file=sys.stderr)
                else:
                    # On Windows, a simple delay is the most practical approach.
                    time.sleep(2.0)

                # --- Prime Audio Tracks Immediately After They Are Ready ---
                print("Priming audio tracks with initial state...")
                is_any_track_soloed = any(t.is_solo for t in tracks if hasattr(t, 'is_solo'))

                with self.process_lock:
                    for ap in self.active_audio_processes:
                        track = tracks[ap.track_index]
                        if isinstance(track, AudioTrack):
                            print(f"  - Priming Audio track '{track.name}'")
                            # Ajout de vérifications pour voir si les commandes réussissent
                            if not self._send_ipc_command(ap.socket_path, {"command": ["set_property", "volume", track.volume * 100]}):
                                print(f"    - Warning: Failed to set volume for track '{track.name}'.", file=sys.stderr)
                            if not self._send_ipc_command(ap.socket_path, {"command": ["set_property", "balance", track.pan]}):
                                print(f"    - Warning: Failed to set pan for track '{track.name}'.", file=sys.stderr)
                            
                            should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                            if not self._send_ipc_command(ap.socket_path, {"command": ["set_property", "mute", not should_be_audible]}):
                                print(f"    - Warning: Failed to set mute state for track '{track.name}'.", file=sys.stderr)

                self.jack_client.set_process_callback(self._process_callback)
                self.jack_client.set_timebase_callback(self._time_callback)
                self.jack_client.activate()
                self.is_running = True

                # --- Initial Transport Sync ---
                state, pos_struct = self.jack_client.transport_query_struct()
                pos_dict = jack.position2dict(pos_struct)
                self.sequencer.song.tempo = pos_dict.get('beats_per_minute', self.sequencer.song.tempo)

                frame = pos_dict.get('frame', 0)
                samplerate = self.jack_client.samplerate
                beats_per_second = self.sequencer.song.tempo / 60.0

                if samplerate > 0 and beats_per_second > 0:
                    initial_beat = (frame / samplerate) * beats_per_second
                    self._sync_playhead_to_beat(initial_beat)
                else:
                    self._sync_playhead_to_beat(0.0)

                if not self.sequencer.gui_mode:
                    self._display_stop_event.clear()
                    self._display_thread = threading.Thread(target=self._display_loop)
                    self._display_thread.daemon = True
                    self._display_thread.start()

                # --- Start the correction thread ---
                self._correction_stop_event.clear()
                self._correction_thread = threading.Thread(target=self._audio_correction_loop)
                self._correction_thread.daemon = True
                self._correction_thread.start()

                print("JACK client started and activated.")
            except jack.JackError as e:
                print(f"Error starting JACK client: {e}")
                if self.jack_client:
                    self.jack_client.close()
                self.jack_client = None

    def stop(self):
        """Stops the JACK client, its threads, and all related processes."""
        if not self.is_running:
            return

        # Stop the display thread if it's running
        if self._display_thread and self._display_thread.is_alive():
            self._display_stop_event.set()
            self._display_thread.join(timeout=1.0)
            self._display_thread = None

        # Stop the correction thread
        if self._correction_thread and self._correction_thread.is_alive():
            self._correction_stop_event.set()
            self._correction_thread.join(timeout=1.0)
            self._correction_thread = None

        # Deactivate and close the JACK client
        if self.jack_client:
            try:
                self.jack_client.deactivate()
                self.jack_client.close()
                print("JACK client deactivated and closed.")
            except jack.JackError as e:
                print(f"Error during JACK client shutdown: {e}", file=sys.stderr)
            finally:
                self.jack_client = None

        # Shut down all external audio player processes
        self._shutdown_audio_processes()
        print("Audio processes terminated.")

        # Close all open MIDI ports
        for name, port in self.open_ports.items():
            try:
                if not port.closed:
                    port.close()
            except Exception as e:
                print(f"Error closing MIDI port '{name}': {e}", file=sys.stderr)
        self.open_ports.clear()

        # Reset the state
        self.is_running = False
        self._active_notes.clear()

    def get_current_beat(self) -> float:
        """Retourne la position actuelle du transport en beats."""
        return self.last_beat

    def silence_all_midi_notes(self):
        """Sends note_off messages for all currently playing MIDI notes."""
        if not self._active_notes:
            return

        for (track_idx, pitch), end_beat in list(self._active_notes.items()):
            if 0 <= track_idx < len(self.sequencer.song.tracks):
                track = self.sequencer.song.tracks[track_idx]
                if isinstance(track, MidiTrack) and track.output_port_name in self.open_ports:
                    port = self.open_ports[track.output_port_name]
                    if port and not port.closed:
                        note_off_msg = mido.Message('note_off', channel=track.channel, note=pitch, velocity=0)
                        port.send(note_off_msg)

        self._active_notes.clear()

    def _send_ipc_command(self, socket_path, command_data) -> bool:
        try:
            if sys.platform == "win32":
                pipe_name = r'\\.\pipe\\' + os.path.basename(socket_path)
                with open(pipe_name, 'w') as pipe:
                    pipe.write(json.dumps(command_data) + '\n')
            else:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                    s.settimeout(0.1)
                    s.connect(socket_path)
                    s.sendall(json.dumps(command_data).encode('utf-8') + b'\n')
            return True
        except (socket.timeout, ConnectionRefusedError, FileNotFoundError, BrokenPipeError):
            return False
        except Exception as e:
            print(f"Error sending IPC command to {socket_path}: {e}", file=sys.stderr)
            return False

    def _query_ipc_command(self, socket_path, command_data, timeout=0.2):
        """Sends a command and waits for a JSON response."""
        try:
            if sys.platform == "win32":
                # For Windows, named pipes behave a bit differently and this simple read/write might not be sufficient.
                # Given the bug report context (unix sockets), we'll focus on the unix implementation.
                # A simple connect check is the fallback.
                return {"error": "success"} if self._is_socket_connectable(socket_path) else None

            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                s.connect(socket_path)
                s.sendall(json.dumps(command_data).encode('utf-8') + b'\n')

                # Read the response
                response_data = b""
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break  # Connection closed
                    response_data += chunk
                    if b'\n' in response_data:
                        break # Full response received

                # There could be multiple JSON objects, we only care about the first
                response_line = response_data.split(b'\n', 1)[0]
                response_json = json.loads(response_line.decode('utf-8'))
                return response_json

        except (socket.timeout, ConnectionRefusedError, FileNotFoundError, BrokenPipeError, json.JSONDecodeError):
            return None
        except Exception:
            # Silently fail, as this is just a check.
            return None

    def _is_socket_responsive(self, socket_path: str) -> bool:
        """Checks if an mpv socket is not just connectable, but also responding to commands."""
        command = {"command": ["get_property", "pid"]}
        response = self._query_ipc_command(socket_path, command)
        return response is not None and response.get("error") == "success"

    def _is_socket_connectable(self, socket_path: str) -> bool:
        if sys.platform == "win32":
            return True
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect(socket_path)
            return True
        except (socket.timeout, ConnectionRefusedError, FileNotFoundError):
            return False

    def set_all_audio_pause_state(self, is_paused: bool):
        with self.process_lock:
            for ap in self.active_audio_processes:
                command = {"command": ["set_property", "pause", is_paused]}
                self._send_ipc_command(ap.socket_path, command)

    def launch_player_for_track(self, track: AudioTrack, track_index: int):
        """Launches, waits for, and primes a player for a single audio track."""
        print(f"Dynamically launching player for new track '{track.name}'...")
        with self.process_lock:
            # First, check if a process for this track index somehow already exists.
            if any(p.track_index == track_index for p in self.active_audio_processes):
                print(f"Warning: Player for track index {track_index} already exists. Aborting launch.")
                return

            # Launch the mpv process
            self._launch_audio_track_player(track, track_index)

            # Find the process we just launched
            ap = next((p for p in self.active_audio_processes if p.track_index == track_index), None)
            if not ap:
                print(f"Error: Failed to find the newly launched process for track index {track_index}.")
                return

        # Wait for the socket to become responsive
        max_wait_time = 5.0
        start_time = time.time()
        is_ready = False
        while time.time() - start_time < max_wait_time:
            if self._is_socket_responsive(ap.socket_path):
                print(f"  - Socket for track '{track.name}' is responsive.")
                is_ready = True
                break
            time.sleep(0.1)

        if not is_ready:
            print(f"Warning: Timed out waiting for audio player for track '{track.name}'.")
            return

        # Prime the new player with initial settings
        print(f"  - Priming new track '{track.name}' with initial state...")
        is_any_track_soloed = any(t.is_solo for t in self.sequencer.song.tracks if hasattr(t, 'is_solo'))
        should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted

        self._send_ipc_command(ap.socket_path, {"command": ["set_property", "volume", track.volume * 100]})
        self._send_ipc_command(ap.socket_path, {"command": ["set_property", "balance", track.pan]})
        self._send_ipc_command(ap.socket_path, {"command": ["set_property", "mute", not should_be_audible]})

        # If playback is active, seek the new track to the current position and unpause
        if self.jack_client and self.jack_client.transport_state == jack.ROLLING:
            current_beat = self.get_current_beat()
            self.seek_audio_to_beat(current_beat)
            self.set_all_audio_pause_state(False)
            print(f"  - New track '{track.name}' synced to current playback position.")

    def _launch_audio_track_player(self, track: AudioTrack, track_index: int):
        import shlex
        socket_dir = tempfile.gettempdir()
        socket_filename = f"mpv-socket-{os.getpid()}-{track_index}"
        socket_path = os.path.join(socket_dir, socket_filename)

        if sys.platform != "win32" and os.path.exists(socket_path):
            os.unlink(socket_path)

        command = shlex.split(self.sequencer.audio_player_command)
        command.extend([
            f"--input-ipc-server={socket_path}",
            "--pause",
        "--loop-file=inf", # Loop the file to prevent mpv from exiting
            track.filepath
        ])

        kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
        if sys.platform != "win32":
            kwargs['preexec_fn'] = os.setsid

        try:
            process = subprocess.Popen(command, **kwargs)
            self.active_audio_processes.append(ActiveAudioProcess(process=process, socket_path=socket_path, track_index=track_index))
            print(f"Launched mpv for track {track_index} with IPC socket: {socket_path}")
        except Exception as e:
            print(f"Error launching mpv for track {track_index}: {e}")
            if sys.platform != "win32" and os.path.exists(socket_path):
                os.unlink(socket_path)

    def _shutdown_audio_processes(self):
        with self.process_lock:
            for ap in self.active_audio_processes:
                
                # CORRECTION : Tenter un arrêt propre via IPC ('quit') avant de forcer la terminaison
                self._send_ipc_command(ap.socket_path, {"command": ["quit"]}) 
                time.sleep(0.05) # Donner un petit délai à mpv pour se fermer proprement

                try:
                    if ap.process.poll() is None:
                        ap.process.terminate()
                        ap.process.wait(timeout=1.0)
                except (subprocess.TimeoutExpired, Exception):
                    if ap.process.poll() is None:
                        ap.process.kill()
                try:
                    if sys.platform != "win32" and ap.socket_path and os.path.exists(ap.socket_path):
                        os.unlink(ap.socket_path)
                except Exception as e:
                    print(f"Error removing socket file {ap.socket_path}: {e}", file=sys.stderr)
            self.active_audio_processes.clear()

    def _seek_audio_process_synchronously(self, ap: ActiveAudioProcess, target_time_sec: float, timeout=2.0):
        """Envoie une commande de recherche (seek) à un processus mpv et attend sa finalisation de manière robuste."""

        command = {"command": ["seek", target_time_sec, "absolute"]}
        if not self._send_ipc_command(ap.socket_path, command):
            print(f"Warning: Impossible d'envoyer la commande seek pour la piste {ap.track_index}", file=sys.stderr)
            return

        start_time = time.time()
        seek_confirmed = False

        while time.time() - start_time < timeout:
            # 1. Vérifier si l'opération de recherche est terminée
            query_seeking = {"command": ["get_property", "seeking"]}
            seeking_response = self._query_ipc_command(ap.socket_path, query_seeking)

            is_seeking = seeking_response.get("data", True) if seeking_response and seeking_response.get("error") == "success" else True

            if not is_seeking:
                # 2. Double-vérification : la tête de lecture est-elle à la bonne position ?
                query_time = {"command": ["get_property", "playback-time"]}
                time_response = self._query_ipc_command(ap.socket_path, query_time, timeout=0.05)

                if time_response and time_response.get("error") == "success":
                    current_time = time_response.get("data", -999)
                    # Tolérance de 150ms pour compenser les imprécisions de l'IPC et du décodage audio
                    if abs(current_time - target_time_sec) < 0.15:
                        seek_confirmed = True
                        break # Succès !

            time.sleep(0.01)

        if not seek_confirmed:
            track_name = self.sequencer.song.tracks[ap.track_index].name
            print(f"Warning: Timed out waiting for seek confirmation on track '{track_name}'", file=sys.stderr)


    def seek_audio_to_beat(self, beat_pos: float, synchronous=False):
        """
        Seeks all audio tracks to a specific beat.
        If synchronous is True, it will block until all seeks are confirmed.
        """
        beats_per_second = self.sequencer.song.tempo / 60.0
        if beats_per_second <= 0:
            return

        threads = []
        with self.process_lock:
            for ap in self.active_audio_processes:
                track = self.sequencer.song.tracks[ap.track_index]
                if isinstance(track, AudioTrack):
                    mpv_time = (beat_pos - track.start_time) / beats_per_second
                    if mpv_time < 0:
                        mpv_time = 0.0

                    if synchronous:
                        # Create and start a thread for each synchronous seek
                        thread = threading.Thread(target=self._seek_audio_process_synchronously, args=(ap, mpv_time))
                        threads.append(thread)
                        thread.start()
                    else:
                        # Asynchronous seek (original behavior)
                        command = {"command": ["seek", mpv_time, "absolute"]}
                        self._send_ipc_command(ap.socket_path, command)

        # If synchronous, wait for all seek threads to complete
        if synchronous:
            for thread in threads:
                thread.join(timeout=3.0) # Add a timeout to prevent indefinite blocking


    def update_audio_tracks_speed(self):
        """Adjusts the playback speed of all audio tracks based on the current song tempo."""
        with self.process_lock:
            for ap in self.active_audio_processes:
                track = self.sequencer.song.tracks[ap.track_index]
                if isinstance(track, AudioTrack) and track.native_tempo is not None and track.native_tempo > 0:
                    speed_factor = self.sequencer.song.tempo / track.native_tempo
                    command = {"command": ["set_property", "speed", speed_factor]}
                    self._send_ipc_command(ap.socket_path, command)

    def _sync_playhead_to_beat(self, beat_pos: float):
        self.last_beat = beat_pos
        tracks = self.sequencer.song.tracks
        num_tracks = len(tracks)  # Corrigé: self.sequencer.song
        self.next_event_indices = [0] * num_tracks
        self._active_notes.clear()

        for i, track in enumerate(tracks):  # Corrigé: self.sequencer.song
            if isinstance(track, MidiTrack):
                for j, event in enumerate(track.events):
                    if event.start_time >= self.last_beat:
                        self.next_event_indices[i] = j
                        break
                else:
                    self.next_event_indices[i] = len(track.events)

        self.next_automation_event_index = 0
        for i, event in enumerate(self.automation_events):
            if event['time'] >= self.last_beat:
                self.next_automation_event_index = i
                break
        else:
            self.next_automation_event_index = len(self.automation_events)

    def _time_callback(self, state, blocksize, pos, new_pos):
        if new_pos:
            pos_dict = jack.position2dict(pos)
            self.sequencer.song.tempo = pos_dict.get('beats_per_minute', self.sequencer.song.tempo)  # Corrigé: self.sequencer.song
            frame = pos_dict.get('frame', 0)
            samplerate = self.jack_client.samplerate
            beats_per_second = self.sequencer.song.tempo / 60.0  # Corrigé: self.sequencer.song
            current_beat = 0.0
            if samplerate > 0 and beats_per_second > 0:
                current_beat = (frame / samplerate) * beats_per_second
            self._sync_playhead_to_beat(current_beat)
            self.seek_audio_to_beat(current_beat)
            if self.sequencer.gui_mode:
                self.sequencer.current_beat = current_beat
                self.sequencer.last_beat_update_time = time.perf_counter()            

    def _process_midi_events(self, start_beat_of_block, end_beat_of_block):
        tracks = self.sequencer.song.tracks
        is_any_track_soloed = any(t.is_solo for t in tracks if hasattr(t, 'is_solo'))

        for i, track in enumerate(tracks):
            # --- Live Preview Override ---
            # If a track is being edited, use the temporary version from the editor.
            if i in self.sequencer.track_overrides:
                track = self.sequencer.track_overrides[i]

            if not isinstance(track, MidiTrack) or not track.output_port_name in self.open_ports:
                continue

            should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
            port = self.open_ports[track.output_port_name]

            if i >= len(self.next_event_indices):
                self.next_event_indices.extend([0] * (i - len(self.next_event_indices) + 1))

            while self.next_event_indices[i] < len(track.events):
                event = track.events[self.next_event_indices[i]]

                if start_beat_of_block <= event.start_time < end_beat_of_block:
                    if should_be_audible:
                        for note in event.notes:
                            note_on_msg = mido.Message('note_on', channel=track.channel, note=note.pitch, velocity=int(note.velocity * track.velocity))
                            port.send(note_on_msg)
                            note_end_beat = event.start_time + note.duration
                            self._active_notes[(i, note.pitch)] = note_end_beat
                        for cc in event.cc_messages:
                            cc_msg = mido.Message('control_change', channel=track.channel, control=cc.control, value=cc.value)
                            port.send(cc_msg)
                    self.next_event_indices[i] += 1
                elif event.start_time >= end_beat_of_block:
                    break
                else:
                    self.next_event_indices[i] += 1

    def _prime_automation_at_beat(self, beat: float) -> set:
        """
        Calculates and applies the correct automation values for a specific beat by
        interpolating the automation curves. This ensures the correct state is set
        when starting playback mid-song.
        Returns a set of (track_index, parameter_name) tuples that were primed.
        """
        primed_params = set()
        param_map = {"vol": {"type": "midi_cc", "control": 7}, "pan": {"type": "midi_cc", "control": 10}, "vel": {"type": "velocity_multiplier"}, "prog": {"type": "program_change"}, **{f"cc{i}": {"type": "midi_cc", "control": i} for i in range(128)}}

        for track in self.sequencer.song.tracks:
            if not isinstance(track, AutomationTrack):
                continue

            # This logic only works if the target track is valid
            if not 0 <= track.target_track_index < len(self.sequencer.song.tracks):
                continue

            points_by_parameter: Dict[str, List[AutomationPoint]] = {}
            for p in track.points:
                points_by_parameter.setdefault(p.parameter, []).append(p)

            for parameter, points in points_by_parameter.items():
                if not points:
                    continue

                start_point = None
                end_point = None

                # Find the last point at or before the beat
                for p in reversed(points):
                    if p.start_time <= beat:
                        start_point = p
                        break

                if not start_point:
                    continue

                # Find the first point after the beat
                for p in points:
                    if p.start_time > beat:
                        end_point = p
                        break

                value_to_apply = start_point.value

                if end_point and start_point.curve != 'none':
                    start_time = start_point.start_time
                    end_time = end_point.start_time
                    time_diff = end_time - start_time

                    if time_diff > 0:
                        start_val = start_point.value
                        end_val = end_point.value
                        value_range = end_val - start_val
                        curve = start_point.curve
                        t = (beat - start_time) / time_diff

                        if curve == "linear":
                            value_to_apply = start_val + t * value_range
                        elif curve == "ease-in":
                            value_to_apply = start_val + (t**2) * value_range
                        elif curve == "ease-out":
                            value_to_apply = start_val + (1 - (1 - t)**2) * value_range
                        elif curve in ["ease-in-out", "sine"]:
                            value_to_apply = start_val + (0.5 * (1 - np.cos(np.pi * t))) * value_range

                param_config = param_map.get(parameter.lower())
                if param_config:
                    event_dict = {
                        "target_track_index": track.target_track_index,
                        "parameter": parameter,
                        "param_config": param_config,
                        "value": value_to_apply
                    }
                    self._apply_automation_event(event_dict)
                    primed_params.add((track.target_track_index, parameter))

        return primed_params

    def _apply_automation_event(self, event: dict):
        """Applies a single automation event."""
        target_track_index = event['target_track_index']
        if not 0 <= target_track_index < len(self.sequencer.song.tracks):
            return

        target_track = self.sequencer.song.tracks[target_track_index]
        param_config = event['param_config']
        value = event['value']
        param_name = event['parameter'].lower()

        # Branch by Track Type first for clarity and correctness
        if isinstance(target_track, MidiTrack):
            if param_config.get('type') == 'midi_cc':
                if target_track.output_port_name in self.open_ports:
                    port = self.open_ports[target_track.output_port_name]
                    midi_value = 0
                    if param_name == 'vol':
                        midi_value = int(value * 127)
                    elif param_name == 'pan':
                        # CORRECT: Convert pan from -1.0..1.0 to 0..127 for MIDI
                        midi_value = int((value + 1.0) / 2.0 * 127)
                    else:
                        midi_value = int(value)
                    midi_value = max(0, min(127, midi_value))
                    msg = mido.Message('control_change', channel=target_track.channel, control=param_config['control'], value=midi_value)
                    port.send(msg)
            elif param_config.get('type') == 'program_change':
                if target_track.output_port_name in self.open_ports:
                    port = self.open_ports[target_track.output_port_name]
                    program_value = max(0, min(127, int(value)))
                    msg = mido.Message('program_change', channel=target_track.channel, program=program_value)
                    port.send(msg)
            elif param_config.get('type') == 'velocity_multiplier':
                target_track.velocity = value

        elif isinstance(target_track, AudioTrack):
            ap = next((p for p in self.active_audio_processes if p.track_index == target_track_index), None)
            if not ap:
                return

            if param_name == 'vol':
                # mpv expects volume from 0 to 100
                mpv_volume = value * 100
                command = {"command": ["set_property", "volume", mpv_volume]}
                self._send_ipc_command(ap.socket_path, command)
            elif param_name == 'pan':
                # CORRECT: Use the lavfi filter for audio track panning, not 'balance'
                gain_l = min(1.0, 1.0 - value)
                gain_r = min(1.0, 1.0 + value)
                pan_filter = f"lavfi=[pan=stereo|c0={gain_l:.2f}*c0|c1={gain_r:.2f}*c1]"
                command = {"command": ["set_property", "af", pan_filter]}
                self._send_ipc_command(ap.socket_path, command)

    def _process_automation_events(self, start_beat_of_block, end_beat_of_block):
        while self.next_automation_event_index < len(self.automation_events):
            event = self.automation_events[self.next_automation_event_index]
            if start_beat_of_block <= event['time'] < end_beat_of_block:
                self._apply_automation_event(event)
                self.next_automation_event_index += 1
            elif event['time'] >= end_beat_of_block:
                break
            else:
                self.next_automation_event_index += 1

    def _process_metronome(self, start_beat_of_block, end_beat_of_block):
        if self.sequencer.song.metronome_enabled and self.sequencer.song.metronome_port_name in self.open_ports:
            port = self.open_ports[self.sequencer.song.metronome_port_name]
            beat_to_check = math.ceil(start_beat_of_block)

            if beat_to_check < end_beat_of_block:
                midi_pan = int((self.sequencer.song.metronome_pan + 1.0) / 2.0 * 127)
                port.send(mido.Message('control_change', channel=self.sequencer.metronome_channel, control=10, value=midi_pan))

            while beat_to_check < end_beat_of_block:
                beats_per_measure = self.sequencer.song.time_signature_numerator
                is_downbeat = (int(beat_to_check) % beats_per_measure) == 0 if beats_per_measure > 0 else beat_to_check == 0
                pitch = self.sequencer.metronome_pitch_downbeat if is_downbeat else self.sequencer.metronome_pitch_beat
                velocity = int(100 * self.sequencer.song.metronome_volume)
                note_on = mido.Message('note_on', channel=self.sequencer.metronome_channel, note=pitch, velocity=velocity)
                note_off = mido.Message('note_off', channel=self.sequencer.metronome_channel, note=pitch, velocity=0)
                port.send(note_on)
                self._metronome_notes_to_turn_off.append(note_off)
                beat_to_check += 1

    def _check_for_loop_and_play_range(self, start_beat_of_block, end_beat_of_block):
        if self.sequencer.play_range_enabled and end_beat_of_block >= self.sequencer.play_range_end_beat:
            if start_beat_of_block < self.sequencer.play_range_end_beat:
                # Schedule the stop command to be executed on the main thread
                Clock.schedule_once(lambda dt: self.sequencer.stop())
                self.sequencer.play_range_enabled = False # Prevent re-triggering

        if self.sequencer.loop_enabled and end_beat_of_block >= self.sequencer.loop_end_beat:
            if start_beat_of_block < self.sequencer.loop_end_beat:
                # When looping, re-prime automation to the loop start point
                self._prime_automation_at_beat(self.sequencer.loop_start_beat)

                beats_per_second = self.sequencer.song.tempo / 60.0
                samplerate = self.jack_client.samplerate
                if beats_per_second > 0 and samplerate > 0:
                    target_frame = int((self.sequencer.loop_start_beat / beats_per_second) * samplerate)
                    _ , pos = self.jack_client.transport_query_struct()
                    pos.frame = target_frame
                    self.jack_client.transport_reposition_struct(pos)

        song_length_beats = self.sequencer.get_song_length_in_beats()
        if not self.sequencer.loop_enabled and not self.sequencer.is_recording and song_length_beats > 0 and end_beat_of_block >= song_length_beats:
            if start_beat_of_block < song_length_beats:
                self.jack_client.transport_stop()
                self.sequencer.playback_state = "stopped"

    def _process_callback(self, frames: int):
        try:
            current_transport_state = self.jack_client.transport_state
            if current_transport_state != self.last_transport_state:
                if current_transport_state == jack.ROLLING:
                    self.set_all_audio_pause_state(False)
                else: # STOPPED or other state
                    self.set_all_audio_pause_state(True)
                self.last_transport_state = current_transport_state

            with self.sync_lock:
                if self.sequencer.song.metronome_enabled and self.sequencer.song.metronome_port_name in self.open_ports:
                    port = self.open_ports[self.sequencer.song.metronome_port_name]
                    for note_off_msg in self._metronome_notes_to_turn_off:
                        port.send(note_off_msg)
                    self._metronome_notes_to_turn_off.clear()

                if not self.jack_client or self.jack_client.transport_state != jack.ROLLING:
                    if self._active_notes:
                        for (track_idx, pitch), end_beat in list(self._active_notes.items()):
                            track = self.sequencer.song.tracks[track_idx]
                            if isinstance(track, MidiTrack) and track.output_port_name in self.open_ports:
                                port = self.open_ports[track.output_port_name]
                                port.send(mido.Message('note_off', channel=track.channel, note=pitch, velocity=0))
                        self._active_notes.clear()
                    return

            state, pos_struct = self.jack_client.transport_query_struct()
            pos = jack.position2dict(pos_struct)
            samplerate = self.jack_client.samplerate
            tempo = self.sequencer.song.tempo
            beats_per_second = tempo / 60.0

            start_beat_of_block = self.last_beat

            current_frame = pos.get('frame', 0)
            if samplerate > 0 and beats_per_second > 0:
                authoritative_beat_now = (current_frame / samplerate) * beats_per_second
            else:
                authoritative_beat_now = self.last_beat

            end_beat_of_block = authoritative_beat_now + (frames / samplerate) * beats_per_second

            for (track_idx, pitch), end_beat in list(self._active_notes.items()):
                if start_beat_of_block <= end_beat < end_beat_of_block:
                    track = self.sequencer.song.tracks[track_idx]
                    if isinstance(track, MidiTrack) and track.output_port_name in self.open_ports:
                        port = self.open_ports[track.output_port_name]
                        port.send(mido.Message('note_off', channel=track.channel, note=pitch, velocity=0))
                    del self._active_notes[(track_idx, pitch)]

            self._process_midi_events(start_beat_of_block, end_beat_of_block)
            self._process_automation_events(start_beat_of_block, end_beat_of_block)
            self._process_metronome(start_beat_of_block, end_beat_of_block)

            with self.process_lock:
                for ap in self.active_audio_processes:
                    track = self.sequencer.song.tracks[ap.track_index]
                    if isinstance(track, AudioTrack):
                        duration_beats = self.sequencer._get_audio_duration_in_beats(track)
                        end_beat = track.start_time + duration_beats
                        if end_beat_of_block >= end_beat and start_beat_of_block < end_beat:
                            command = {"command": ["set_property", "pause", True]}
                            self._send_ipc_command(ap.socket_path, command)

            self._check_for_loop_and_play_range(start_beat_of_block, end_beat_of_block)

            self.last_beat = end_beat_of_block
            if self.sequencer.gui_mode:
                self.sequencer.current_beat = start_beat_of_block
                self.sequencer.last_beat_update_time = time.perf_counter()
        except Exception as e:
            print(f"\nError in JACK process callback: {e}")
            
    def get_measure_beats(self):
        """Calcule la position de chaque barre de mesure en beats."""
        if not self.song:
            return []
            
        # Assumons une signature rythmique par défaut de 4/4 (4 temps par mesure)
        beats_per_measure = 4 
        
        # Obtenir la longueur totale en beats (méthode existante dans Sequencer)
        total_beats = self.get_song_length() 
        
        measure_beats = []
        current_beat = 0
        while current_beat < total_beats:
            measure_beats.append(current_beat)
            current_beat += beats_per_measure
            
        return measure_beats            

class Sequencer(EventDispatcher):
    current_beat = NumericProperty(0)
    last_beat_update_time = NumericProperty(0)
    playback_state = StringProperty("stopped")
    is_recording = BooleanProperty(False)
    ui_end_pos_str = StringProperty("")
    song_structure_changed = NumericProperty(0)
    DEFAULT_AUDIO_PLAYER_COMMAND = "mpv --really-quiet --no-video --idle --af=rubberband --audio-device=jack"

    def __init__(self, tempo: int = 120, gui_mode=False):
        super().__init__()
        self.gui_mode = gui_mode
        self.song = Song(name="New Song", tempo=tempo)
        self.config_manager = ConfigManager()
        self.midi_config = MidiConfig("config/midi_mappings.json")
        self.jack_manager = JackManager(self)
        self.midi_listener_thread = None
        self._midi_listener_stop_event = threading.Event()
        self._transport_control_thread = None
        self._transport_control_stop_event = threading.Event()
        self.control_port_name: Optional[str] = None
        self.open_ports = {}
        self.virtual_ports = []
        self.temporary_ports = []
        self.audio_player_command: str = self.DEFAULT_AUDIO_PLAYER_COMMAND
        self.carla_process: Optional[subprocess.Popen] = None

        # New properties to hold the start/end positions from the UI
        self.ui_start_pos_str = "1:1"
        self.ui_end_pos_str = ""

        self.pause_beat = 0.0
        self.rewind_beat = 0.0
        self.recording_thread = None
        self._stop_event = threading.Event()

        self.loop_enabled = False
        self.loop_start_beat = 0.0
        self.loop_end_beat = 0.0

        self.play_range_enabled = False
        self.play_range_start_beat = 0.0
        self.play_range_end_beat = 0.0

        self.metronome_channel = 9
        self.metronome_pitch_downbeat = 76
        self.metronome_pitch_beat = 77

        self.last_record_settings = None
        self.is_dirty = False
        self.last_project_basename = None
        self._cached_song_length_beats: Optional[float] = None
        self.audio_track_duration_ms: Dict[str, int] = {}
        
        self.default_record_port: Optional[str] = None  # Port d'enregistrement par défaut        
        
        # ⚠️ NOUVEAU : Cache pour éviter de recharger les fichiers audio
        self._audio_duration_cache: Dict[str, float] = {} 
        
        # Cache pour la longueur totale du morceau (dépend de l'audio)
        self._cached_song_length_beats: Optional[float] = None

        self.track_overrides: Dict[int, MidiTrack] = {}
        self.last_play_start_beat: Optional[float] = None

    def _start_carla_process(self, carla_project_path: str):
        """Starts the Carla process with a given project file."""
        self._stop_carla_process()  # Ensure any existing process is stopped first
        if not carla_project_path or not os.path.exists(carla_project_path):
            return

        try:
            print(f"Starting Carla with project: {carla_project_path}")
            # Using Popen to run Carla as a non-blocking background process
            self.carla_process = subprocess.Popen(
                ["carla", carla_project_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            print("Error: 'carla' command not found. Please ensure Carla is installed and in your system's PATH.", file=sys.stderr)
            self.carla_process = None
        except Exception as e:
            print(f"An error occurred while starting Carla: {e}", file=sys.stderr)
            self.carla_process = None

    def _stop_carla_process(self):
        """Stops the managed Carla process if it is running."""
        if self.carla_process and self.carla_process.poll() is None:
            print("Stopping Carla process...")
            self.carla_process.terminate()
            try:
                self.carla_process.wait(timeout=5.0)
                print("Carla process stopped.")
            except subprocess.TimeoutExpired:
                print("Carla did not terminate gracefully. Forcing shutdown.", file=sys.stderr)
                self.carla_process.kill()
            self.carla_process = None

    def process_transport_command(self, command: str):
        """
        Centralized method to handle all transport commands (play, pause, stop, record)
        from both the UI and MIDI controllers to ensure consistent behavior.
        """
        if command == "play_pause":
            # If armed for recording, pressing play should start the recording.
            if self.is_recording and self.playback_state == 'stopped':
                # The recording thread is already waiting for the transport to start.
                # We find the start_beat from the last recording settings.
                start_beat = 0.0
                if self.last_record_settings and 'start_beat' in self.last_record_settings:
                    start_beat = self.last_record_settings['start_beat']
                self.play(start_beat=start_beat)
                return

            # If already playing, do nothing. If paused, resume.
            if self.playback_state == "playing":
                self.pause()
                return
            if self.playback_state == "paused":
                self.pause() # The pause method handles both pause and resume
                return

            # --- Start new playback ---
            # Set the rewind position from the UI, but don't use it for starting playback yet.
            start_pos_for_rewind = self.ui_start_pos_str or "1:1"
            rewind_beat = self.parse_position_to_beats(start_pos_for_rewind)
            if rewind_beat is not None:
                self.rewind_beat = rewind_beat
            else:
                print(f"Warning: Invalid rewind position '{start_pos_for_rewind}', defaulting to 0.")
                self.rewind_beat = 0.0

            # --- MODIFIED: Prioritize the UI start position text field ---
            # The start beat is now determined by the UI's 'start_pos' field,
            # which is also updated by clicking on the ruler.
            start_pos_str = self.ui_start_pos_str or "1:1"
            start_beat = self.parse_position_to_beats(start_pos_str)
            if start_beat is None:
                print(f"Warning: Invalid start position '{start_pos_str}', defaulting to 0.")
                start_beat = 0.0

            end_pos = self.ui_end_pos_str
            if self.loop_enabled:
                self.set_loop_range(self.ui_start_pos_str, end_pos)
                self.play(start_beat=start_beat)
            else:
                end_beat = self.parse_position_to_beats(end_pos) if end_pos else None
                self.play_range_enabled = True
                self.play_range_start_beat = start_beat
                self.play_range_end_beat = end_beat if end_beat is not None else self.get_song_length_in_beats()
                self.play(start_beat=start_beat)

        elif command == "stop":
            # If armed for recording but not yet playing, "stop" should just cancel the armed state.
            if self.is_recording and self.playback_state == 'stopped':
                self.is_recording = False
                print("Recording armed state cancelled.")
            else:
                self.stop()

        elif command == "record":
            if self.is_recording:
                self.stop()
            else:
                self.start_midi_recording() # Assumes a track is armed

    def invalidate_caches(self):
            """Invalide tous les caches qui dépendent de la structure du morceau ou des données audio."""
            self._audio_duration_cache.clear()
            self._cached_song_length_beats = None
            # N'oubliez pas d'appeler cette fonction chaque fois que le tempo, le chemin d'un fichier audio, 
            # ou un événement de piste est modifié (ajout/suppression).

    def _transport_control_listener_loop(self, port_name: str):
        """
        A dedicated thread that listens for transport control MIDI messages based on the loaded configuration.
        """
        try:
            with mido.open_input(port_name) as inport:
                while not self._transport_control_stop_event.is_set():
                    for msg in inport.iter_pending():
                        if msg.type == 'control_change':
                            control = msg.control
                            value = msg.value

                            # --- Handle Transport Controls ---
                            if value == 127:
                                if control == self.midi_config.get_transport_cc("play_pause"):
                                    Clock.schedule_once(lambda dt: self.process_transport_command("play_pause"))
                                elif control == self.midi_config.get_transport_cc("stop"):
                                    Clock.schedule_once(lambda dt: self.process_transport_command("stop"))
                                elif control == self.midi_config.get_transport_cc("record_arm"):
                                    Clock.schedule_once(lambda dt: self.process_transport_command("record"))

                            # --- Handle Volume Sliders & Solo Buttons ---
                            for i in range(len(self.song.tracks)):
                                # Volume
                                if control == self.midi_config.get_volume_slider_cc(i):
                                    volume_value = value / 127.0
                                    Clock.schedule_once(lambda dt, ti=i, vol=volume_value: self.set_track_volume(ti, vol, api_mode=True))
                                    break # Found a match, no need to check other tracks for this CC

                                # Solo
                                if control == self.midi_config.get_track_solo_button_cc(i):
                                    track = self.song.tracks[i]
                                    is_solo = getattr(track, 'is_solo', False)
                                    if (value == 127 and not is_solo) or (value == 0 and is_solo):
                                        Clock.schedule_once(lambda dt, ti=i: self.toggle_solo(ti))
                                    break # Found a match

                    time.sleep(0.01)
        except Exception as e:
            print(f"\nError in transport control listener for port '{port_name}': {e}")

    def reload_midi_mappings(self, filepath: str) -> str:
        """Loads a new MIDI mapping file and restarts the listener if necessary."""
        self.midi_config.load_mappings(filepath)

        # If a transport control port is active, restart it to apply the new mappings
        if self.default_record_port and self._transport_control_thread and self._transport_control_thread.is_alive():
            print("Restarting MIDI transport control listener to apply new mappings...")
            return self.set_default_record_port(self.default_record_port)

        return f"MIDI mappings loaded from {filepath}. No transport listener was active."

    def set_default_record_port(self, port_name: str) -> str:
        """
        Sets the default MIDI input port for recording and transport controls.
        Manages the lifecycle of the transport control listener thread.
        """
        try:
            input_ports = get_input_names()
            if port_name not in input_ports:
                return f"Error: MIDI input port '{port_name}' not found."

            # Stop any existing listener before starting a new one
            if self._transport_control_thread and self._transport_control_thread.is_alive():
                self._transport_control_stop_event.set()
                self._transport_control_thread.join(timeout=1.0)

            self.default_record_port = port_name
            self.is_dirty = True

            # Start the new listener thread
            self._transport_control_stop_event.clear()
            self._transport_control_thread = threading.Thread(
                target=self._transport_control_listener_loop,
                args=(port_name,),
                daemon=True
            )
            self._transport_control_thread.start()

            return f"Default record and transport control port set to: {port_name}"
        except Exception as e:
            return f"Error setting record port: {e}"

    def start_midi_recording(self):
        """
        Starts a recording from a MIDI command. Finds the armed track and starts recording.
        """
        if not self.default_record_port:
            print("Error: No MIDI input port selected for recording.")
            return

        armed_track_index = None
        for i, track in enumerate(self.song.tracks):
            if isinstance(track, MidiTrack) and track.record_mode != 'OFF':
                if armed_track_index is not None:
                    print("Error: Multiple tracks are armed for recording. Please arm only one.")
                    return
                armed_track_index = i

        if armed_track_index is None:
            print("Error: No track is armed for recording.")
            return

        # Use the UI's start position for consistency with play commands
        start_pos = self.ui_start_pos_str or "1:1"
        start_beat = self.parse_position_to_beats(start_pos)
        if start_beat is None:
            start_beat = 0.0 # Fallback

        self.record_track(
            track_idx=armed_track_index,
            start_beat=start_beat,
            inport_name=self.default_record_port
        )

    def get_default_record_port(self) -> Optional[str]:
        """Retourne le port d'enregistrement par défaut"""
        return self.default_record_port

    def invalidate_song_length_cache(self):
        """Invalidates the cached song length."""
        self._cached_song_length_beats = None

    def _get_audio_duration_in_beats(self, track: AudioTrack) -> float:
        """
        Calcule la durée du fichier audio en beats, en utilisant un cache pour éviter le lag.
        """
        if track.duration_beats is not None:
            return track.duration_beats

        if not track.filepath or not os.path.exists(track.filepath):
            return self.song.time_signature_numerator * 4 

        try:
            audio = AudioSegment.from_file(track.filepath) 
            duration_ms = len(audio)
            
            duration_beats = (duration_ms * self.song.tempo) / 60000.0
            
            print("--- DEBUG AUDIO CONVERSION ---")
            print(f"  - Fichier: {os.path.basename(track.filepath)}")
            print(f"  - Tempo du morceau (BPM): {self.song.tempo}")
            print(f"  - Durée Audio (ms): {duration_ms}")
            print(f"  - Durée Audio (sec): {duration_ms / 1000.0}")
            print(f"  - Résultat (Beats): {duration_beats}")
            print("------------------------------")
            
            track.duration_beats = duration_beats
            
            return duration_beats
        except Exception as e:
            print(f"Error loading or processing audio file {track.filepath}: {e}")
            track.duration_beats = 0.0
            return 0.0

    def get_song_length_in_beats(self) -> float:
        """
        Calculates the total length of the song in beats.
        If recording, the length is dynamic and extends to the current playhead position.
        Otherwise, it's based on the last event and cached for performance.
        """
        # If not recording, try to use the cache first.
        if not self.is_recording and self._cached_song_length_beats is not None:
            return self._cached_song_length_beats

        max_beat = 0.0
        # Always calculate the "natural" end of the song based on existing events
        for track in self.song.tracks:
            if isinstance(track, AudioTrack):
                duration_beats = self._get_audio_duration_in_beats(track)
                end_beat = track.start_time + duration_beats
                if end_beat > max_beat:
                    max_beat = end_beat
            elif isinstance(track, MidiTrack):
                if hasattr(track, 'events') and track.events:
                    for event in track.events:
                        for note in event.notes:
                            end_beat = event.start_time + note.duration
                            if end_beat > max_beat:
                                max_beat = end_beat
            elif isinstance(track, AutomationTrack):
                if hasattr(track, 'points') and track.points:
                    last_point_beat = max(p.start_time for p in track.points)
                    if last_point_beat > max_beat:
                        max_beat = last_point_beat

        # If recording, the dynamic length is the greater of the natural end or the current playhead
        if self.is_recording:
            current_beat = self.jack_manager.get_current_beat()
            max_beat = max(max_beat, current_beat)

        # Round up to the next measure
        beats_per_measure = self.song.time_signature_numerator
        min_length = beats_per_measure * 4

        if max_beat > 0.0:
            rounded_length = math.ceil((max_beat + 0.0001) / beats_per_measure) * beats_per_measure
        else:
            rounded_length = min_length

        rounded_length = max(rounded_length, min_length)

        # Only cache the result if we are NOT recording
        if not self.is_recording:
            self._cached_song_length_beats = rounded_length

        return rounded_length

    def _all_notes_off(self):
        for port in self.open_ports.values():
            if port and not port.closed:
                for channel in range(16):
                    port.send(mido.Message('control_change', channel=channel, control=123, value=0))

    def parse_position_to_beats(self, position_str: str, default: str = "1:1") -> Optional[float]:
        if not position_str:
            position_str = default
        try:
            parts = position_str.split(':')
            if len(parts) > 2:
                print("Error: Invalid format. Please use 'measure:beat' or 'measure'.")
                return None
            measure = int(parts[0])
            beat = int(parts[1]) if len(parts) == 2 else 1
            beats_per_measure = self.song.time_signature_numerator
            if not 1 <= beat <= beats_per_measure:
                print(f"Error: Beat number {beat} is out of range for the current time signature ({beats_per_measure}/...). It must be between 1 and {beats_per_measure}.")
                return None
            if measure < 1:
                print("Error: Measure number must be 1 or greater.")
                return None
            return (measure - 1) * beats_per_measure + (beat - 1)
        except (ValueError, IndexError):
            print("Error: Invalid format. Please enter numbers in 'measure:beat' format.")
            return None

    def _format_beats_to_position(self, beats: float) -> str:
        if beats is None:
            return ""
        beats_per_measure = self.song.time_signature_numerator
        if beats_per_measure == 0:
            return "1:1"
        measure = int(beats / beats_per_measure) + 1
        beat = int(beats % beats_per_measure) + 1
        return f"{measure}:{beat}"

    def set_tempo(self, tempo: int) -> str:
        if tempo <= 0:
            return "Error: Tempo must be positive."
        self.song.tempo = tempo
        self.is_dirty = True
        self.invalidate_song_length_cache()
        if self.jack_manager.is_running:
            self.jack_manager.update_audio_tracks_speed()
        return f"Tempo set to {self.song.tempo} BPM."

    def set_time_signature(self, numerator: int, denominator: int) -> str:
        if not (numerator > 0 and denominator > 0 and (denominator & (denominator - 1) == 0)):
            return "Error: Invalid time signature. Denominator must be a power of 2."
        self.song.time_signature_numerator = numerator
        self.song.time_signature_denominator = denominator
        self.is_dirty = True
        return f"Time signature set to {numerator}/{denominator}."

    def add_track(self, name: str, track_type: str = 'midi', instrument: int = 0, filepath: Optional[str] = None):
        if track_type == 'midi':
            track = MidiTrack(name=name, instrument=instrument)
            self.song.add_track(track)
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return {"status": "success", "message": f"MIDI track '{name}' added."}
        elif track_type == 'audio':
            if not filepath:
                return {"status": "error", "message": "Error: Filepath is required for audio tracks."}
            try:
                with suppress_stdout_stderr():
                    segment = AudioSegment.from_file(filepath)
                self.audio_track_duration_ms[filepath] = len(segment)
            except FileNotFoundError:
                return {"status": "error", "message": f"Error: Audio file not found at '{filepath}'"}
            except Exception as e:
                return {"status": "error", "message": f"Error opening audio file: {e}"}
            track = AudioTrack(name=name, filepath=filepath, native_tempo=self.song.tempo)
            self.song.add_track(track)
            self.is_dirty = True
            self.invalidate_song_length_cache()

            # If the sequencer is already running, launch the player for the new track immediately.
            if self.jack_manager.is_running:
                new_track_index = len(self.song.tracks) - 1
                self.jack_manager.launch_player_for_track(track, new_track_index)

            return {"status": "success", "message": f"Audio track '{name}' added with file '{filepath}'."}
        else:
            return {"status": "error", "message": f"Error: Unknown track type '{track_type}'. Must be 'midi' or 'audio'."}

    def add_automation_track(self, name: str, target_track_index: int):
        if not 0 <= target_track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid target track index."}
        target_track = self.song.tracks[target_track_index]
        if isinstance(target_track, AutomationTrack):
            return {"status": "error", "message": "Error: Automation tracks cannot target other automation tracks."}
        track = AutomationTrack(name=name, target_track_index=target_track_index)
        self.song.add_track(track)
        self.is_dirty = True
        return {"status": "success", "message": f"Automation track '{name}' added, targeting track {target_track_index} ('{target_track.name}')."}

    def add_automation_point(self, track_index: int, position_str: str, parameter: str, value: float, curve: str) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, AutomationTrack):
            return "Error: Automation points can only be added to automation tracks."
        start_beat = self.parse_position_to_beats(position_str)
        if start_beat is None:
            return "Error: Invalid position."
        try:
            point = AutomationPoint(start_time=start_beat, parameter=parameter, value=value, curve=curve)
            track.add_point(point)
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return f"Added '{parameter}' automation point to track '{track.name}' at position {position_str}."
        except ValueError as e:
            return f"Error: {e}"

    def delete_track(self, track_index: int, confirm_str: Optional[str] = None, api_mode: bool = False, confirmation_handler=None):
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        track_name = self.song.tracks[track_index].name

        if not api_mode:
            if confirmation_handler:
                confirm = confirmation_handler(f"Are you sure you want to delete track '{track_name}'? [y/N] ").lower()
            else:
                confirm = cancellable_input(f"Are you sure you want to delete track '{track_name}'? [y/N] ").lower()
            if confirm != 'y':
                return {"status": "cancelled", "message": "Deletion cancelled."}
        else:
            if confirm_str is None:
                return {"status": "prompt", "message": f"Are you sure you want to delete track '{track_name}'? [y/N] ", "next_arg": "confirm_str"}
            if confirm_str.lower() != 'y':
                return {"status": "cancelled", "message": "Deletion cancelled."}

        self.song.tracks.pop(track_index)
        self.is_dirty = True
        self.invalidate_song_length_cache()
        return {"status": "success", "message": f"Track '{track_name}' deleted."}

    def add_cc_event(self, track_index: int, position_str: str, control: int, value: int) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: CC events can only be added to MIDI tracks."
        start_beat = self.parse_position_to_beats(position_str)
        if start_beat is None:
            return "Error: Invalid position."
        try:
            new_cc = CCMessage(control=control, value=value)
        except ValueError as e:
            return f"Error: Invalid CC value. {e}"
        existing_event = None
        for event in track.events:
            if math.isclose(event.start_time, start_beat):
                existing_event = event
                break
        if existing_event:
            existing_event.cc_messages.append(new_cc)
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return f"Added CC to existing event at position {position_str} on track '{track.name}'."
        else:
            new_event = Event(start_time=start_beat, cc_messages=[new_cc])
            track.add_event(new_event)
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return f"Added new CC event at position {position_str} on track '{track.name}'."

    def erase_track(self, track_idx: int, start_beat: float, end_beat: float, erase_choice: str, shift_events: bool):
        if not 0 <= track_idx < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_idx]
        if not isinstance(track, (MidiTrack, AutomationTrack)):
            return "Error: Erasing is only supported for MIDI and Automation tracks."
        if end_beat <= start_beat:
            return "Error: End position must be after the start position."

        if isinstance(track, MidiTrack):
            erase_notes = erase_choice.startswith('a') or erase_choice.startswith('n')
            erase_ccs = erase_choice.startswith('a') or erase_choice.startswith('c')

            final_events = []
            events_to_shift = []
            modified_count = 0
            deleted_count = 0
            for event in list(track.events):
                if start_beat <= event.start_time < end_beat:
                    event_modified = False
                    if erase_notes:
                        if event.notes:
                            event.notes.clear()
                            event_modified = True
                    if erase_ccs:
                        if event.cc_messages:
                            event.cc_messages.clear()
                            event_modified = True
                    if event_modified:
                        modified_count += 1
                    if not event.notes and not event.cc_messages:
                        deleted_count += 1
                    else:
                        final_events.append(event)
                elif event.start_time >= end_beat:
                    events_to_shift.append(event)
                else:
                    final_events.append(event)

            if shift_events:
                shift_offset = end_beat - start_beat
                for event in events_to_shift:
                    event.start_time -= shift_offset

            final_events.extend(events_to_shift)
            track.events = final_events
            track.events.sort(key=lambda e: e.start_time)

            report = []
            if modified_count > 0: report.append(f"modified {modified_count} event(s)")
            if deleted_count > 0: report.append(f"deleted {deleted_count} empty event(s)")
            if shift_events: report.append(f"shifted {len(events_to_shift)} event(s)")

            if report:
                 self.is_dirty = True
                 self.invalidate_song_length_cache()
                 return f"Operation complete: {', '.join(report)} from track '{track.name}'."
            else:
                 return "No events were modified, deleted, or shifted."

        elif isinstance(track, AutomationTrack):
            points_to_keep = []
            deleted_count = 0
            for point in track.points:
                is_in_range = start_beat <= point.start_time < end_beat
                should_delete = is_in_range and (erase_choice == "all" or point.parameter == erase_choice)
                if not should_delete:
                    points_to_keep.append(point)
                else:
                    deleted_count += 1

            if deleted_count > 0:
                track.points = points_to_keep
                self.is_dirty = True
                self.invalidate_song_length_cache()
                return f"Erased {deleted_count} point(s) from track '{track.name}'."
            else:
                return "No points were erased."

        return "This should not be reached."

    def rename_track(self, track_index: int, new_name: str):
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        old_name = self.song.tracks[track_index].name
        self.song.tracks[track_index].name = new_name
        self.is_dirty = True
        return {"status": "success", "message": f"Track '{old_name}' renamed to '{new_name}'."}

    def move_track_section(self, source_track_idx: int, confirmation_handler=None) -> str:
        if not 0 <= source_track_idx < len(self.song.tracks):
            return "Error: Invalid source track index."
        source_track = self.song.tracks[source_track_idx]
        if not isinstance(source_track, MidiTrack):
            return "Error: Moving events is only supported for MIDI tracks."

        _input = confirmation_handler or cancellable_input

        try:
            start_pos_str = _input(f"Move from position on track '{source_track.name}' (measure:beat) [default: 1:1]: ").strip()
            source_start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if source_start_beat is None: return ""
            end_pos_str = _input(f"Move up to position on track '{source_track.name}' (measure:beat): ").strip()
            source_end_beat = self.parse_position_to_beats(end_pos_str)
            if source_end_beat is None: return ""
            if source_end_beat <= source_start_beat:
                return "Error: End position must be after the start position."
            dest_track_idx_str = _input(f"Move to destination track index (default: {source_track_idx}, '{source_track.name}'): ").strip()
            dest_track_idx = source_track_idx if dest_track_idx_str == "" else int(dest_track_idx_str)
            if not 0 <= dest_track_idx < len(self.song.tracks):
                return "Error: Invalid destination track index."
            dest_track = self.song.tracks[dest_track_idx]
            if not isinstance(dest_track, MidiTrack):
                return "Error: Destination track must be a MIDI track."
            dest_pos_str = _input(f"Move to destination position on track '{dest_track.name}' (measure:beat) [default: 1:1]: ").strip()
            destination_start_beat = self.parse_position_to_beats(dest_pos_str, default="1:1")
            if destination_start_beat is None: return ""
        except (ValueError, UserInputCancelled):
            return "\nMove cancelled."
        range_duration_beats = source_end_beat - source_start_beat
        destination_end_beat = destination_start_beat + range_duration_beats
        offset_beats = destination_start_beat - source_start_beat
        confirm_message = f"Move events from {start_pos_str} to {end_pos_str} on track '{source_track.name}' to start at {dest_pos_str} on track '{dest_track.name}'. Are you sure? [y/N] "
        if _input(confirm_message).lower() != 'y':
            return "Move cancelled."
        events_at_destination = [event for event in dest_track.events if destination_start_beat <= event.start_time < destination_end_beat]
        if source_track == dest_track:
            events_at_destination = [e for e in events_at_destination if not (source_start_beat <= e.start_time < source_end_beat)]
        overwrite_mode = "add"
        if events_at_destination:
            output = "There are existing notes at the destination.\n"
            while True:
                choice = _input("Do you want to (r)eplace them or (a)dd to them? [r/a] ").lower()
                if choice in ['r', 'replace', 'a', 'add']:
                    overwrite_mode = choice[0]
                    break
                else:
                    output += "Invalid choice. Please enter 'r' or 'a'.\n"
            print(output)
        events_to_move = []
        remaining_source_events = []
        for event in source_track.events:
            if source_start_beat <= event.start_time < source_end_beat:
                events_to_move.append(event)
            else:
                remaining_source_events.append(event)
        deleted_event_count = 0
        if overwrite_mode == 'r':
            final_dest_events = []
            for event in dest_track.events:
                if not (destination_start_beat <= event.start_time < destination_end_beat):
                    final_dest_events.append(event)
                else:
                    if source_track == dest_track and source_start_beat <= event.start_time < source_end_beat:
                        final_dest_events.append(event)
                    else:
                        deleted_event_count += 1
            dest_track.events = final_dest_events
        if source_track != dest_track:
            source_track.events = remaining_source_events
        moved_event_count = 0
        for event in events_to_move:
            event.start_time += offset_beats
            if event.start_time < 0:
                print(f"Warning: Moving event would result in a negative start time ({event.start_time:.2f} beats). Skipping and keeping original.")
                source_track.add_event(event)
                continue
            if source_track != dest_track:
                dest_track.add_event(event)
            moved_event_count += 1
        source_track.events.sort(key=lambda e: e.start_time)
        dest_track.events.sort(key=lambda e: e.start_time)
        report = []
        if moved_event_count > 0:
            report.append(f"Moved {moved_event_count} event(s) from '{source_track.name}' to '{dest_track.name}'")
        if deleted_event_count > 0:
            report.append(f"deleted {deleted_event_count} event(s) at destination")
        if not report:
            return "No notes were found in the source range to move."
        else:
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return f"Operation complete: {', '.join(report)}."

    def copy_track_section(self, source_track_idx: int, confirmation_handler=None) -> str:
        if not self.song.tracks:
            return "No tracks to copy from."

        _input = confirmation_handler or cancellable_input

        try:
            if not 0 <= source_track_idx < len(self.song.tracks):
                return "Error: Invalid source track index."
            source_track = self.song.tracks[source_track_idx]
            if not isinstance(source_track, MidiTrack):
                return "Error: Copying events is only supported for MIDI tracks."
            start_pos_str = _input(f"Copy from position on track '{source_track.name}' (measure:beat) [default: 1:1]: ").strip()
            source_start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if source_start_beat is None: return ""
            end_pos_str = _input(f"Copy up to position on track '{source_track.name}' (measure:beat): ").strip()
            source_end_beat = self.parse_position_to_beats(end_pos_str)
            if source_end_beat is None: return ""
            if source_end_beat <= source_start_beat:
                return "Error: End position must be after the start position."
            dest_track_idx = int(_input(f"Copy to destination track index (default: {source_track_idx}): ").strip() or str(source_track_idx))
            if not 0 <= dest_track_idx < len(self.song.tracks):
                return "Error: Invalid destination track index."
            dest_track = self.song.tracks[dest_track_idx]
            if not isinstance(dest_track, MidiTrack):
                return "Error: Destination track must be a MIDI track."
            dest_pos_str = _input(f"Copy to destination position on track '{dest_track.name}' (measure:beat) [default: 1:1]: ").strip()
            destination_start_beat = self.parse_position_to_beats(dest_pos_str, default="1:1")
            if destination_start_beat is None: return ""
        except (ValueError, UserInputCancelled):
            return "\nCopy cancelled."
        range_duration_beats = source_end_beat - source_start_beat
        destination_end_beat = destination_start_beat + range_duration_beats
        offset_beats = destination_start_beat - source_start_beat
        confirm_message = f"Copy events from {start_pos_str} to {end_pos_str} on track '{source_track.name}' to start at {dest_pos_str} on track '{dest_track.name}'. Are you sure? [y/N] "
        if _input(confirm_message).lower() != 'y':
            return "Copy cancelled."
        events_at_destination = [event for event in dest_track.events if destination_start_beat <= event.start_time < destination_end_beat]
        overwrite_mode = "add"
        if events_at_destination:
            output = "There are existing notes at the destination.\n"
            while True:
                choice = _input("Do you want to (r)eplace them or (a)dd to them? [r/a] ").lower()
                if choice in ['r', 'replace']:
                    overwrite_mode = "replace"
                    break
                elif choice in ['a', 'add']:
                    overwrite_mode = "add"
                    break
                else:
                    output += "Invalid choice. Please enter 'r' or 'a'.\n"
            print(output)
        copied_event_count = 0
        deleted_event_count = 0
        source_events_to_copy = [event for event in source_track.events if source_start_beat <= event.start_time < source_end_beat]
        if overwrite_mode == "replace":
            initial_dest_event_count = len(dest_track.events)
            dest_track.events = [event for event in dest_track.events if not (destination_start_beat <= event.start_time < destination_end_beat)]
            deleted_event_count = initial_dest_event_count - len(dest_track.events)
        for event in source_events_to_copy:
            new_event = deepcopy(event)
            new_event.start_time += offset_beats
            if new_event.start_time < 0:
                print(f"Warning: Copying event would result in a negative start time ({new_event.start_time:.2f} beats). Skipping event.")
                continue
            dest_track.add_event(new_event)
            copied_event_count += 1
        dest_track.events.sort(key=lambda e: e.start_time)
        report = []
        if copied_event_count > 0:
            report.append(f"Copied {copied_event_count} event(s)")
        if deleted_event_count > 0:
            report.append(f"deleted {deleted_event_count} event(s) at destination")
        if not report:
            return "No notes were found in the source range to copy."
        else:
            self.is_dirty = True
            self.invalidate_song_length_cache()
            return f"Operation complete: {', '.join(report)}."

    def transpose_track_section(self, track_idx: int, start_pos_str: Optional[str] = None, end_pos_str: Optional[str] = None, transpose_value_str: Optional[str] = None, confirm_str: Optional[str] = None, api_mode: bool = False, confirmation_handler=None):
        if not 0 <= track_idx < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        track = self.song.tracks[track_idx]
        if not isinstance(track, MidiTrack):
            return {"status": "error", "message": "Error: Transposing is only supported for MIDI tracks."}

        _input = confirmation_handler or cancellable_input

        # Interactive CLI mode
        if not api_mode:
            try:
                start_pos_str = _input(f"Transpose from position on track '{track.name}' (measure:beat) [default: 1:1]: ").strip()
                start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
                if start_beat is None: return {"status": "error", "message": "Invalid start position."}

                end_pos_str_in = _input(f"Transpose up to position on track '{track.name}' (measure:beat) [default: end of track]: ").strip()
                if end_pos_str_in == "":
                    end_beat = float('inf')
                else:
                    end_beat = self.parse_position_to_beats(end_pos_str_in)
                    if end_beat is None: return {"status": "error", "message": "Invalid end position."}

                if end_beat <= start_beat:
                    return {"status": "error", "message": "Error: End position must be after the start position."}

                transpose_value = int(_input("Transpose by how many semitones (e.g., 12 for up, -12 for down): ").strip())
                if not -127 <= transpose_value <= 127:
                    return {"status": "error", "message": "Error: Transposition value must be between -127 and 127."}

                events_to_transpose = [event for event in track.events if start_beat <= event.start_time < end_beat]
                if not events_to_transpose:
                    return {"status": "success", "message": "No notes found in the specified range to transpose."}

                confirm_message = f"Transpose {len(events_to_transpose)} event(s) on track '{track.name}' by {transpose_value} semitones. Are you sure? [y/N] "
                if _input(confirm_message).lower() != 'y':
                    return {"status": "cancelled", "message": "Transpose cancelled."}

            except (ValueError, UserInputCancelled):
                return {"status": "cancelled", "message": "\nTranspose cancelled."}

        # API mode
        else:
            if start_pos_str is None:
                return {"status": "prompt", "message": f"Transpose from position on track '{track.name}' (measure:beat) [default: 1:1]: ", "next_arg": "start_pos_str"}
            start_beat = self.parse_position_to_beats(start_pos_str, default="1:1")
            if start_beat is None: return {"status": "error", "message": "Invalid start position format."}

            if end_pos_str is None:
                return {"status": "prompt", "message": f"Transpose up to position on track '{track.name}' (measure:beat) [default: end of track]: ", "next_arg": "end_pos_str"}
            if end_pos_str == "": end_beat = float('inf')
            else:
                end_beat = self.parse_position_to_beats(end_pos_str)
                if end_beat is None: return {"status": "error", "message": "Invalid end position format."}

            if end_beat <= start_beat:
                return {"status": "error", "message": "Error: End position must be after the start position."}

            if transpose_value_str is None:
                return {"status": "prompt", "message": "Transpose by how many semitones (e.g., 12 for up, -12 for down): ", "next_arg": "transpose_value_str"}
            try:
                transpose_value = int(transpose_value_str)
            except ValueError:
                return {"status": "error", "message": "Error: Invalid number for semitones."}
            if not -127 <= transpose_value <= 127:
                return {"status": "error", "message": "Error: Transposition value must be between -127 and 127."}

            events_to_transpose = [event for event in track.events if start_beat <= event.start_time < end_beat]
            if not events_to_transpose:
                return {"status": "success", "message": "No notes found in the specified range to transpose."}

            if confirm_str is None:
                return {"status": "prompt", "message": f"Transpose {len(events_to_transpose)} event(s) on track '{track.name}' by {transpose_value} semitones. Are you sure? [y/N] ", "next_arg": "confirm_str"}
            if confirm_str.lower() != 'y':
                return {"status": "cancelled", "message": "Transpose cancelled."}

        # Common execution logic
        transposed_note_count = 0
        clamped_note_count = 0
        for event in events_to_transpose:
            for note in event.notes:
                original_pitch = note.pitch
                new_pitch = original_pitch + transpose_value
                if not 0 <= new_pitch <= 127:
                    clamped_pitch = max(0, min(127, new_pitch))
                    note.pitch = clamped_pitch
                    clamped_note_count += 1
                else:
                    note.pitch = new_pitch
                transposed_note_count += 1
        self.is_dirty = True
        message = f"Transposed {transposed_note_count} note(s) on track '{track.name}'."
        if clamped_note_count > 0:
            message += f" {clamped_note_count} note(s) were clamped to the valid MIDI pitch range (0-127)."
        return {"status": "success", "message": message}

    def assign_port(self, track_index: int, port_name: str) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: Port assignment is currently only supported for MIDI tracks."

        # Strip quotes if port_name is a quoted string from the command line
        if port_name.startswith('"') and port_name.endswith('"'):
            port_name = port_name[1:-1]

        old_port_name = track.output_port_name
        track.output_port_name = port_name
        self.is_dirty = True

        if self.jack_manager.is_running:
            self.jack_manager.open_midi_port(port_name)
            if old_port_name and old_port_name != port_name:
                self.jack_manager.close_midi_port(old_port_name)

        return f"Assigned port '{port_name}' to track '{track.name}'."

    def unassign_port(self, track_index: int) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: Port un-assignment is currently only supported for MIDI tracks."
        if track.output_port_name:
            port_name = track.output_port_name
            output = f"Un-assigned port from track '{track.name}'."
            track.output_port_name = None
            self.is_dirty = True

            if self.jack_manager.is_running:
                self.jack_manager.close_midi_port(port_name)

            return output
        else:
            return f"Track '{track.name}' has no port assigned."

    def set_audio_player_command(self, command: str):
        """Sets the command for the external audio player."""
        self.audio_player_command = command
        self.is_dirty = True
        print(f"Audio player command set to: {command}")
        print("Note: The audio filepath will be appended to this command.")

    def set_bank(self, track_index: int, msb: int, lsb: int = 0) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: Bank select is only available for MIDI tracks."
        if not 0 <= msb <= 127 and 0 <= lsb <= 127:
            return "Error: Bank values (MSB, LSB) must be between 0 and 127."
        track.bank_msb = msb
        track.bank_lsb = lsb
        self.is_dirty = True
        return f"Set bank for track '{track.name}' to MSB={msb}, LSB={lsb}."

    def set_channel(self, track_index: int, channel: int) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: MIDI channel can only be set for MIDI tracks."
        if not 1 <= channel <= 16:
            return "Error: MIDI channel must be between 1 and 16."
        track.channel = channel - 1
        self.is_dirty = True
        return f"Set MIDI channel for track '{track.name}' to {channel}."

    def set_program(self, track_index: int, program: int) -> str:
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: Program change is only available for MIDI tracks."
        if not 0 <= program <= 127:
            return "Error: Program number must be between 0 and 127."
        track.instrument = program
        self.is_dirty = True
        return f"Set program for track '{track.name}' to {program + 1}."

    def set_metronome_track(self, track_index: int) -> str:
        """Designates a specific MIDI track as the one to be hidden in the UI."""
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."

        track_to_set = self.song.tracks[track_index]
        if not isinstance(track_to_set, MidiTrack):
            return "Error: Only MIDI tracks can be designated as metronome tracks."

        for i, track in enumerate(self.song.tracks):
            if isinstance(track, MidiTrack):
                track.is_metronome = (i == track_index)

        self.is_dirty = True
        return f"Track '{track_to_set.name}' is now designated as the metronome track."

    def set_track_volume(self, track_index: int, volume_str: Optional[str] = None, api_mode: bool = False, confirmation_handler=None):
        """Sets the volume for a specific audio or MIDI track."""
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        track = self.song.tracks[track_index]
        if not isinstance(track, (AudioTrack, MidiTrack)):
            return {"status": "error", "message": "Error: Volume can only be set for audio or MIDI tracks."}

        if volume_str is None:
            if api_mode:
                return {"status": "prompt", "message": "Enter volume (0.0 - 1.0): ", "next_arg": "volume_str"}
            else:
                _input = confirmation_handler or cancellable_input
                try:
                    volume_str = _input("Enter volume (0.0 - 1.0): ").strip()
                except UserInputCancelled:
                    return {"status": "cancelled", "message": "Cancelled."}

        try:
            volume = float(volume_str)
            if not 0.0 <= volume <= 1.0:
                return {"status": "error", "message": "Error: Volume must be between 0.0 and 1.0."}
        except ValueError:
            return {"status": "error", "message": "Error: Invalid volume."}

        track.volume = volume
        self.is_dirty = True

        if isinstance(track, AudioTrack):
            if self.jack_manager.is_running:
                with self.jack_manager.process_lock:
                    for ap in self.jack_manager.active_audio_processes:
                        if ap.track_index == track_index:
                            self.jack_manager._send_ipc_command(ap.socket_path, {"command": ["set_property", "volume", volume * 100]})
                            break
        elif isinstance(track, MidiTrack):
            if self.jack_manager.is_running and track.output_port_name:
                port = self.jack_manager.open_ports.get(track.output_port_name)
                if port:
                    midi_volume = int(volume * 127)
                    port.send(mido.Message("control_change", channel=track.channel, control=7, value=midi_volume))

        return {"status": "success", "message": f"Volume for track '{track.name}' set to {volume:.2f}."}

    def _resync_jack_transport(self):
        """
        Forces a full resynchronization of the JACK transport if it is currently rolling.
        This is used after state changes like mute/solo/pan to prevent desync.
        """
        if not self.jack_manager.is_running:
            return

        debug = getattr(self, "debug_enabled", True)
        was_rolling = self.jack_manager.jack_client and self.jack_manager.jack_client.transport_state == jack.ROLLING

        if was_rolling:
            if debug:
                print("[DEBUG] Forcing JACK transport resync...")
            try:
                jc = self.jack_manager.jack_client
                # Read current position
                state, pos_struct = jc.transport_query_struct()
                pos_dict = jack.position2dict(pos_struct)
                frame = pos_dict.get('frame', 0)
                samplerate = jc.samplerate
                beats_per_second = self.song.tempo / 60.0
                current_beat = (frame / samplerate) * beats_per_second if samplerate > 0 and beats_per_second > 0 else self.jack_manager.last_beat

                # 1. Briefly stop the transport
                jc.transport_stop()
                time.sleep(0.02)

                # 2. Reposition playhead and audio tracks
                self.jack_manager._sync_playhead_to_beat(current_beat)
                self.jack_manager.seek_audio_to_beat(current_beat)
                self.jack_manager.last_beat = current_beat

                # 3. Restart JACK
                jc.transport_start()

                if debug:
                    print(f"[DEBUG] Transport resynced → beat={current_beat:.6f}")
            except Exception as e:
                print(f"[DEBUG] JACK resync failed: {e}", file=sys.stderr)

    def set_track_pan(self, track_index: int, pan_str: Optional[str] = None, api_mode: bool = False, confirmation_handler=None):
        """Sets the pan for a specific audio or MIDI track."""
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        track = self.song.tracks[track_index]
        if not isinstance(track, (AudioTrack, MidiTrack)):
            return {"status": "error", "message": "Error: Pan can only be set for audio or MIDI tracks."}

        if pan_str is None:
            if api_mode:
                return {"status": "prompt", "message": "Enter pan (-1.0 to 1.0): ", "next_arg": "pan_str"}
            else:
                _input = confirmation_handler or cancellable_input
                try:
                    pan_str = _input("Enter pan (-1.0 to 1.0): ").strip()
                except UserInputCancelled:
                    return {"status": "cancelled", "message": "Cancelled."}

        try:
            pan = float(pan_str)
            if not -1.0 <= pan <= 1.0:
                return {"status": "error", "message": "Error: Pan must be between -1.0 (left) and 1.0 (right)."}
        except ValueError:
            return {"status": "error", "message": "Error: Invalid pan value."}

        track.pan = pan
        self.is_dirty = True

        # === UPDATE PISTE AUDIO (mpv IPC) ===
        if isinstance(track, AudioTrack):
            if self.jack_manager.is_running:
                with self.jack_manager.process_lock:
                    for ap in self.jack_manager.active_audio_processes:
                        if ap.track_index == track_index:
                            # Pan value from -1.0 (L) to 1.0 (R)
                            # Using lavfi pan filter. c0 is left, c1 is right.
                            gain_l = min(1.0, 1.0 - pan)
                            gain_r = min(1.0, 1.0 + pan)
                            pan_filter = f"lavfi=[pan=stereo|c0={gain_l:.2f}*c0|c1={gain_r:.2f}*c1]"
                            command = {"command": ["set_property", "af", pan_filter]}
                            self.jack_manager._send_ipc_command(ap.socket_path, command)
                            break
        # === UPDATE PISTE MIDI (CC #10) ===
        elif isinstance(track, MidiTrack):
            if self.jack_manager.is_running and track.output_port_name:
                port = self.jack_manager.open_ports.get(track.output_port_name)
                if port:
                    # Conversion de pan (-1.0 à 1.0) en valeur MIDI (0 à 127)
                    midi_pan = int((pan + 1.0) / 2.0 * 127)
                    port.send(mido.Message("control_change", channel=track.channel, control=10, value=midi_pan))
        
        return {"status": "success", "message": f"Pan for track '{track.name}' set to {pan:.2f}."}

    def set_track_velocity(self, track_index: int, velocity_str: Optional[str] = None, api_mode: bool = False, confirmation_handler=None):
        """Sets the velocity multiplier for a specific MIDI track."""
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return {"status": "error", "message": "Error: Velocity can only be set for MIDI tracks."}

        if velocity_str is None:
            if api_mode:
                return {"status": "prompt", "message": "Enter velocity multiplier (e.g., 1.0): ", "next_arg": "velocity_str"}
            else:
                _input = confirmation_handler or cancellable_input
                try:
                    velocity_str = _input("Enter velocity multiplier (e.g., 1.0): ").strip()
                except UserInputCancelled:
                    return {"status": "cancelled", "message": "Cancelled."}

        try:
            velocity = float(velocity_str)
            if not 0.0 <= velocity:
                return {"status": "error", "message": "Error: Velocity multiplier must be a positive number."}
        except ValueError:
            return {"status": "error", "message": "Error: Invalid velocity value."}

        track.velocity = velocity
        self.is_dirty = True
        return {"status": "success", "message": f"Velocity for track '{track.name}' set to {velocity:.2f}."}

    def toggle_mute(self, track_index: int):
        if not 0 <= track_index < len(self.song.tracks):
            return {"status": "error", "message": "Error: Invalid track index."}

        track = self.song.tracks[track_index]
        track.is_muted = not track.is_muted
        self.is_dirty = True
        status = "Muted" if track.is_muted else "Unmuted"

        # --- Update in real-time if JACK is running ---
        if self.jack_manager.is_running:
            is_rolling = self.jack_manager.jack_client.transport_state == jack.ROLLING
            is_any_track_soloed = any(t.is_solo for t in self.song.tracks if hasattr(t, 'is_solo'))
            should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted

            # --- Audio Track: Lightweight IPC command ---
            if isinstance(track, AudioTrack):
                with self.jack_manager.process_lock:
                    ap = next((p for p in self.jack_manager.active_audio_processes if p.track_index == track_index), None)
                    if ap:
                        self.jack_manager._send_ipc_command(ap.socket_path, {"command": ["set_property", "mute", not should_be_audible]})
                # Audio tracks do not require a transport resync for mute/solo

            # --- MIDI Track: Silence notes and conditionally resync ---
            elif isinstance(track, MidiTrack):
                if track.output_port_name and track.output_port_name in self.jack_manager.open_ports:
                    port = self.jack_manager.open_ports[track.output_port_name]
                    if track.is_muted:
                        # Silence all notes for this track
                        for cc in (123, 120, 121):
                            port.send(mido.Message('control_change', channel=track.channel, control=cc, value=0))
                        keys_to_remove = [key for key in self.jack_manager._active_notes.keys() if key[0] == track_index]
                        for key in keys_to_remove:
                            del self.jack_manager._active_notes[key]

                # MIDI tracks DO require a resync to prevent timing drift if muted/unmuted during playback
                if is_rolling:
                    self._resync_jack_transport()

            # Automation events depend on mute/solo state, so they must be regenerated
            self.jack_manager._prepare_automation_events()

        return {"status": "success", "message": f"Track '{track.name}' is now {status}."}

    def toggle_solo(self, track_index: int):
        tracks = self.song.tracks
        if not 0 <= track_index < len(tracks):
            return {"status": "error", "message": "Error: Invalid track index."}

        target_track = tracks[track_index]
        is_being_soloed = not target_track.is_solo
        target_track.is_solo = is_being_soloed
        self.is_dirty = True
        output = ""

        if is_being_soloed:
            for i, other_track in enumerate(tracks):
                if i != track_index and hasattr(other_track, 'is_solo') and other_track.is_solo:
                    other_track.is_solo = False
                    output += f"Track '{other_track.name}' is now Un-soloed.\n"
        status = "Solo" if target_track.is_solo else "Un-soloed"

        # --- Real-time update if JACK is running ---
        if self.jack_manager.is_running:
            is_rolling = self.jack_manager.jack_client.transport_state == jack.ROLLING
            is_any_track_soloed = any(t.is_solo for t in tracks if hasattr(t, 'is_solo'))

            # Update all audio track mute states via lightweight IPC
            with self.jack_manager.process_lock:
                for i, track in enumerate(tracks):
                    if isinstance(track, AudioTrack):
                        should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                        ap = next((p for p in self.jack_manager.active_audio_processes if p.track_index == i), None)
                        if ap:
                            self.jack_manager._send_ipc_command(ap.socket_path, {"command": ["set_property", "mute", not should_be_audible]})

            # For MIDI tracks, silence those that are no longer audible
            for i, track in enumerate(tracks):
                if isinstance(track, MidiTrack):
                    should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                    if not should_be_audible:
                        if track.output_port_name and track.output_port_name in self.jack_manager.open_ports:
                            port = self.jack_manager.open_ports[track.output_port_name]
                            port.send(mido.Message('control_change', channel=track.channel, control=123, value=0))
                            keys_to_remove = [key for key in self.jack_manager._active_notes.keys() if key[0] == i]
                            for key in keys_to_remove:
                                del self.jack_manager._active_notes[key]

            # Regenerate automation events as their audibility may have changed
            self.jack_manager._prepare_automation_events()

            # A solo change is a major state change that requires resynchronization to avoid drift
            if is_rolling:
                self._resync_jack_transport()

        output += f"Track '{target_track.name}' is now {status}."
        return {"status": "success", "message": output}

    def prime_all_tracks(self, primed_by_automation: set = None) -> str:
        """
        Sends the current state (program, volume, pan, etc.) for all assigned MIDI tracks.
        Skips parameters that have already been set by an automation event.
        """
        if not self.jack_manager.is_running:
            return "Warning: prime_all_tracks called but JACK manager is not running. State will not be sent."

        if primed_by_automation is None:
            primed_by_automation = set()

        output = "Priming all MIDI tracks with initial state...\n"
        tracks = self.song.tracks
        is_any_track_soloed = any(t.is_solo for t in tracks if hasattr(t, 'is_solo'))

        for i, track in enumerate(tracks):
            if self.is_recording and self.last_record_settings and i == self.last_record_settings.get('track_index'):
                continue

            if isinstance(track, MidiTrack) and track.output_port_name:
                should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                port = self.jack_manager.open_ports.get(track.output_port_name)

                if port:
                    if should_be_audible:
                        try:
                            output += f"  - Priming MIDI track '{track.name}' to '{port.name}' on Ch: {track.channel + 1}\n"
                            # Bank and Program changes are always sent, unless automation for them exists at the start.
                            if track.bank_msb is not None and (i, 'cc0') not in primed_by_automation:
                                port.send(mido.Message('control_change', channel=track.channel, control=0, value=track.bank_msb))
                            if track.bank_lsb is not None and (i, 'cc32') not in primed_by_automation:
                                port.send(mido.Message('control_change', channel=track.channel, control=32, value=track.bank_lsb))
                            if (i, 'prog') not in primed_by_automation:
                                port.send(mido.Message('program_change', channel=track.channel, program=track.instrument))

                            # Only send volume if not already handled by automation.
                            if (i, 'vol') not in primed_by_automation:
                                midi_volume = int(track.volume * 127)
                                port.send(mido.Message('control_change', channel=track.channel, control=7, value=midi_volume))

                            # Only send pan if not already handled by automation.
                            if (i, 'pan') not in primed_by_automation:
                                midi_pan = int((track.pan + 1.0) / 2.0 * 127)
                                port.send(mido.Message('control_change', channel=track.channel, control=10, value=midi_pan))

                        except Exception as e:
                            output += f"  - Could not send state to port '{track.output_port_name}': {e}\n"
                    else:
                        # Silence the track if it's not supposed to be audible
                        port.send(mido.Message('control_change', channel=track.channel, control=7, value=0)) # Volume to 0
                        port.send(mido.Message('control_change', channel=track.channel, control=123, value=0)) # All notes off
                else:
                    output += f"  - Skipping track '{track.name}', port '{track.output_port_name}' not open in JackManager.\n"
        return output

    def set_control_port(self, port_name: str) -> str:
        """Sets the MIDI input port for control messages and starts listening."""
        if self.midi_listener_thread and self.midi_listener_thread.is_alive():
            return "A control port is already active. Please unset it first."
        self.control_port_name = port_name
        self._midi_listener_stop_event.clear()
        self.midi_listener_thread = threading.Thread(target=self._midi_listener_loop, args=(port_name,))
        self.midi_listener_thread.daemon = True
        self.midi_listener_thread.start()
        return f"Listening for control messages on '{port_name}'."

    def unset_control_port(self) -> str:
        """Stops listening for control messages and closes the port."""
        if not self.midi_listener_thread or not self.midi_listener_thread.is_alive():
            return "No active control port to unset."
        self._midi_listener_stop_event.set()
        if self.midi_listener_thread:
            self.midi_listener_thread.join(timeout=1.0)
        self.control_port_name = None
        return "Stopped listening for control messages."

    def _midi_listener_loop(self, port_name: str):
        """
        The main loop for the MIDI control message listener thread.
        This loop handles live parameter changes and, if recording is active,
        triggers the recording of automation points.
        """
        try:
            with mido.open_input(port_name) as inport:
                while not self._midi_listener_stop_event.is_set():
                    for msg in inport.iter_pending():
                        if msg.type == 'control_change':
                            for mapping in self.song.midi_mappings:
                                if mapping.channel == msg.channel and mapping.control == msg.control:
                                    self._apply_midi_mapping_action(mapping, msg.value)
                                    if self.is_recording:
                                        self._record_automation_from_mapping(mapping, msg.value)
                    time.sleep(0.01)
        except Exception as e:
            print(f"\nError in MIDI listener thread for port '{port_name}': {e}")

    def _get_parameter_value_from_cc(self, action: str, cc_value: int) -> float:
        """Converts a MIDI CC value (0-127) to a normalized parameter value."""
        if action == 'volume':
            return cc_value / 127.0
        elif action == 'pan':
            return (cc_value / 127.0) * 2.0 - 1.0
        return float(cc_value)

    def _apply_midi_mapping_action(self, mapping: 'MidiMapping', value: int):
        """Applies the action defined in a MidiMapping."""
        if not 0 <= mapping.track_index < len(self.song.tracks):
            return
        param_value = self._get_parameter_value_from_cc(mapping.action, value)
        if mapping.action == 'volume':
            self.set_track_volume(mapping.track_index, param_value)
        elif mapping.action == 'pan':
            self.set_track_pan(mapping.track_index, param_value)
        elif mapping.action == 'program':
            self.set_program(mapping.track_index, int(param_value))
        print(f"\rCC -> Track {mapping.track_index} {mapping.action.capitalize()}: {value}   ", end="")
        sys.stdout.flush()

    def _record_automation_from_mapping(self, mapping: 'MidiMapping', value: int):
        """Records a received CC value as an automation point."""
        auto_track_idx = self._find_or_create_automation_track(mapping.track_index, mapping.action)
        if auto_track_idx is None:
            return
        auto_track = self.song.tracks[auto_track_idx]
        if not isinstance(auto_track, AutomationTrack):
            return
        current_beat = self._get_current_beat()
        point_value = self._get_parameter_value_from_cc(mapping.action, value)
        new_point = AutomationPoint(start_time=current_beat, parameter=mapping.action, value=point_value, curve='linear')
        auto_track.add_point(new_point)
        self.is_dirty = True

    def load_song(self, filepath: str) -> str:
        try:
            self.song = import_song(filepath)
            self.is_dirty = True
            self.last_project_basename = None
            self.invalidate_song_length_cache()
            return f"Successfully loaded song from '{filepath}'."
        except Exception as e:
            return f"Error loading MIDI file: {e}"

    def import_midi_file(self, filepath: str) -> str:
        """Imports a MIDI file into the current project."""
        result = import_midi_to_project(self, filepath)
        return result.get("message", "Import process finished.")

    def export_midi_tracks(self, track_indices: List[int], filepath: str) -> str:
        """Exports selected MIDI tracks to a MIDI file."""
        result = export_midi_from_project(self, track_indices, filepath)
        return result.get("message", "Export process finished.")

    def save_song(self, filepath: str) -> str:
        try:
            export_to_midi(self.song, filepath)
            return f"Song successfully saved to '{filepath}'."
        except Exception as e:
            return f"Error saving MIDI file: {e}"

    def save_project(self, basename: str) -> str:
        project_filepath = f"{basename}.proj.json"
        try:
            # Update the song name to match the project basename
            self.song.name = basename
            project_data = {"song": self.song, "virtual_ports": [vp.name for vp in self.virtual_ports], "control_port_name": self.control_port_name, "audio_player_command": self.audio_player_command}
            with open(project_filepath, 'w') as f:
                json.dump(project_data, f, indent=4, cls=CustomSongEncoder)
            self.is_dirty = False
            self.last_project_basename = basename
            return f"Project saved to '{project_filepath}'"
        except Exception as e:
            return f"Error saving project file: {e}"

    def load_project(self, basename: str) -> str:
        project_filepath = f"{basename}.proj.json"
        try:
            with open(project_filepath, 'r') as f:
                project_data = json.load(f, object_hook=song_decoder)
            self.song = project_data.get("song", Song(name="New Song"))
            self.audio_player_command = project_data.get("audio_player_command", self.DEFAULT_AUDIO_PLAYER_COMMAND)
            if "mplayer" in self.audio_player_command:
                print("Warning: Old 'mplayer' command found in project. Updating to 'mpv' default.")
                self.audio_player_command = self.DEFAULT_AUDIO_PLAYER_COMMAND
            self.close_virtual_ports()
            self.virtual_ports = []
            for vp_name in project_data.get("virtual_ports", []):
                self.create_virtual_port(vp_name)
            self.unset_control_port()
            control_port_name = project_data.get("control_port_name")
            if control_port_name:
                self.set_control_port(control_port_name)
            self.is_dirty = False
            self.last_project_basename = basename
            self.invalidate_song_length_cache()

            # --- Stop existing Carla instance ---
            self._stop_carla_process()

            # --- Start new Carla instance if specified ---
            if self.song.carla_project_path:
                self._start_carla_process(self.song.carla_project_path)
                print("Waiting for Carla to initialize...")
                time.sleep(3)  # Wait for Carla and its plugins to be ready

            # --- Restart Jack Manager to apply new project settings ---
            self.jack_manager.stop()
            self.jack_manager.start()
            time.sleep(0.5) # Give Jack time to register ports

            # --- Restore JACK connections with aj-snapshot if specified ---
            if self.song.aj_snapshot_path and os.path.exists(self.song.aj_snapshot_path):
                try:
                    print(f"Restoring JACK connections from {self.song.aj_snapshot_path}...")
                    subprocess.run(["aj-snapshot", "-r", self.song.aj_snapshot_path], check=True)
                except FileNotFoundError:
                    print("Error: 'aj-snapshot' command not found. Please ensure it is installed.", file=sys.stderr)
                except subprocess.CalledProcessError as e:
                    print(f"Error restoring aj-snapshot: {e}", file=sys.stderr)
            else:
                # --- Auto-connect MIDI tracks based on project data (fallback) ---
                for track in self.song.tracks:
                    if isinstance(track, MidiTrack) and track.output_port_name and track.input_port_name:
                        self.jack_manager.auto_connect_dynamic(track.output_port_name, track.input_port_name)

            return f"Successfully loaded project from '{project_filepath}'"
        except FileNotFoundError:
            return f"Error: Project file not found at '{project_filepath}'"
        except Exception as e:
            return f"Error loading project file: {e}"

    def save_jack_connections(self, filepath: str) -> dict:
        """Saves the current JACK connections using aj-snapshot."""
        if not filepath:
            return {"status": "error", "message": "Filepath cannot be empty."}

        command = ["aj-snapshot", "-d", filepath]
        try:
            print(f"Executing: {' '.join(command)}")
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode == 0:
                output = result.stdout.strip() or result.stderr.strip()
                print(f"aj-snapshot output: {output}")
                return {
                    "status": "success",
                    "message": f"JACK connections saved to {os.path.basename(filepath)}."
                }
            else:
                error_message = result.stderr.strip()
                print(f"aj-snapshot command failed with exit code {result.returncode}: {error_message}", file=sys.stderr)
                return {
                    "status": "error",
                    "message": f"aj-snapshot failed: {error_message}"
                }

        except FileNotFoundError:
            print("Error: 'aj-snapshot' command not found.", file=sys.stderr)
            return {
                "status": "error",
                "message": "Error: 'aj-snapshot' command not found. Please ensure it is installed and in your system's PATH."
            }
        except Exception as e:
            print(f"An unexpected error occurred while running aj-snapshot: {e}", file=sys.stderr)
            return {
                "status": "error",
                "message": f"An unexpected error occurred: {e}"
            }

    def new_project(self):
        """Resets the sequencer to a new, empty project."""
        if self.playback_state != "stopped":
            self.stop()
        self.song = Song(name="New Song", tempo=120)
        self.close_virtual_ports()
        self.virtual_ports = []
        self.unset_control_port()
        self.last_project_basename = None
        self.is_dirty = False
        self.invalidate_song_length_cache()

        # --- Restart Jack Manager for the new empty project ---
        self.jack_manager.stop()
        self.jack_manager.start()

        print("New project created.")

    def list_tracks(self) -> str:
        if not self.song.tracks:
            return "No tracks in the song."
        lines = [f"Song: {self.song.name} | Tempo: {self.song.tempo} BPM | Time Signature: {self.song.time_signature_numerator}/{self.song.time_signature_denominator}"]
        metro_status = "OFF"
        if self.song.metronome_enabled:
            port_info = f" -> Port: {self.song.metronome_port_name}" if self.song.metronome_port_name else " (No port assigned)"
            metro_status = f"ON{port_info}"
        lines.append(f"Metronome: {metro_status}")
        loop_status = "OFF"
        if self.loop_enabled:
            start_pos = self._format_beats_to_position(self.loop_start_beat)
            end_pos = self._format_beats_to_position(self.loop_end_beat)
            loop_status = f"ON ({start_pos} -> {end_pos})"
        lines.append(f"Loop: {loop_status}")
        play_range_status = "OFF"
        if self.play_range_enabled:
            start_pos = self._format_beats_to_position(self.play_range_start_beat)
            end_pos = self._format_beats_to_position(self.play_range_end_beat)
            play_range_status = f"ON ({start_pos} -> {end_pos})"
        lines.append(f"Play Range: {play_range_status}")
        lines.append("=" * 20)
        for i, track in enumerate(self.song.tracks):
            status_info = ""
            if hasattr(track, 'is_muted') and track.is_muted: status_info += " [M]"
            if hasattr(track, 'is_solo') and track.is_solo: status_info += " [S]"
            if isinstance(track, MidiTrack):
                bank_info = ""
                if track.bank_msb is not None: bank_info = f", Bank: {track.bank_msb}:{track.bank_lsb or 0}"
                ch_info = f"Ch: {track.channel + 1}"
                prog_info = f"Prog: {track.instrument + 1}"
                vol_info = f"Vol: {track.volume:.2f}"
                pan_info = f"Pan: {track.pan:.2f}"
                vel_info = f"Vel: {track.velocity:.2f}"
                port_info = f" -> Port: {track.output_port_name}" if track.output_port_name else ""
                lines.append(f"[{i}] {track.name} (MIDI){status_info} ({ch_info}, {prog_info}{bank_info}, {vol_info}, {pan_info}, {vel_info}, {len(track.events)} events){port_info}")
            elif isinstance(track, AudioTrack):
                start_pos_str = self._format_beats_to_position(track.start_time)
                vol_info = f"Vol: {track.volume:.2f}"
                pan_info = f"Pan: {track.pan:.2f}"
                lines.append(f"[{i}] {track.name} (Audio){status_info} (File: {track.filepath}, Starts at: {start_pos_str}, {vol_info}, {pan_info})")
            elif isinstance(track, AutomationTrack):
                target_track_name = "N/A"
                if 0 <= track.target_track_index < len(self.song.tracks):
                    target_track_name = self.song.tracks[track.target_track_index].name
                lines.append(f"[{i}] {track.name} (Automation){status_info} (Target: {track.target_track_index} '{target_track_name}', {len(track.points)} points)")
            else:
                lines.append(f"[{i}] {track.name} (Unknown Type){status_info}")
        return "\n".join(lines)

    def list_ports(self) -> str:
        lines = []
        try:
            lines.append("Available MIDI Input Ports:")
            input_ports = get_input_names()
            if input_ports:
                for i, port in enumerate(input_ports): lines.append(f"  [{i}] {port}")
            else:
                lines.append("  (None found)")
            lines.append("\nAvailable MIDI Output Ports:")
            output_ports = get_output_names()
            virtual_port_names = [vp.name for vp in self.virtual_ports]
            all_outputs = output_ports + virtual_port_names
            if all_outputs:
                for i, port in enumerate(all_outputs): lines.append(f"  [{i}] {port}")
            else:
                lines.append("  (None found)")
            return "\n".join(lines)
        except Exception as e:
            return f"Error getting MIDI ports: {e}"

    def create_virtual_port(self, name: str) -> str:
        try:
            port = open_output(name, virtual=True)
            self.virtual_ports.append(port)
            self.is_dirty = True
            return f"Created virtual MIDI port: '{name}'"
        except Exception as e:
            return f"Error creating virtual port: {e}"

    def close_virtual_ports(self):
        for port in self.virtual_ports:
            if not port.closed:
                port.close()
        print("Virtual ports closed.")
        self._stop_carla_process()
        """Assure la fermeture propre des processus audio mpv à la sortie du séquenceur."""
        try:
            self.jack_manager._shutdown_audio_processes()
            print("✅ All mpv processes terminated.")
        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")
   
    def delete_virtual_port(self, name: str) -> str:
        port_to_delete = None
        for vp in self.virtual_ports:
            if vp.name == name:
                port_to_delete = vp
                break
        if port_to_delete:
            output = ""
            for track in self.song.tracks:
                if isinstance(track, MidiTrack) and track.output_port_name == port_to_delete.name:
                    track.output_port_name = None
                    output += f"Un-assigned port from track '{track.name}'.\n"
            port_to_delete.close()
            self.virtual_ports.remove(port_to_delete)
            self.is_dirty = True
            output += f"Virtual port '{name}' deleted."
            return output
        else:
            return f"Error: Virtual port '{name}' not found."

    def _find_or_create_automation_track(self, target_track_index: int, parameter_name: str) -> Optional[int]:
        if not 0 <= target_track_index < len(self.song.tracks):
            return None
        target_track = self.song.tracks[target_track_index]
        new_track_name = f"{target_track.name} {parameter_name.capitalize()} Automation"
        for i, track in enumerate(self.song.tracks):
            if isinstance(track, AutomationTrack) and track.target_track_index == target_track_index and track.name == new_track_name:
                return i
        print(f"\nCreating new automation track: '{new_track_name}'")
        new_track = AutomationTrack(name=new_track_name, target_track_index=target_track_index)
        self.song.add_track(new_track)
        self.is_dirty = True
        return len(self.song.tracks) - 1

    def _get_current_beat(self) -> float:
        if self.jack_manager and self.jack_manager.is_running and self.jack_manager.jack_client:
            try:
                _ , pos_struct = self.jack_manager.jack_client.transport_query_struct()
                pos = jack.position2dict(pos_struct)

                # Frame-based calculation is more reliable than bar/beat from transport
                frame = pos.get('frame', 0)
                samplerate = self.jack_manager.jack_client.samplerate
                beats_per_second = self.song.tempo / 60.0

                if samplerate > 0 and beats_per_second > 0:
                    return (frame / samplerate) * beats_per_second

                # Fallback for safety, but the primary method is now frame-based
                beats_per_bar = pos.get('beats_per_bar', self.song.time_signature_numerator)
                bar = pos.get('bar', 1)
                beat = pos.get('beat', 1)
                tick = pos.get('tick', 0)
                ticks_per_beat = pos.get('ticks_per_beat', self.song.ticks_per_beat)
                if ticks_per_beat > 0:
                    return (bar - 1) * beats_per_bar + (beat - 1) + (tick / ticks_per_beat)
                else:
                    return (bar - 1) * beats_per_bar + (beat - 1)

            except (jack.JackError, AttributeError):
                return 0.0
        return 0.0

    def _recording_thread_main(self, target_track, start_beat, inport_name, outport_name, num_beats_to_record, original_mute_state, enable_thru):
            # Le dictionnaire stockera maintenant un tuple: (start_beat, velocity)
            open_notes = {}
            outport = None
            first_note_detected = False
            recording_start_beat = None

            try:
                if outport_name and enable_thru:
                    try:
                        outport = mido.open_output(outport_name)
                        print(f"MIDI thru activé sur le port: {outport_name}")
                    except Exception as e:
                        print(f"Warning: Impossible d'ouvrir le port de sortie: {e}")

                with mido.open_input(inport_name) as inport:
                    print(f"Port d'entrée MIDI ouvert: {inport_name}")
                    list(inport.iter_pending())
                    print(f"En attente de la première note sur '{target_track.name}'...")
                    
                    wait_start_time = time.time()
                    pending_first_note = None
                    while not self._stop_event.is_set() and not first_note_detected:
                        # Case 1: Recording is cancelled (e.g., by pressing stop or record again)
                        if not self.is_recording:
                             print("Recording armed state cancelled.")
                             return

                        # Case 2: User presses the main Play button
                        if self.playback_state == 'playing':
                            print("Transport started. Beginning recording.")
                            first_note_detected = True
                            recording_start_beat = self._get_current_beat()
                            print(f"Enregistrement démarré à la position: {self._format_beats_to_position(recording_start_beat)}")
                            break # Exit the wait loop

                        # Case 3: User plays a note on the MIDI keyboard
                        msg = inport.poll()
                        if msg and msg.type == 'note_on' and msg.velocity > 0:
                            print(f"Première note détectée: {msg.note} (vélocité: {msg.velocity})")
                            first_note_detected = True
                            pending_first_note = msg
                            # This call starts the transport, which will be detected on the next loop,
                            # or the recording will just proceed. Let's start it directly.
                            self.play(start_beat=start_beat)
                            time.sleep(0.05) # Give transport a moment to start
                            recording_start_beat = self._get_current_beat()
                            print(f"Enregistrement démarré à la position: {self._format_beats_to_position(recording_start_beat)}")
                            break # Exit the wait loop

                        time.sleep(0.01)

                    if not first_note_detected:
                        # This can happen if stop is pressed while waiting
                        print("Recording start cancelled.")
                        self.is_recording = False
                        return

                    print("Début de l'enregistrement en temps réel...")
                    
                    while not self._stop_event.is_set():
                        current_beat = self._get_current_beat()
                        
                        # Traiter la première note qui a déclenché l'enregistrement
                        if pending_first_note:
                            msg = pending_first_note
                            if msg.note not in open_notes:
                                # Correction: Utiliser le temps de départ le plus précis possible
                                note_start_time = recording_start_beat if recording_start_beat is not None else current_beat
                                open_notes[msg.note] = (note_start_time, msg.velocity)
                                print(f"Note ON: {msg.note} à {self._format_beats_to_position(note_start_time)}")
                                if outport and enable_thru:
                                    outport.send(msg.copy(channel=target_track.channel))
                            pending_first_note = None

                        for msg in inport.iter_pending():
                            if outport and enable_thru and hasattr(msg, 'channel'):
                                outport.send(msg.copy(channel=target_track.channel))

                            if msg.type == 'note_on' and msg.velocity > 0:
                                if msg.note not in open_notes:
                                    open_notes[msg.note] = (current_beat, msg.velocity)
                                    print(f"Note ON: {msg.note} à {self._format_beats_to_position(current_beat)}")
                            
                            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                                if msg.note in open_notes:
                                    # Récupérer le temps ET la vélocité originale
                                    note_start, original_velocity = open_notes.pop(msg.note)
                                    duration = current_beat - note_start
                                    
                                    if duration > 0:
                                        # Utiliser la vélocité originale du "note_on"
                                        note = Note(pitch=msg.note, velocity=original_velocity, duration=duration)
                                        event = Event(notes=[note], start_time=note_start)
                                        
                                        target_track.add_event(event)
                                        self.is_dirty = True
                                        self.invalidate_song_length_cache()
                                        
                                        print(f"Note OFF: {msg.note}, durée: {duration:.2f} beats")
                        
                        if (num_beats_to_record and recording_start_beat and
                                current_beat >= (recording_start_beat + num_beats_to_record)):
                            print("Fin de la durée d'enregistrement atteinte. Arrêt de la lecture.")
                            # Schedule the transport stop on the main thread to avoid deadlocks
                            Clock.schedule_once(lambda dt: self._stop_playback_transport())
                            self._stop_event.set()
                            break
                        
                        time.sleep(0.001)

            except Exception as e:
                print(f"Erreur lors de l'enregistrement: {e}")
                import traceback
                traceback.print_exc()
            
            finally:
                print("Nettoyage de l'enregistrement...")
                
                current_beat = self._get_current_beat()
                # Gérer les notes orphelines
                for note, (note_start, original_velocity) in open_notes.items():
                    duration = current_beat - note_start
                    if duration > 0:
                        note_obj = Note(pitch=note, velocity=original_velocity, duration=duration)
                        event = Event(notes=[note_obj], start_time=note_start)
                        target_track.add_event(event)
                        print(f"Note orpheline fermée: {note}, durée: {duration:.2f} beats")
                
                if outport:
                    try: outport.close()
                    except: pass
                
                target_track.is_muted = original_mute_state
                self.is_recording = False
                self._stop_event.clear()
                
                print(f"Enregistrement terminé. {len(target_track.events)} événements enregistrés.")

    def _start_recording_internal(self, track_index: int, start_beat: float, num_beats_to_record: Optional[float], inport_name: str, replace_notes: bool, enable_thru: bool):
            target_track = self.song.tracks[track_index]
            if not isinstance(target_track, MidiTrack):
                print("Error: Recording is only supported for MIDI tracks.")
                return
            
            if target_track.record_mode == 'OFF':
                print(f"Error: Track '{target_track.name}' is not armed for recording.")
                return
            
            # --- NOUVELLE LOGIQUE D'ÉCRASEMENT AMÉLIORÉE ---
            if replace_notes:
                # Détermine la zone à effacer en fonction de la durée de l'enregistrement
                end_beat_for_deletion = float("inf") if num_beats_to_record is None else start_beat + num_beats_to_record
                
                final_events = []
                
                for event in target_track.events:
                    event_start_time = event.start_time
                    
                    # Cas 1: L'événement commence AVANT le point d'enregistrement.
                    # On vérifie si ses notes doivent être raccourcies.
                    if event_start_time < start_beat:
                        notes_to_keep_in_event = []
                        for note in event.notes:
                            note_end_time = event_start_time + note.duration
                            # Si la note se termine avant, on la garde telle quelle.
                            if note_end_time <= start_beat:
                                notes_to_keep_in_event.append(note)
                            # Si la note déborde sur la zone, on la tronçonne.
                            elif event_start_time < start_beat < note_end_time:
                                note.duration = start_beat - event_start_time
                                notes_to_keep_in_event.append(note)
                        
                        event.notes = notes_to_keep_in_event
                        if event.notes or event.cc_messages:
                            final_events.append(event)

                    # Cas 2: L'événement commence DANS la zone à effacer.
                    # On supprime ses notes mais on garde les messages CC éventuels.
                    elif start_beat <= event_start_time < end_beat_for_deletion:
                        event.notes.clear()
                        if event.cc_messages:
                            final_events.append(event)
                    
                    # Cas 3: L'événement commence APRÈS la zone d'effacement.
                    else:
                        final_events.append(event)

                # Nettoyage final : on enlève les événements devenus complètement vides.
                target_track.events = [e for e in final_events if e.notes or e.cc_messages]
                self.invalidate_song_length_cache()
                self.song_structure_changed += 1

                start_pos_msg = self._format_beats_to_position(start_beat)
                if end_beat_for_deletion == float('inf'):
                    print(f"Notes existantes effacées/tronquées à partir de {start_pos_msg}.")
                else:
                    end_pos_msg = self._format_beats_to_position(end_beat_for_deletion)
                    print(f"Notes existantes effacées/tronquées dans la plage {start_pos_msg} à {end_pos_msg}.")

            outport_name = target_track.output_port_name
            original_mute_state = target_track.is_muted
            if replace_notes:
                target_track.is_muted = True
                
            self.is_recording = True
            self.recording_thread = threading.Thread(target=self._recording_thread_main, args=(target_track, start_beat, inport_name, outport_name, num_beats_to_record, original_mute_state, enable_thru))
            self.recording_thread.daemon = True
            self.recording_thread.start()

    def prepare_recording(self, track_idx: int, start_beat: float, inport_name: str):
        """Prépare l'enregistrement qui sera déclenché par la première note MIDI"""
        if not 0 <= track_idx < len(self.song.tracks):
            return "Error: Invalid track index."
        
        target_track = self.song.tracks[track_idx]
        if not isinstance(target_track, MidiTrack):
            return "Error: Recording is only supported for MIDI tracks."
        
        # Vérifier que la piste est armée
        if target_track.record_mode == 'OFF':
            return f"Error: Track '{target_track.name}' is not armed for recording."
        
        # Déterminer le port d'entrée
        if inport_name is None:
            if self.default_record_port:
                inport_name = self.default_record_port
            else:
                # Trouver un port d'entrée par défaut
                input_ports = get_input_names()
                if input_ports:
                    inport_name = input_ports[0]
                else:
                    return "Error: No MIDI input ports available and no default port set."  
        
        # Sauvegarder les paramètres pour le démarrage différé
        self.last_record_settings = {
            "track_index": track_idx, 
            "start_beat": start_beat, 
            "num_beats_to_record": None,  # Pas de limite de durée
            "inport_name": inport_name, 
            "replace_notes": (target_track.record_mode == 'OVERWRITE'),
            "enable_thru": True
        }
        
        # Marquer que l'enregistrement est prêt mais pas encore démarré
        self.is_recording = True
        self._stop_event.clear()
        
        mode_text = "OVERWRITE" if target_track.record_mode == 'OVERWRITE' else "KEEP"
        start_pos = self._format_beats_to_position(start_beat)
        return f"Recording prepared on track '{target_track.name}' at {start_pos} in {mode_text} mode. Waiting for first MIDI note..."
    
    def record_track(self, track_idx: Optional[int] = None, start_beat: Optional[float] = None, num_beats_to_record: Optional[float] = None, inport_name: Optional[str] = None, replace_notes: Optional[bool] = None, enable_thru: bool = True):
            """Starts recording immediately or uses prepared settings."""
            
            if self.playback_state != "stopped":
                return "Error: Please stop playback before starting a new recording."

            
            # This is a new recording session initiated from the UI or command line
            if track_idx is not None:
                # --- Parameter Validation and Setup ---
                if not 0 <= track_idx < len(self.song.tracks):
                    return "Error: Invalid track index."
                
                target_track = self.song.tracks[track_idx]
                if not isinstance(target_track, MidiTrack):
                    return "Error: Recording is only supported for MIDI tracks."
                    
                if target_track.record_mode == 'OFF':
                    return f"Error: Track '{target_track.name}' is not armed for recording."
                
                # Determine the input port if not explicitly provided
                if inport_name is None:
                    if self.default_record_port:
                        inport_name = self.default_record_port
                    else:
                        input_ports = get_input_names()
                        if not input_ports:
                            return "Error: No MIDI input ports available and no default port set."
                        inport_name = input_ports[0]  # Fallback to the first available port
                
                if start_beat is None:
                    # If no start_beat is given, use the UI's start position for consistency
                    start_pos = self.ui_start_pos_str or "1:1"
                    start_beat = self.parse_position_to_beats(start_pos)
                    if start_beat is None:
                        start_beat = 0.0 # Fallback in case of invalid format

                # --- NEW LOGIC: Determine recording duration from UI end position ---
                end_pos = self.ui_end_pos_str
                end_beat = self.parse_position_to_beats(end_pos) if end_pos else None

                # If an end beat is defined and valid, calculate the number of beats to record.
                # Otherwise, num_beats_to_record remains as passed (likely None for infinite).
                if end_beat is not None and end_beat > start_beat:
                    num_beats_to_record = end_beat - start_beat

                # Determine if existing notes should be replaced based on the track's record mode
                should_replace_notes = target_track.record_mode == 'OVERWRITE'
                if replace_notes is not None:
                    # Allow the function call to override the track's mode
                    should_replace_notes = replace_notes

                # Save these settings for a potential re-record (`record_bis`)
                self.last_record_settings = {
                    "track_index": track_idx,
                    "start_beat": start_beat,
                    "num_beats_to_record": num_beats_to_record,
                    "inport_name": inport_name,
                    "replace_notes": should_replace_notes,
                    "enable_thru": enable_thru
                }

                # --- Start the Recording Thread ---
                self._stop_event.clear()
                self._start_recording_internal(
                    track_index=track_idx,
                    start_beat=start_beat,
                    num_beats_to_record=num_beats_to_record,
                    inport_name=inport_name,
                    replace_notes=should_replace_notes,
                    enable_thru=enable_thru
                )
                
                mode_text = "OVERWRITE" if should_replace_notes else "KEEP"
                start_pos = self._format_beats_to_position(start_beat)
                return f"Recording armed on track '{target_track.name}' at {start_pos} in {mode_text} mode. Waiting for first MIDI note..."

            # This block handles re-recording using previous settings ('record_bis')
            else:
                if self.last_record_settings is None:
                    return "Error: No recording settings prepared. Use 'record' with a track index first."
                
                settings = self.last_record_settings.copy()
                
                # Re-check the track's record mode in case it changed
                target_track = self.song.tracks[settings['track_index']]
                if isinstance(target_track, MidiTrack):
                    settings['replace_notes'] = (target_track.record_mode == 'OVERWRITE')

                self._stop_event.clear()
                self._start_recording_internal(**settings)
                return "Re-recording with last settings..."
        

    def set_record_mode(self, track_index: int, mode: str) -> str:
        """Définit le mode d'enregistrement pour une piste MIDI."""
        if not 0 <= track_index < len(self.song.tracks):
            return "Error: Invalid track index."
        
        track = self.song.tracks[track_index]
        if not isinstance(track, MidiTrack):
            return "Error: Record mode can only be set for MIDI tracks."
        
        if mode not in ['OFF', 'OVERWRITE', 'KEEP']:
            return "Error: Invalid record mode. Must be 'OFF', 'OVERWRITE', or 'KEEP'."
        
        # Sauvegarder l'ancien mode pour le log
        old_mode = track.record_mode
        
        # Désactiver les autres pistes si on active celle-ci
        if mode != 'OFF':
            for i, other_track in enumerate(self.song.tracks):
                if (isinstance(other_track, MidiTrack) and 
                    i != track_index and 
                    other_track.record_mode != 'OFF'):
                    other_track.record_mode = 'OFF'
                    print(f"DEBUG: Disabled track {i} '{other_track.name}' (was {other_track.record_mode})")
        
        track.record_mode = mode
        self.is_dirty = True
        
        mode_descriptions = {
            'OFF': 'Piste désactivée',
            'OVERWRITE': 'Écrase les notes existantes', 
            'KEEP': 'Conserve les notes existantes'
        }
        
        return f"Track '{track.name}' record mode changed from {old_mode} to {mode}: {mode_descriptions[mode]}"

    def record_bis(self, replace_notes: bool):
        """Re-records using the last saved parameters."""
        if self.playback_state != "stopped":
            return "Error: Please stop playback before starting a new recording."
        if self.last_record_settings is None:
            return "Error: No previous recording settings found. Use 'record' first."

        settings = self.last_record_settings.copy()
            
        # Vérifier le mode actuel de la piste
        track_index = settings['track_index']
        if 0 <= track_index < len(self.song.tracks):
            target_track = self.song.tracks[track_index]
            if isinstance(target_track, MidiTrack):
                # Utiliser le mode actuel de la piste
                settings['replace_notes'] = (target_track.record_mode == 'OVERWRITE')
        
        if 'enable_thru' not in settings:
            settings['enable_thru'] = True
            
        self._stop_event.clear()
        self._start_recording_internal(**settings)
        return "Re-recording with last used settings..."        

    def _calculate_song_length_in_beats(self) -> float:
        """Calculates the total length of the song in beats, considering both MIDI and audio tracks."""
        max_beats = 0.0
        tracks = self.song.tracks
        is_any_track_soloed = any(t.is_solo for t in tracks)

        for track in tracks:
            should_play = (track.is_solo or not is_any_track_soloed) and not track.is_muted
            if not should_play:
                continue

            if isinstance(track, MidiTrack):
                for event in track.events:
                    for note in event.notes:
                        event_end_beat = event.start_time + note.duration
                        if event_end_beat > max_beats:
                            max_beats = event_end_beat
            elif isinstance(track, AudioTrack):
                try:
                    duration_ms = self.audio_track_duration_ms.get(track.filepath)
                    if duration_ms is None:
                        with suppress_stdout_stderr():
                            segment = AudioSegment.from_file(track.filepath)
                        duration_ms = len(segment)
                        self.audio_track_duration_ms[track.filepath] = duration_ms

                    duration_beats = (duration_ms / 1000.0) * (self.song.tempo / 60.0)
                    track_end_beat = track.start_time + duration_beats
                    if track_end_beat > max_beats:
                        max_beats = track_end_beat
                except Exception as e:
                    print(f"Could not calculate duration for {track.filepath}: {e}")
                    pass
        return max_beats

    def _generate_automation_events(self, auto_track: 'AutomationTrack') -> List[dict]:
        """
        Generates a list of concrete MIDI/audio events from an automation track.
        """
        generated_events = []
        points = sorted(auto_track.points, key=lambda p: p.start_time)
        if not points:
            return []

        target_track_index = auto_track.target_track_index
        if not 0 <= target_track_index < len(self.song.tracks):
            return []
        target_track = self.song.tracks[target_track_index]
        param_map = {"vol": {"type": "midi_cc", "control": 7}, "pan": {"type": "midi_cc", "control": 10}, "vel": {"type": "velocity_multiplier"}, "prog": {"type": "program_change"}, **{f"cc{i}": {"type": "midi_cc", "control": i} for i in range(128)}}

        points_by_parameter: Dict[str, List[AutomationPoint]] = {}
        for p in points:
            points_by_parameter.setdefault(p.parameter, []).append(p)

        for parameter, param_points in points_by_parameter.items():
            param_config = param_map.get(parameter.lower())
            if not param_config:
                continue

            # First, add all the raw points to the event list. This ensures they are always present.
            for p in param_points:
                generated_events.append({
                    "time": p.start_time,
                    "target_track_index": target_track_index,
                    "parameter": p.parameter,
                    "param_config": param_config,
                    "value": p.value
                })

            # Now, iterate through the segments between points to generate the curves.
            for i in range(len(param_points) - 1):
                start_point = param_points[i]
                end_point = param_points[i+1]

                if start_point.curve == "none":
                    continue

                start_time = start_point.start_time
                end_time = end_point.start_time
                start_val = start_point.value
                end_val = end_point.value
                time_diff = end_time - start_time

                if time_diff <= 0:
                    continue

                granularity = 1.0 / 16.0  # Generate events for every 16th note
                num_steps = int(time_diff / granularity)
                if num_steps <= 1:
                    continue

                # Generate time steps and value steps based on the curve type
                t = np.linspace(0, 1, num_steps, endpoint=False)[1:]  # Exclude t=0
                time_steps = start_time + t * time_diff
                value_range = end_val - start_val
                value_steps = None

                if start_point.curve == "linear":
                    value_steps = start_val + t * value_range
                elif start_point.curve == "ease-in":
                    value_steps = start_val + (t**2) * value_range
                elif start_point.curve == "ease-out":
                    value_steps = start_val + (1 - (1 - t)**2) * value_range
                elif start_point.curve in ["ease-in-out", "sine"]:
                    value_steps = start_val + (0.5 * (1 - np.cos(np.pi * t))) * value_range
                else:
                    continue  # Unsupported curve type

                # Add the generated intermediate events
                if value_steps is not None:
                    for step_time, step_value in zip(time_steps, value_steps):
                        generated_events.append({
                            "time": step_time,
                            "target_track_index": target_track_index,
                            "parameter": start_point.parameter,
                            "param_config": param_config,
                            "value": step_value
                        })

        generated_events.sort(key=lambda e: e['time'])
        return generated_events

    def _resync_all_at_beat(self, beat: float, force_play: bool = False):
        """
        Resynchronizes all tracks to a specific beat.
        If JACK is running, it repositions the master transport. Otherwise, it just
        updates the internal sequencer state.
        If `force_play` is True, it will start the transport even if it wasn't rolling before.
        """
        print(f"\n[DIAGNOSTIC] === _resync_all_at_beat START (target_beat={beat:.6f}) ===")

        # --- Step 1: Update internal state (always) ---
        # This is the crucial part for the headless test to work.
        print("[DIAGNOSTIC] Syncing internal playhead...")
        self.jack_manager._sync_playhead_to_beat(beat)
        if self.gui_mode:
            self.current_beat = beat # Update the Kivy property for the UI

        # --- Step 2: Handle JACK and external processes (if running) ---
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            print("[DIAGNOSTIC] JACK not running. Skipping transport and audio sync.")
            print(f"[DIAGNOSTIC] === _resync_all_at_beat END (No JACK) ===\n")
            return

        try:
            # 1. Mémoriser si le transport était en cours de lecture
            was_rolling = self.jack_manager.jack_client.transport_state == jack.ROLLING
            print(f"[DIAGNOSTIC] was_rolling: {was_rolling}")

            # 2. Arrêter le transport pour garantir un état de base propre
            if was_rolling:
                self.jack_manager.jack_client.transport_stop()

            # --- NOUVELLE LOGIQUE ---
            # Forcer l'état de pause sur tous les lecteurs audio, peu importe leur état précédent.
            # C'est la clé pour garantir qu'ils sont prêts à recevoir une commande de recherche (seek).
            print("[DIAGNOSTIC] Forcing pause on all audio players to ensure a known state.")
            self.jack_manager.set_all_audio_pause_state(True)
            time.sleep(0.05)  # Un court délai crucial pour laisser aux processus mpv le temps de traiter la commande de pause.
            # -------------------------

            # 3. Repositionner le transport JACK à la position exacte du beat
            beats_per_second = self.song.tempo / 60.0
            samplerate = self.jack_manager.jack_client.samplerate
            if beats_per_second > 0 and samplerate > 0:
                target_frame = int((beat / beats_per_second) * samplerate)
                _ , pos = self.jack_manager.jack_client.transport_query_struct()
                original_frame = pos.frame
                pos.frame = target_frame
                self.jack_manager.jack_client.transport_reposition_struct(pos)
                print(f"[DIAGNOSTIC] Repositioning JACK transport from frame {original_frame} to {target_frame} (beat {beat:.6f})")

            # 4. Synchroniser les lecteurs externes avec la nouvelle position (l'état interne est déjà à jour)
            print("[DIAGNOSTIC] Seeking audio tracks (synchronously)...")
            self.jack_manager.seek_audio_to_beat(beat, synchronous=True)
            print("[DIAGNOSTIC] Audio track seek complete.")

            # 5. Régénérer les événements d'automation pour refléter le nouvel état (solo/mute)
            self.jack_manager._prepare_automation_events()

            # 6. Envoyer l'état actuel (volume, pan, etc.) à toutes les pistes audibles
            primed_by_automation = self.jack_manager._prime_automation_at_beat(beat)
            self.prime_all_tracks(primed_by_automation=primed_by_automation)

            # 7. CORRECTION : Mettre à jour l'état mute/solo des pistes audio
            is_any_track_soloed = any(t.is_solo for t in self.song.tracks if hasattr(t, 'is_solo'))
            with self.jack_manager.process_lock:
                for i, track in enumerate(self.song.tracks):
                    if isinstance(track, AudioTrack):
                        should_be_audible = (track.is_solo or not is_any_track_soloed) and not track.is_muted
                        ap = next((p for p in self.jack_manager.active_audio_processes if p.track_index == i), None)
                        if ap:
                            self.jack_manager._send_ipc_command(ap.socket_path, {"command": ["set_property", "mute", not should_be_audible]})

            # 8. Redémarrer le transport s'il était en cours de lecture ou si forcé
            if was_rolling or force_play:
                print("[DIAGNOSTIC] Restarting transport...")
                self.jack_manager.jack_client.transport_start()
                self.jack_manager.set_all_audio_pause_state(False)
                print("[DIAGNOSTIC] Transport restarted.")

        except jack.JackError as e:
            print(f"Error during resynchronization: {e}", file=sys.stderr)
        finally:
            print(f"[DIAGNOSTIC] === _resync_all_at_beat END (With JACK) ===\n")

    def play(self, start_beat: Optional[float] = None):
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            self.jack_manager.start()
            time.sleep(0.2) # Give JACK time to start and connect

        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            print("Error: Could not start JACK client.")
            return

        # Store the beat from which playback is starting
        effective_start_beat = start_beat if start_beat is not None else self.rewind_beat
        self.last_play_start_beat = effective_start_beat


        # If a start beat is provided, reposition the transport
        if start_beat is not None:
            beats_per_second = self.song.tempo / 60.0
            samplerate = self.jack_manager.jack_client.samplerate
            if beats_per_second > 0 and samplerate > 0:
                target_frame = int((start_beat / beats_per_second) * samplerate)
                _ , pos = self.jack_manager.jack_client.transport_query_struct()
                pos.frame = target_frame
                self.jack_manager.jack_client.transport_reposition_struct(pos)

        # Prime automation first and get the set of parameters it handled.
        primed_by_automation = self.jack_manager._prime_automation_at_beat(effective_start_beat)

        # Then, prime the tracks, passing in the set so it can skip what's already done.
        self.prime_all_tracks(primed_by_automation=primed_by_automation)

        # Simply tell JACK to start rolling
        if self.jack_manager.jack_client.transport_state != jack.ROLLING:
            self.jack_manager.jack_client.transport_start()
            self.playback_state = "playing"


    def pause(self):
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            return

        try:
            if self.jack_manager.jack_client.transport_state == jack.ROLLING:
                # Get current beat BEFORE stopping
                current_beat = self._get_current_beat()
                print(f"\n[DIAGNOSTIC] --- PAUSING at beat {current_beat:.6f} ---")
                self.jack_manager.jack_client.transport_stop()
                self.playback_state = "paused"
                # Store the precise beat for resume
                self.pause_beat = current_beat
            elif self.playback_state == "paused":
                print(f"\n[DIAGNOSTIC] --- RESUMING from beat {self.pause_beat:.6f} ---")
                # Resync all tracks to the last beat and resume
                self._resync_all_at_beat(self.pause_beat, force_play=True)
                self.playback_state = "playing"

        except jack.JackError as e:
            print(f"Error controlling JACK transport: {e}")

    def _stop_playback_transport(self):
        """Stops the JACK transport and resets the playhead. Safe to call from any thread."""
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            self.playback_state = "stopped"
            return

        if self.jack_manager.jack_client.transport_state == jack.ROLLING:
            self.jack_manager.silence_all_midi_notes()
            time.sleep(0.01)

        try:
            if self.jack_manager.jack_client.transport_state == jack.ROLLING:
                self.jack_manager.jack_client.transport_stop()
                print("JACK transport stopped.")

            beats_per_second = self.song.tempo / 60.0
            samplerate = self.jack_manager.jack_client.samplerate
            if beats_per_second > 0 and samplerate > 0:
                target_frame = int((self.rewind_beat / beats_per_second) * samplerate)
                _, pos = self.jack_manager.jack_client.transport_query_struct()
                pos.frame = target_frame
                self.jack_manager.jack_client.transport_reposition_struct(pos)
                self.jack_manager._sync_playhead_to_beat(self.rewind_beat)
                self.jack_manager.seek_audio_to_beat(self.rewind_beat)
                self.jack_manager.set_all_audio_pause_state(True)
        except jack.JackError as e:
            print(f"Error controlling JACK transport: {e}")

        self.playback_state = "stopped"
        self.jack_manager.silence_all_midi_notes()

        if self.gui_mode:
            self.current_beat = self.rewind_beat

        print("Sequencer stopped.")

    def stop(self):
        """Stops recording and/or playback."""
        if self.is_recording and self.recording_thread:
            print("Stopping recording...")
            self._stop_event.set()
            # Wait for the recording thread to finish its cleanup
            self.recording_thread.join(timeout=1.0)
            self.is_recording = False

            # After recording, invalidate the cache so the new length is calculated
            self.invalidate_song_length_cache()
            # Trigger a UI refresh to redraw all tracks to the new length
            self.song_structure_changed += 1

            # After the recording thread has stopped itself, we might not need to stop playback again
            # as it might have already done so. However, calling it ensures a consistent state.
            if self.playback_state != "stopped":
                 self._stop_playback_transport()
        else:
            self._stop_playback_transport()

        # LA LIGNE SUIVANTE EST LA CAUSE DU PROBLÈME ET A ÉTÉ VOLONTAIREMENT SUPPRIMÉE :
        # self.jack_manager.stop()

    def set_loop_range(self, start_pos_str: str, end_pos_str: str) -> str:
        """Sets the loop range without starting playback."""
        start_beat = self.parse_position_to_beats(start_pos_str)
        if start_beat is None:
            return "Error: Invalid start position."
        
        end_beat = self.parse_position_to_beats(end_pos_str)
        if end_beat is None:
            return "Error: Invalid end position."
        
        if end_beat <= start_beat:
            return "Error: End position must be after the start position."
        
        self.loop_start_beat = start_beat
        self.loop_end_beat = end_beat
        self.loop_enabled = True
        self.is_dirty = True
        
        start_pos = self._format_beats_to_position(start_beat)
        end_pos = self._format_beats_to_position(end_beat)
        return f"Loop range set from {start_pos} to {end_pos}. Use 'play' to start playback."

    def restart(self):
        """Restarts playback from the beginning."""
        print("When slaved to JACK, playback must be controlled by the JACK transport master.")
        print("Use your master application to return to the start of the song.")

    def seek(self, amount_str: str) -> str:
        """Seeks the JACK transport by a relative amount of measures or beats."""
        if not self.jack_manager.is_running or not self.jack_manager.jack_client:
            return "Error: JACK is not running. Cannot seek."

        try:
            if not amount_str.startswith(('+', '-')):
                amount_str = '+' + amount_str

            sign = 1 if amount_str.startswith('+') else -1
            unit = amount_str[-1].lower()
            value = int(amount_str[1:-1])

            if unit not in ['m', 'b']:
                raise ValueError("Invalid unit. Use 'm' for measures or 'b' for beats.")

            offset_beats = 0
            if unit == 'm':
                offset_beats = sign * value * self.song.time_signature_numerator
            else: # unit == 'b'
                offset_beats = sign * value

            # Get current position
            _, pos_struct = self.jack_manager.jack_client.transport_query_struct()
            pos_dict = jack.position2dict(pos_struct)
            current_frame = pos_dict.get('frame', 0)

            samplerate = self.jack_manager.jack_client.samplerate
            beats_per_second = self.song.tempo / 60.0

            if samplerate <= 0 or beats_per_second <= 0:
                return "Error: Cannot determine current position (invalid transport state)."

            current_beat = (current_frame / samplerate) * beats_per_second
            new_beat = current_beat + offset_beats
            if new_beat < 0:
                new_beat = 0.0

            # Reposition JACK transport
            target_frame = int((new_beat / beats_per_second) * samplerate)
            pos_struct.frame = target_frame
            self.jack_manager.jack_client.transport_reposition_struct(pos_struct)

            # Manually sync sequencer and audio players because transport_reposition does not trigger the timebase callback
            self.jack_manager._sync_playhead_to_beat(new_beat)
            self.jack_manager.seek_audio_to_beat(new_beat)

            return f"Seeked to position {self._format_beats_to_position(new_beat)}."

        except (ValueError, IndexError):
            return "Error: Invalid seek format. Use +/-<number><m|b> (e.g., '+1m', '-4b')."

    def send_cc_message(self, port_name: str, channel: int, control: int, value: int) -> str:
        """Sends a single CC message to a specified port."""
        port = self.open_ports.get(port_name)
        if not port:
            vp = next((p for p in self.virtual_ports if p.name == port_name), None)
            if vp:
                port = vp
        is_temp_port = False
        if not port:
            try:
                port = open_output(port_name)
                is_temp_port = True
            except Exception as e:
                return f"Error: Could not open MIDI port '{port_name}': {e}"
        if port:
            try:
                if not 0 <= channel <= 15:
                    return "Error: Channel must be between 0 and 15."
                if not 0 <= control <= 127:
                    return "Error: CC number must be between 0 and 127."
                if not 0 <= value <= 127:
                    return "Error: CC value must be between 0 and 127."
                msg = mido.Message('control_change', channel=channel, control=control, value=value)
                port.send(msg)
                time.sleep(0.01)
                return f"Sent CC message to {port_name}: Ch={channel+1}, CC={control}, Val={value}"
            except Exception as e:
                return f"Error sending CC message: {e}"
            finally:
                if is_temp_port and port:
                    port.close()
        return ""
