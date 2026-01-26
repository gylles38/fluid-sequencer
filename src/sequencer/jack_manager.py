from __future__ import annotations
import json
import math
import mido
import jack
import numpy as np
import collections
import os
import subprocess
import sys
import tempfile
import socket
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING, Dict

from kivy.clock import Clock

if TYPE_CHECKING:
    from .sequencer import Sequencer

from .models import (AudioTrack, MidiTrack, AutomationTrack, AutomationPoint,
                    is_midi_track, is_audio_track, is_automation_track)

@dataclass
class ActiveAudioProcess:
    process: subprocess.Popen
    socket_path: str
    track_index: int
    temp_filepath: Optional[str] = None # No longer mixing to a temp file


class JackManager:
    def __init__(self, sequencer: 'Sequencer'):
        self.sequencer = sequencer
        self.jack_client = None
        self.is_running = False
        self.last_beat = 0.0
        self.last_transport_state = jack.STOPPED
        self.open_ports = {} # track_name -> port (for ALSA compatibility)
        self.midi_out_ports = {} # MidiTrack object -> jack.Port (Native JACK)
        self._midi_out_ports_by_idx = {} # track_index -> jack.Port
        self.clavier_port = None
        self.metronome_port = None
        self._out_event_queue = collections.deque() # For UI-initiated MIDI (track_idx, mido_msg)
        self._in_process_callback = False
        self._current_block_frames = 0
        self._recorded_notes = {} # pitch -> (start_beat, target_track_idx, velocity)
        self._recorded_events_to_merge = collections.deque()
        self._live_forwarded_notes = {} # pitch -> target_track_idx
        self._live_forwarded_ccs = collections.defaultdict(set) # cc_num -> set(target_track_idx)
        self._live_activity = collections.defaultdict(set) # track_idx -> set(pitch)
        self.next_event_indices = []
        self._active_notes = {} # key: (track_idx, note_pitch), value: end_beat
        self._metronome_notes_to_turn_off = []
        self.active_audio_processes: List[ActiveAudioProcess] = []
        self.process_lock = threading.Lock()
        self.sync_lock = threading.Lock()
        self._rt_log_lock = threading.Lock()
        self._rt_log_queue = collections.deque(maxlen=500)
        self._ipc_queue = collections.deque(maxlen=100) # (socket_path, command_dict)
        self._pending_seek_beat: Optional[float] = None
        self._log_worker_thread = None
        self._display_thread = None
        self._display_stop_event = threading.Event()
        self.automation_events = []
        self.next_automation_event_index = 0
        self.event_to_ignore: Optional[dict] = None
        self._routing_track: Optional[AutomationTrack] = None

        # --- Diagnostics (RT Safe) ---
        self._diag_clavier_in = 0
        self._diag_clavier_routed = 0
        self._diag_last_target_idx = -1
        self._last_beat_rt = 0.0 # Atomic float for UI sync
        self._last_transport_state_rt = jack.STOPPED

        # --- RT Caches ---
        self._cached_song_length = 16.0
        self._cached_loop_enabled = False
        self._cached_loop_start = 0.0
        self._cached_loop_end = 16.0
        self._cached_play_range_enabled = False
        self._cached_play_range_end = 16.0
        self._cached_is_recording = False
        self._cached_tempo = 120.0
        self._cached_tracks = []
        self._cached_midi_mappings = []
        self._cached_audio_ends = {} # track_idx -> end_beat
        self._cached_metronome_enabled = False
        self._cached_metronome_volume = 1.0
        self._cached_metronome_pan = 0.0
        self._cached_metronome_channel = 9
        self._cached_metronome_pitch_downbeat = 76
        self._cached_metronome_pitch_beat = 77
        self._cached_transport_ccs = {}
        self._cached_track_overrides = {}
        self._repositioning_pending = False
        self._target_beat = 0.0
        self._reposition_frames = 0

        # --- UI Command Flags (from MIDI) ---
        self._pending_play_pause = False
        self._pending_stop = False
        self._pending_record = False
        self._pending_mappings = collections.deque() # (mapping_obj, value)

        # --- Dynamic Audio Correction ---
        self.CORRECTION_GAIN = 0.02
        self.CORRECTION_THRESHOLD = 0.03 # 30ms
        self._correction_thread = None
        self._correction_stop_event = threading.Event()
        self.last_applied_speeds = {}

    def find_port_by_name(self, pattern: str | None):
        """
        Cherche un port JACK complet qui contient le 'pattern' donné.
        Retourne le nom complet du premier port trouvé, ou None.
        """
        if not pattern:
            return None
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

        # Link the global routing track from the song
        self._routing_track = self.sequencer.song.input_routing

        tracks = self.sequencer.song.tracks
        is_any_track_soloed = any(getattr(t, 'is_solo', False) for t in tracks)
        for track in tracks:
            if is_automation_track(track):
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
            self._log_rt(f"Starting JackManager. current is_running={self.is_running}")
            if self.is_running:
                print("JACK client is already running.")
                return

            self._cb_count = 0 # Reset heartbeat counter

            try:
                # Ensure client name is safe for JACK and unique
                base_name = self.sequencer.song.name or "NewSong"
                sanitized_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in base_name)
                client_name = f"{sanitized_name}-{os.getpid()}"

                self._log_rt(f"Creating JACK client: {client_name}")

                # Retry logic for client creation
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        self.jack_client = jack.Client(client_name)
                        break
                    except jack.JackError as e:
                        if attempt < max_retries - 1:
                            print(f"JACK client creation attempt {attempt+1} failed, retrying in 0.5s...")
                            time.sleep(0.5)
                        else:
                            raise e

                tracks = self.sequencer.song.tracks

                # --- Native JACK MIDI Port Setup ---
                self.midi_out_ports.clear()
                self._midi_out_ports_by_idx.clear()
                self.open_ports.clear()

                # --- 1. Essential Ports (Clavier always first) ---
                try:
                    self.clavier_port = self.jack_client.midi_inports.register("Clavier")
                    self._log_rt(f"Registered input port: {self.clavier_port.name}")
                except Exception as e:
                    self._log_rt(f"FAILED to register Clavier port: {e}")
                    # If we can't register the main input, the bridge won't work.
                    # We might want to raise here, but let's try to continue.

                # Metronome port
                try:
                    self.metronome_port = self.jack_client.midi_outports.register("Metronome")
                    if self.sequencer.song.metronome_port_name:
                        self.open_ports[self.sequencer.song.metronome_port_name] = self.metronome_port
                except Exception as e:
                    self._log_rt(f"Error registering metronome port: {e}")

                # --- 2. Track specific ports ---
                self._log_rt(f"Scanning {len(tracks)} tracks for MIDI ports...")
                for i, track in enumerate(tracks):
                    midi_status = is_midi_track(track)
                    self._log_rt(f"Track {i}: name='{track.name}', is_midi={midi_status}")
                    if midi_status:
                        # Use a safe name for the port
                        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in track.name)
                        port_name = f"out_{i}_{safe_name}"
                        try:
                            port = self.jack_client.midi_outports.register(port_name)
                            self.midi_out_ports[track] = port
                            self._midi_out_ports_by_idx[i] = port

                            # Update the track's output port name to match the registered JACK port
                            # This allows auto_connect_dynamic to work reliably.
                            short_port_name = port.name.split(':')[-1]
                            track.output_port_name = short_port_name

                            self._log_rt(f"Registered output port for track '{track.name}': {port.name}")
                            # Compatibility mapping
                            self.open_ports[short_port_name] = port
                        except Exception as e:
                            self._log_rt(f"Error registering port for track {i}: {e}")

                # --- Audio Track Setup ---
                with self.process_lock:
                    self.active_audio_processes.clear()
                    for i, track in enumerate(tracks):
                        if is_audio_track(track):
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
                self.jack_client.activate()
                self.is_running = True
                self._log_rt("JackManager started successfully.")

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

                self._display_stop_event.clear()
                self._log_worker_thread = threading.Thread(target=self._log_worker_loop)
                self._log_worker_thread.daemon = True
                self._log_worker_thread.start()

                if not self.sequencer.gui_mode:
                    self._display_thread = threading.Thread(target=self._display_loop)
                    self._display_thread.daemon = True
                    self._display_thread.start()

                # --- Start the correction thread ---
                self._correction_stop_event.clear()
                self._correction_thread = threading.Thread(target=self._audio_correction_loop)
                self._correction_thread.daemon = True
                self._correction_thread.start()

                print("JACK client started and activated.")
            except Exception as e:
                self._log_rt(f"Error starting JACK client: {e}")
                print(f"Error starting JACK client: {e}")
                if self.jack_client:
                    try: self.jack_client.close()
                    except: pass
                self.jack_client = None

    def stop(self):
        """Stops the JACK client, its threads, and all related processes."""
        if not self.is_running:
            return

        # Signal all threads to stop
        self._display_stop_event.set()

        # Stop the display thread if it's running
        if self._display_thread and self._display_thread.is_alive():
            self._display_thread.join(timeout=1.0)
            self._display_thread = None

        # Stop the log worker
        if self._log_worker_thread and self._log_worker_thread.is_alive():
            self._log_worker_thread.join(timeout=1.0)
            self._log_worker_thread = None

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

    def reposition_to_beat(self, beat: float):
        """Repositions the JACK transport to a specific beat and sets up the grace period."""
        if not self.jack_client:
            return

        try:
            # 1. Update internal RT target
            self._target_beat = float(beat)
            self._repositioning_pending = True
            if hasattr(self, '_reposition_frames'):
                self._reposition_frames = 0

            # 2. Command JACK transport
            beats_per_second = self.sequencer.song.tempo / 60.0
            samplerate = self.jack_client.samplerate
            if beats_per_second > 0 and samplerate > 0:
                target_frame = int((beat / beats_per_second) * samplerate)
                _ , pos = self.jack_client.transport_query_struct()
                pos.frame = target_frame
                self.jack_client.transport_reposition_struct(pos)
                self._log_rt(f"JACK repositioning to beat {beat:.4f} (frame {target_frame})")
        except Exception as e:
            print(f"Error during reposition: {e}", file=sys.stderr)

    def refresh_automation(self):
        """Forces a refresh of automation events and routing track from the project."""
        self._prepare_automation_events()

        # 1. Snapshot simple properties
        self._cached_tracks = list(self.sequencer.song.tracks)
        self._cached_tempo = float(self.sequencer.song.tempo)
        self._cached_is_recording = self.sequencer.is_recording
        self._cached_metronome_enabled = self.sequencer.song.metronome_enabled
        self._cached_metronome_volume = self.sequencer.song.metronome_volume
        self._cached_metronome_pan = self.sequencer.song.metronome_pan
        self._cached_metronome_channel = self.sequencer.metronome_channel
        self._cached_metronome_pitch_downbeat = self.sequencer.metronome_pitch_downbeat
        self._cached_metronome_pitch_beat = self.sequencer.metronome_pitch_beat
        self._cached_midi_mappings = list(self.sequencer.song.midi_mappings)
        self._cached_track_overrides = dict(self.sequencer.track_overrides)

        # Cache transport CCs
        mc = self.sequencer.midi_config
        self._cached_transport_ccs = {
            'play_pause': mc.get_transport_cc('play_pause'),
            'stop': mc.get_transport_cc('stop'),
            'record_arm': mc.get_transport_cc('record_arm')
        }

        # 2. Cache armed index for RT safety
        self._cached_armed_idx = self.sequencer.get_armed_track_index()

        # 3. Cache channels and warm up audio durations
        self._cached_first_midi_idx = None
        self._cached_channels = {}
        self._cached_audio_ends = {}

        for i, t in enumerate(self._cached_tracks):
            if is_midi_track(t):
                if self._cached_first_midi_idx is None:
                    self._cached_first_midi_idx = i
                self._cached_channels[i] = getattr(t, 'channel', 0)
            elif is_audio_track(t):
                # Warm up duration cache (HEAVY - call from UI thread)
                duration = self.sequencer._get_audio_duration_in_beats(t)
                self._cached_audio_ends[i] = t.start_time + duration

        # 4. Cache song length and loop points
        self._cached_song_length = max(1.0, self.sequencer.get_song_length_in_beats())
        self._cached_loop_enabled = self.sequencer.loop_enabled
        self._cached_loop_start = self.sequencer.loop_start_beat
        self._cached_loop_end = self.sequencer.loop_end_beat
        self._cached_play_range_enabled = self.sequencer.play_range_enabled
        self._cached_play_range_end = self.sequencer.play_range_end_beat

        self._log_rt(f"Refreshed automation: tracks={len(self._cached_tracks)}, length={self._cached_song_length:.2f}, loop={self._cached_loop_enabled}")

    def get_live_activity(self) -> Dict[int, List[int]]:
        """Returns a copy of the current live MIDI activity."""
        with self.sync_lock:
            return {idx: list(notes) for idx, notes in self._live_activity.items()}

    def get_diagnostics(self) -> dict:
        """Returns engine diagnostics for UI/CLI display."""
        clavier_connected = False
        clavier_connections = []
        if self.jack_client and self.clavier_port:
            try:
                clavier_connections = self.jack_client.get_all_connections(self.clavier_port)
                clavier_connected = len(clavier_connections) > 0
            except Exception:
                pass

        return {
            "clavier_in": self._diag_clavier_in,
            "clavier_routed": self._diag_clavier_routed,
            "clavier_connected": clavier_connected,
            "clavier_connections": clavier_connections,
            "last_target_idx": self._diag_last_target_idx,
            "out_ports_count": len(self.midi_out_ports),
            "is_running": self.is_running,
            "cb_count": getattr(self, '_cb_count', 0),
            "monitor_alive": self._log_worker_thread and self._log_worker_thread.is_alive(),
            "cached_armed_idx": getattr(self, '_cached_armed_idx', None),
            "cached_first_midi_idx": getattr(self, '_cached_first_midi_idx', None)
        }

    def _queue_midi_message(self, track_index: int, msg: mido.Message):
        """Queues a MIDI message to be sent in the next JACK process cycle. Use track_index=-1 for metronome."""
        self._out_event_queue.append((track_index, msg))

    def _queue_ipc_command(self, socket_path: str, command: dict):
        """Queues an IPC command for the background monitor thread."""
        self._ipc_queue.append((socket_path, command))

    def send_midi_to_track(self, track_index: int, msg: mido.Message):
        """Sends a MIDI message to a track, identifying it by index for backward compatibility."""
        if self.is_running:
            try:
                if 0 <= track_index < len(self.sequencer.song.tracks):
                    track = self.sequencer.song.tracks[track_index]
                    self._queue_midi_message(track, msg)
                elif track_index == -1:
                    self._queue_midi_message(-1, msg)
                else:
                    self._log_rt(f"send_midi_to_track: Invalid index {track_index}")
            except (IndexError, AttributeError) as e:
                self._log_rt(f"send_midi_to_track: Error {e}")

    def _log_rt(self, message: str):
        """Queues a message for logging. Safe to call from RT thread."""
        self._rt_log_queue.append(f"{time.time():.4f} [RT] {message}")

    def _log_worker_loop(self):
        """Background thread to handle logs, IPC commands, and seeks."""
        while not self._display_stop_event.is_set():
            processed_anything = False

            # 0. Handle Seeks (Highest priority for background tasks)
            if self._pending_seek_beat is not None:
                beat = self._pending_seek_beat
                self._pending_seek_beat = None
                self.seek_audio_to_beat(beat)
                processed_anything = True

            # 1. Handle Logs
            try:
                if self._rt_log_queue:
                    msg = self._rt_log_queue.popleft()
                    with open("/tmp/sequencer_rt.log", "a") as f:
                        f.write(msg + "\n")
                    processed_anything = True
            except Exception: pass

            # 2. Handle background IPC commands
            try:
                if self._ipc_queue:
                    socket_path, cmd = self._ipc_queue.popleft()
                    self._send_ipc_command(socket_path, cmd)
                    processed_anything = True
            except Exception: pass

            if not processed_anything:
                time.sleep(0.05)

    def _write_midi_safe(self, port, offset: int, data: bytes):
        """Safely writes MIDI data to a JACK port, catching buffer overflows."""
        try:
            # Native JACK MIDI events must be written as bytes
            port.write_midi_event(offset, data)
        except Exception as e:
            # Error -105 is typically ENOBUFS (buffer overflow)
            if "-105" not in str(e):
                self._log_rt(f"Write error: {e}")

    def silence_all_midi_notes(self):
        """Sends note_off messages for all currently playing MIDI notes and clears live activity."""
        # 1. Clear scheduled notes
        if self._active_notes:
            for (track_idx, pitch), end_beat in list(self._active_notes.items()):
                try:
                    track = self.sequencer.song.tracks[track_idx]
                    if is_midi_track(track):
                        note_off_msg = mido.Message('note_off', channel=track.channel, note=pitch, velocity=0)
                        self._queue_midi_message(track, note_off_msg)
                except (IndexError, AttributeError, ValueError):
                    pass
            self._active_notes.clear()

        # 2. Clear live forwarded notes
        if self._live_forwarded_notes:
            for pitch, track in list(self._live_forwarded_notes.items()):
                try:
                    note_off_msg = mido.Message('note_off', channel=getattr(track, 'channel', 0), note=pitch, velocity=0)
                    self._queue_midi_message(track, note_off_msg)
                except Exception:
                    pass
            self._live_forwarded_notes.clear()

        # 3. Clear UI activity
        with self.sync_lock:
            self._live_activity.clear()

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

    def ensure_track_ports(self):
        """Registers JACK output ports for any tracks that don't have one yet."""
        if not self.jack_client or not self.is_running:
            return

        tracks = self.sequencer.song.tracks

        # 1. Register missing ones
        for i, track in enumerate(tracks):
            if is_midi_track(track) and track not in self.midi_out_ports:
                # Use a safe name for the port
                safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in track.name)
                port_name = f"out_{i}_{safe_name}"
                try:
                    port = self.jack_client.midi_outports.register(port_name)
                    self.midi_out_ports[track] = port
                    # Also keep an index-based mapping for robust lookup in RT thread
                    if not hasattr(self, '_midi_out_ports_by_idx'):
                        self._midi_out_ports_by_idx = {}
                    self._midi_out_ports_by_idx[i] = port

                    # Update the track's output port name to match the registered JACK port
                    short_port_name = port.name.split(':')[-1]
                    track.output_port_name = short_port_name

                    # Compatibility mapping
                    self.open_ports[short_port_name] = port

                    msg = f"Dynamically registered native JACK MIDI port: {port_name}"
                    print(msg)
                    self._log_rt(msg)
                except Exception as e:
                    err = f"Error dynamically registering port for track {i}: {e}"
                    print(err)
                    self._log_rt(err)

        # 3. Cleanup stale ports (optional, but keep references consistent)
        # Note: We don't unregister from JACK here to avoid disconnecting Carla users
        # but we could if needed. For now, we just keep the dict up to date.
        stale_tracks = [t for t in self.midi_out_ports if t not in tracks]
        for t in stale_tracks:
            # Maybe don't delete to avoid breaking things, but it's cleaner
            pass

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
        # Take a copy to avoid holding lock during sleeps
        with self.process_lock:
            processes_to_stop = list(self.active_audio_processes)
            self.active_audio_processes.clear()

        for ap in processes_to_stop:
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

    def _get_input_routing_value(self, beat: float) -> Optional[int]:
        """
        Returns the target track index for MIDI input routing at the given beat.
        Prioritization:
        1. Automation points on the routing track.
        2. Armed track index (cached).
        3. Final Fallback: First MIDI track (cached).
        """
        # 1. Automation Priority
        if self._routing_track and self._routing_track.points:
            routing_points = [p for p in self._routing_track.points if p.parameter == 'input_routing']
            if routing_points:
                val = self._routing_track.get_value_at(beat, 'input_routing')
                if val is not None:
                    return int(round(val))

        # 2. Armed Track Priority (RT safe)
        armed_idx = getattr(self, '_cached_armed_idx', None)
        if armed_idx is not None:
            return armed_idx

        # 3. Final Fallback (RT safe)
        return getattr(self, '_cached_first_midi_idx', None)

    def _handle_control_midi(self, msg: mido.Message):
        """Handles MIDI control messages (transport, mappings) from the 'Clavier' port."""
        try:
            if msg.type == 'control_change':
                # --- Handle Transport Controls (RT Safe: No Clock.schedule_once) ---
                if msg.value == 127:
                    if msg.control == self._cached_transport_ccs.get('play_pause'):
                        self._pending_play_pause = True
                    elif msg.control == self._cached_transport_ccs.get('stop'):
                        self._pending_stop = True
                    elif msg.control == self._cached_transport_ccs.get('record_arm'):
                        self._pending_record = True

                # --- Handle Custom MIDI Mappings (Volume, Pan, etc.) ---
                for mapping in self._cached_midi_mappings:
                    if mapping.channel == msg.channel and mapping.control == msg.control:
                        # Queue for UI thread processing
                        self._pending_mappings.append((mapping, msg.value))
        except Exception:
            pass

    def _record_midi_event(self, msg: mido.Message, start_beat_of_block: float, offset: int):
        """Records a MIDI event from the 'Clavier' port."""
        try:
            samplerate = self.jack_client.samplerate
            beats_per_second = self.sequencer.song.tempo / 60.0
            accurate_beat = start_beat_of_block + (offset / samplerate) * beats_per_second

            if msg.type == 'note_on' and msg.velocity > 0:
                # Auto-start transport if recording and stopped (RT Safe flag)
                if self._last_transport_state_rt == jack.STOPPED:
                    self._pending_play_pause = True

                track_idx = self._get_input_routing_value(accurate_beat)
                if track_idx is not None:
                    self._recorded_notes[msg.note] = (accurate_beat, track_idx, msg.velocity)

            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in self._recorded_notes:
                    start_beat, track_idx, velocity = self._recorded_notes.pop(msg.note)
                    duration = accurate_beat - start_beat
                    if duration > 0:
                        self._recorded_events_to_merge.append({
                            'type': 'note',
                            'track_idx': track_idx,
                            'start_time': start_beat,
                            'pitch': msg.note,
                            'velocity': velocity,
                            'duration': duration
                        })

            elif msg.type == 'control_change':
                track_idx = self._get_input_routing_value(accurate_beat)
                if track_idx is not None:
                    self._recorded_events_to_merge.append({
                        'type': 'cc',
                        'track_idx': track_idx,
                        'start_time': accurate_beat,
                        'control': msg.control,
                        'value': msg.value
                    })
        except Exception:
            pass

    def _sync_playhead_to_beat(self, beat_pos: float):
        self.last_beat = beat_pos
        tracks = self._cached_tracks
        num_tracks = len(tracks)
        self.next_event_indices = [0] * num_tracks
        self._active_notes.clear()

        for i, track in enumerate(tracks):
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

    def _process_midi_events(self, start_beat_of_block, end_beat_of_block):
        tracks = self._cached_tracks
        is_any_track_soloed = any(getattr(t, 'is_solo', False) for t in tracks)

        for i, track in enumerate(tracks):
            # --- Live Preview Override ---
            if i in self._cached_track_overrides:
                is_recording_this_track = self._cached_is_recording and self._cached_armed_idx == i
                if not is_recording_this_track:
                    track = self._cached_track_overrides[i]

            if not is_midi_track(track) or track not in self.midi_out_ports:
                continue

            should_be_audible = (getattr(track, 'is_solo', False) or not is_any_track_soloed) and not getattr(track, 'is_muted', False)
            port = self.midi_out_ports[track]

            if i >= len(self.next_event_indices):
                self.next_event_indices.extend([0] * (i - len(self.next_event_indices) + 1))

            while self.next_event_indices[i] < len(track.events):
                event = track.events[self.next_event_indices[i]]

                if start_beat_of_block <= event.start_time < end_beat_of_block:
                    if should_be_audible:
                        # Calculate offset within block for better timing
                        offset = 0
                        if end_beat_of_block > start_beat_of_block:
                            offset = int((event.start_time - start_beat_of_block) / (end_beat_of_block - start_beat_of_block) * self._current_block_frames)
                            offset = max(0, min(self._current_block_frames - 1, offset))

                        for note in event.notes:
                            final_velocity = int(note.velocity * track.velocity)
                            final_velocity = max(0, min(127, final_velocity))
                            note_on_msg = mido.Message('note_on', channel=track.channel, note=note.pitch, velocity=final_velocity)
                            self._write_midi_safe(port, offset, bytes(note_on_msg.bytes()))
                            note_end_beat = event.start_time + note.duration
                            self._active_notes[(i, note.pitch)] = note_end_beat
                        for cc in event.cc_messages:
                            cc_msg = mido.Message('control_change', channel=track.channel, control=cc.control, value=cc.value)
                            self._write_midi_safe(port, offset, bytes(cc_msg.bytes()))
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

        tracks = self._cached_tracks
        for track in tracks:
            if not isinstance(track, AutomationTrack):
                continue

            # This logic only works if the target track is valid
            if not 0 <= track.target_track_index < len(tracks):
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

    def _apply_automation_event(self, event: dict, offset: int = 0):
        """Applies a single automation event."""
        target_track_index = event['target_track_index']
        if not 0 <= target_track_index < len(self._cached_tracks):
            return

        target_track = self._cached_tracks[target_track_index]
        param_config = event['param_config']
        value = event['value']
        param_name = event['parameter'].lower()

        # Branch by Track Type first for clarity and correctness
        if is_midi_track(target_track):
            if param_config.get('type') == 'midi_cc':
                midi_value = 0
                if param_name == 'vol':
                    midi_value = int(value * 127)
                elif param_name == 'pan':
                    midi_value = int((value + 1.0) / 2.0 * 127)
                else:
                    midi_value = int(value)

                # --- FIX: Clamp the final value to the valid MIDI range ---
                midi_value = max(0, min(127, midi_value))

                msg = mido.Message('control_change', channel=target_track.channel, control=param_config['control'], value=midi_value)
                if self._in_process_callback and target_track in self.midi_out_ports:
                    self._write_midi_safe(self.midi_out_ports[target_track], offset, bytes(msg.bytes()))
                else:
                    self._queue_midi_message(target_track, msg)
            elif param_config.get('type') == 'program_change':
                program_value = max(0, min(127, int(value)))
                msg = mido.Message('program_change', channel=target_track.channel, program=program_value)
                if self._in_process_callback and target_track in self.midi_out_ports:
                    self._write_midi_safe(self.midi_out_ports[target_track], offset, bytes(msg.bytes()))
                else:
                    self._queue_midi_message(target_track, msg)
            elif param_config.get('type') == 'velocity_multiplier':
                target_track.velocity = float(value)

        elif is_audio_track(target_track):
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
                offset = 0
                if end_beat_of_block > start_beat_of_block:
                    offset = int((event['time'] - start_beat_of_block) / (end_beat_of_block - start_beat_of_block) * self._current_block_frames)
                    offset = max(0, min(self._current_block_frames - 1, offset))
                self._apply_automation_event(event, offset=offset)
                self.next_automation_event_index += 1
            elif event['time'] >= end_beat_of_block:
                break
            else:
                self.next_automation_event_index += 1

    def _process_metronome(self, start_beat_of_block, end_beat_of_block):
        if self._cached_metronome_enabled and self.metronome_port:
            port = self.metronome_port
            beat_to_check = math.ceil(start_beat_of_block)

            if beat_to_check < end_beat_of_block:
                # Calculate offset for the panning CC
                offset = int((beat_to_check - start_beat_of_block) / (end_beat_of_block - start_beat_of_block) * self._current_block_frames) if end_beat_of_block > start_beat_of_block else 0
                offset = max(0, min(self._current_block_frames - 1, offset))

                midi_pan = int((self._cached_metronome_pan + 1.0) / 2.0 * 127)
                pan_msg = mido.Message('control_change', channel=self._cached_metronome_channel, control=10, value=midi_pan)
                self._write_midi_safe(port, offset, bytes(pan_msg.bytes()))

            # Safeguard: don't generate more than 16 metronome ticks in one block
            ticks_this_block = 0
            while beat_to_check < end_beat_of_block and ticks_this_block < 16:
                # Offset for the note
                offset = int((beat_to_check - start_beat_of_block) / (end_beat_of_block - start_beat_of_block) * self._current_block_frames) if end_beat_of_block > start_beat_of_block else 0
                offset = max(0, min(self._current_block_frames - 1, offset))

                # Use authoritative cached tracks/tempo for beats_per_measure lookup?
                # For now song object is okay for numerator as it's just an int.
                # For numerator, we can use the song object as it's an immutable int during playback usually.
                # But to be safe we should probably cache it too if it changes.
                beats_per_measure = self.sequencer.song.time_signature_numerator
                is_downbeat = (int(beat_to_check) % beats_per_measure) == 0 if beats_per_measure > 0 else beat_to_check == 0
                pitch = self._cached_metronome_pitch_downbeat if is_downbeat else self._cached_metronome_pitch_beat
                velocity = int(100 * self._cached_metronome_volume)
                note_on = mido.Message('note_on', channel=self._cached_metronome_channel, note=pitch, velocity=velocity)
                note_off = mido.Message('note_off', channel=self._cached_metronome_channel, note=pitch, velocity=0)
                self._write_midi_safe(port, offset, bytes(note_on.bytes()))
                self._metronome_notes_to_turn_off.append((note_off, offset)) # Store offset for note_off
                beat_to_check += 1
                ticks_this_block += 1

    def _check_for_loop_and_play_range(self, start_beat_of_block, end_beat_of_block):
        # 0. Play Range Handling (One-shot)
        if self._cached_play_range_enabled and self._cached_play_range_end > 0.1:
            if end_beat_of_block >= self._cached_play_range_end:
                if start_beat_of_block < self._cached_play_range_end:
                    self._log_rt(f"Stopping at play range end: end_beat={end_beat_of_block:.4f}, start_beat={start_beat_of_block:.4f}, range_end={self._cached_play_range_end:.4f}")
                    self.jack_client.transport_stop()
                    return
                elif start_beat_of_block >= self._cached_play_range_end:
                    # We are ALREADY past the end of the play range
                    self._log_rt(f"Already past play range end: start_beat={start_beat_of_block:.4f}, range_end={self._cached_play_range_end:.4f}")
                    self.jack_client.transport_stop()
                    return

        # 1. Loop Handling (RT Safe)
        if self._cached_loop_enabled and end_beat_of_block >= self._cached_loop_end:
            if start_beat_of_block < self._cached_loop_end:
                # When looping, re-prime automation to the loop start point
                self._prime_automation_at_beat(self._cached_loop_start)

                beats_per_second = self.sequencer.song.tempo / 60.0
                samplerate = self.jack_client.samplerate
                if beats_per_second > 0 and samplerate > 0:
                    target_frame = int((self._cached_loop_start / beats_per_second) * samplerate)
                    _ , pos = self.jack_client.transport_query_struct()
                    pos.frame = target_frame
                    self.jack_client.transport_reposition_struct(pos)
                    self._repositioning_pending = True
                    self.last_beat = self._cached_loop_start
                    return

        # 2. End of Song Handling (RT Safe)
        # Only stop automatically if not recording and loop is off
        if not self._cached_loop_enabled and not self._cached_is_recording:
            song_length = self._cached_song_length
            if song_length > 0.1 and end_beat_of_block >= song_length:
                if start_beat_of_block < song_length:
                    if not self._repositioning_pending:
                        self._log_rt(f"Stopping at end of song: end_beat={end_beat_of_block:.4f}, start_beat={start_beat_of_block:.4f}, length={song_length:.4f}")
                        self.jack_client.transport_stop()
                elif start_beat_of_block >= song_length:
                    if not self._repositioning_pending:
                        self._log_rt(f"Already past end of song: start_beat={start_beat_of_block:.4f}, length={song_length:.4f}")
                        self.jack_client.transport_stop()

    def _process_callback(self, frames: int):
        self._in_process_callback = True
        self._current_block_frames = frames

        # --- Heartbeat Logging ---
        if not hasattr(self, '_cb_count'): self._cb_count = 0
        self._cb_count += 1
        if self._cb_count == 1:
            self._log_rt("First process callback triggered!")
        # Log every ~5 seconds (assuming ~48kHz and 1024 block size)
        if self._cb_count % 250 == 0:
            self._log_rt(f"Heartbeat #{self._cb_count//250} (block={self._cb_count}, frames={frames})")

        try:
            # --- 0. Calculate Current Beat & Timing ---
            samplerate = self.jack_client.samplerate
            tempo = self._cached_tempo
            beats_per_second = tempo / 60.0
            start_beat_of_block = self.last_beat

            state, pos_struct = self.jack_client.transport_query_struct()
            pos = jack.position2dict(pos_struct)
            current_frame = pos.get('frame', 0)

            if samplerate > 0 and beats_per_second > 0:
                authoritative_beat_now = (current_frame / samplerate) * beats_per_second
            else:
                authoritative_beat_now = self.last_beat

            end_beat_of_block = authoritative_beat_now + (frames / samplerate) * beats_per_second if samplerate > 0 else authoritative_beat_now

            # --- 0.5 Detect External Jumps ---
            if not self._repositioning_pending:
                beat_delta = abs(authoritative_beat_now - self.last_beat)
                # If jump > 1/4 beat (arbitrary threshold for "jump" vs "normal playback")
                if beat_delta > 0.25:
                    self._log_rt(f"External jump detected: {self.last_beat:.4f} -> {authoritative_beat_now:.4f}")
                    self._sync_playhead_to_beat(authoritative_beat_now)
                    self._pending_seek_beat = authoritative_beat_now

            # --- 1. Handle UI-queued MIDI events ---
            events_processed = 0
            while events_processed < 64:
                try:
                    target, msg = self._out_event_queue.popleft()
                    if target == -1: # Metronome
                        if self.metronome_port:
                            self._write_midi_safe(self.metronome_port, 0, bytes(msg.bytes()))
                    elif target in self.midi_out_ports:
                        self._write_midi_safe(self.midi_out_ports[target], 0, bytes(msg.bytes()))
                    events_processed += 1
                except IndexError:
                    break

            # --- 1.5 Handle Clavier Input Routing & Pass-through ---
            if self.clavier_port:
                incoming = self.clavier_port.incoming_midi_events()
                for offset, data in incoming:
                    try:
                        data_bytes = bytes(data)
                        if data_bytes[0] >= 0xF8: continue # Skip real-time sync

                        msg = mido.Message.from_bytes(data_bytes)
                    except Exception:
                        continue

                    # Accurate timing for this event
                    accurate_event_beat = start_beat_of_block + (offset / samplerate) * beats_per_second if samplerate > 0 else start_beat_of_block
                    current_routing_idx = self._get_input_routing_value(accurate_event_beat)

                    target_track = None
                    if current_routing_idx is not None and 0 <= current_routing_idx < len(self._cached_tracks):
                        target_track = self._cached_tracks[current_routing_idx]

                    # Port lookup (Fast path: use index directly)
                    port = None
                    if current_routing_idx is not None:
                        port = self._midi_out_ports_by_idx.get(current_routing_idx)

                    # Robust fallback: use object
                    if not port and target_track:
                        port = self.midi_out_ports.get(target_track)

                    if port:
                        # Channel Remapping (for non-system messages)
                        if msg.type not in ['sysex', 'reset'] and hasattr(msg, 'channel'):
                            msg.channel = self._cached_channels.get(current_routing_idx, 0)

                        if msg.type == 'note_on' and msg.velocity > 0:
                            self._write_midi_safe(port, offset, bytes(msg.bytes()))
                            self._live_forwarded_notes[msg.note] = (port, msg.channel, current_routing_idx)
                            if current_routing_idx is not None:
                                with self.sync_lock: self._live_activity[current_routing_idx].add(msg.note)

                        elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                            if msg.note in self._live_forwarded_notes:
                                f_port, f_channel, f_routing_idx = self._live_forwarded_notes.pop(msg.note)
                                msg.channel = f_channel
                                self._write_midi_safe(f_port, offset, bytes(msg.bytes()))
                                # Update UI activity using the ORIGINAL routing index
                                if f_routing_idx is not None:
                                    with self.sync_lock:
                                        if f_routing_idx in self._live_activity and msg.note in self._live_activity[f_routing_idx]:
                                            self._live_activity[f_routing_idx].remove(msg.note)
                            else:
                                self._write_midi_safe(port, offset, bytes(msg.bytes()))
                                if current_routing_idx is not None:
                                    with self.sync_lock:
                                        if msg.note in self._live_activity[current_routing_idx]:
                                            self._live_activity[current_routing_idx].remove(msg.note)
                        else:
                            # Forward CC, Pitch Bend, Program Change, etc.
                            self._write_midi_safe(port, offset, bytes(msg.bytes()))

                    # Also handle transport/control CCs
                    self._handle_control_midi(msg)

                    # Update diagnostics
                    self._diag_clavier_in += 1
                    if target_track:
                        self._diag_clavier_routed += 1
                        self._diag_last_target_idx = current_routing_idx

                    if self._cached_is_recording:
                        self._record_midi_event(msg, start_beat_of_block, offset)

            # --- 2. Handle Transport State Changes ---
            current_transport_state = self.jack_client.transport_state
            self._last_transport_state_rt = current_transport_state # Update for UI polling

            if current_transport_state != self.last_transport_state:
                # Move to background:
                is_paused = (current_transport_state != jack.ROLLING)
                with self.process_lock:
                    for ap in self.active_audio_processes:
                        self._queue_ipc_command(ap.socket_path, {"command": ["set_property", "pause", is_paused]})

                self.last_transport_state = current_transport_state

            with self.sync_lock:
                if self.metronome_port:
                    for note_off_msg, offset in self._metronome_notes_to_turn_off:
                        self._write_midi_safe(self.metronome_port, offset, bytes(note_off_msg.bytes()))
                    self._metronome_notes_to_turn_off.clear()

                if not self.jack_client or self.jack_client.transport_state != jack.ROLLING:
                    if self._active_notes:
                        for (track_idx, pitch), end_beat in list(self._active_notes.items()):
                            try:
                                track = self._cached_tracks[track_idx]
                                if track in self.midi_out_ports:
                                    self._write_midi_safe(self.midi_out_ports[track], 0, bytes(mido.Message('note_off', channel=track.channel, note=pitch, velocity=0).bytes()))
                            except IndexError: pass
                        self._active_notes.clear()
                    return

            for (track_idx, pitch), end_beat in list(self._active_notes.items()):
                if start_beat_of_block <= end_beat < end_beat_of_block:
                    try:
                        track = self._cached_tracks[track_idx]
                        if track in self.midi_out_ports:
                            offset = int((end_beat - start_beat_of_block) / (end_beat_of_block - start_beat_of_block) * self._current_block_frames) if end_beat_of_block > start_beat_of_block else 0
                            offset = max(0, min(self._current_block_frames - 1, offset))
                            self._write_midi_safe(self.midi_out_ports[track], offset, bytes(mido.Message('note_off', channel=track.channel, note=pitch, velocity=0).bytes()))
                    except IndexError: pass
                    del self._active_notes[(track_idx, pitch)]

            self._process_midi_events(start_beat_of_block, end_beat_of_block)
            self._process_automation_events(start_beat_of_block, end_beat_of_block)
            self._process_metronome(start_beat_of_block, end_beat_of_block)

            with self.process_lock:
                for ap in self.active_audio_processes:
                    if ap.track_index < len(self._cached_tracks):
                         track = self._cached_tracks[ap.track_index]
                    else:
                         continue
                    if is_audio_track(track):
                        end_beat = self._cached_audio_ends.get(ap.track_index, 0.0)
                        if end_beat_of_block >= end_beat > start_beat_of_block:
                            self._queue_ipc_command(ap.socket_path, {"command": ["set_property", "pause", True]})

            if not self._repositioning_pending:
                self._check_for_loop_and_play_range(start_beat_of_block, end_beat_of_block)

            if self._repositioning_pending:
                 # Wait for the engine to actually reach the target position
                 self._reposition_frames += frames

                 # Tolerance of 0.5 beats or 1 second timeout
                 reached = math.isclose(authoritative_beat_now, self._target_beat, abs_tol=0.5)
                 timeout = self._reposition_frames > (samplerate if samplerate > 0 else 48000)

                 if reached or timeout:
                      if timeout and not reached:
                          self._log_rt(f"Repositioning TIMEOUT: target={self._target_beat:.4f}, current={authoritative_beat_now:.4f}")
                      else:
                          self._log_rt(f"Repositioning confirmed: authoritative_beat={authoritative_beat_now:.4f}")

                      self.last_beat = authoritative_beat_now
                      self._repositioning_pending = False
                      self._reposition_frames = 0
                 else:
                      # Still waiting for jump to take effect
                      return # Skip logic until jump is visible

            if not self._repositioning_pending:
                 self.last_beat = end_beat_of_block
                 self._last_beat_rt = authoritative_beat_now # Use authoritative for RT sync
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._log_rt(f"CALLBACK CRASH:\n{tb}")
        finally:
            self._in_process_callback = False
