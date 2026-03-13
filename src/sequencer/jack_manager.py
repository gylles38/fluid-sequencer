from __future__ import annotations
import json
import math
import re
import mido
import jack
import numpy as np
import os
import subprocess
import sys
import tempfile
import socket
import threading
import time
import collections
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING, Dict

from kivy.clock import Clock

if TYPE_CHECKING:
    from .sequencer import Sequencer

from .models import (AudioTrack, MidiTrack, AutomationTrack, AutomationPoint,
                    is_midi_track, is_audio_track)

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
        self.open_ports = {}
        self.next_event_indices = []
        self._active_notes = {} # key: (track_idx, note_pitch), value: (end_beat, velocity)
        self._sustained_notes = set() # key: (track_idx, note_pitch)
        self._metronome_notes_to_turn_off = []
        self.active_audio_processes: List[ActiveAudioProcess] = []
        self.process_lock = threading.Lock()
        self.sync_lock = threading.Lock()
        self._display_thread = None
        self._display_stop_event = threading.Event()
        self.automation_events = []
        self.next_automation_event_index = 0
        self.event_to_ignore: Optional[dict] = None
        self._last_cc_values = {} # key: (track_idx, cc_num), value: val
        self._routing_track: Optional[AutomationTrack] = None        
        self._cached_first_midi_idx = None
        self._cached_armed_idx = None

        # --- UI Command Flags (from MIDI) ---
        self._pending_play_pause = False
        self._pending_stop = False
        self._pending_record = False
        self._pending_mappings = collections.deque() # (mapping_obj, value)
        self._recorded_events_to_merge = collections.deque()

        # --- RT-Safe IPC Handling ---
        self._ipc_queue = collections.deque()
        self._ipc_worker_thread = None
        self._ipc_worker_stop_event = threading.Event()

        # --- Dynamic Audio Correction ---
        self.CORRECTION_GAIN = 0.02
        self.CORRECTION_THRESHOLD = 0.03 # 30ms
        self._correction_thread = None
        self._correction_stop_event = threading.Event()
        self.last_applied_speeds = {}

        # --- Dynamic MIDI Routing (Conductor) ---
        self._routing_thread = None
        self._routing_stop_event = threading.Event()
        self._last_connected_src_id = None
        self._last_connected_dest_id = None
        self._last_routing_target_idx = -1
        self._manual_routing_override = -1
        self._routing_initialized = False
        
        # --- Diagnostics (RT Safe) ---
        #self._diag_clavier_in = 0
        #self._diag_clavier_routed = 0
        #self._diag_last_target_idx = -1
        self._last_beat_rt = 0.0 # Atomic float for UI sync
        self._last_transport_state_rt = jack.STOPPED        

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

    def open_midi_port(self, port_name: str, verbose=False):
        """Opens a MIDI port if it's not already open."""
        if port_name in self.open_ports and not self.open_ports[port_name].closed:
            return  # Port is already open

        vp = next((p for p in self.sequencer.virtual_ports if p.name == port_name), None)
        if vp:
            self.open_ports[port_name] = vp
        else:
            try:
                self.open_ports[port_name] = mido.open_output(port_name)
                if verbose: print(f"Successfully opened MIDI port '{port_name}'")
            except Exception as e:
                if verbose: print(f"Could not open MIDI port '{port_name}': {e}")

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
                state, pos_dict = self.get_safe_transport_pos()
                if pos_dict is None:
                    time.sleep(0.1)
                    continue
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

    def _find_jack_port(self, pattern: str, is_output: bool = False) -> Optional[jack.Port]:
        """Finds a JACK port matching the given pattern using native API."""
        if not pattern: return None
        if not self.jack_client: return None

        # Clean the pattern (remove ALSA indices)
        clean_pattern = re.sub(r'[:\s]\d+[:\d]*$', '', pattern)
        tokens = [t.lower() for t in re.split(r'[^a-zA-Z0-9]+', clean_pattern) if t]
        if not tokens: return None
        unique_tokens = set(tokens)

        # Query ports from JACK using keyword arguments
        if is_output:
            ports = self.jack_client.get_ports(is_midi=True, is_output=True)
        else:
            ports = self.jack_client.get_ports(is_midi=True, is_input=True)

        for port in ports:
            name_lower = port.name.lower()
            if all(token in name_lower for token in unique_tokens):
                return port
        return None

    def _routing_worker_loop(self):
        """
        Background loop managing the dynamic MIDI routing using native JACK API.
        """
        while not self._routing_stop_event.is_set():
            try:
                if not self.is_running or not self.jack_client:
                    time.sleep(1.0)
                    continue

                # 0. Initial "Clean Slate" - Disconnect everything before starting routing
                if not self._routing_initialized:
                    time.sleep(1.5)
                    print("[Conductor] Initializing routing: Disconnecting ALL project instrument links...")

                    src_pattern = self.sequencer.default_record_port or "MPK249 Port A"
                    src_port = self._find_jack_port(src_pattern, is_output=True)

                    if src_port:
                        # 1. Disconnect specific project targets
                        for track in self.sequencer.song.tracks:
                            if is_midi_track(track) and track.input_port_name:
                                dest_port = self._find_jack_port(track.input_port_name, is_output=False)
                                if dest_port:
                                    try: self.jack_client.disconnect(src_port, dest_port)
                                    except: pass

                        # 2. Aggressively disconnect ANY existing connections from this source
                        try:
                            connections = self.jack_client.get_all_connections(src_port)
                            for connection in connections:
                                try: self.jack_client.disconnect(src_port, connection)
                                except: pass
                        except Exception: pass

                    self.silence_all_midi_notes()
                    self._routing_initialized = True

                # 1. Determine target track index
                current_beat = self.last_beat
                target_idx = self._get_input_routing_value(current_beat)

                # 2. Identify source keyboard
                src_pattern = self.sequencer.default_record_port or "MPK249 Port A"

                # 3. Identify destination instrument
                dest_pattern = None
                if target_idx is not None and 0 <= target_idx < len(self.sequencer.song.tracks):
                    track = self.sequencer.song.tracks[target_idx]
                    if is_midi_track(track):
                        dest_pattern = getattr(track, 'input_port_name', None)

                # 4. Manage connections if target changed
                if dest_pattern:
                    src_port = self._find_jack_port(src_pattern, is_output=True)
                    dest_port = self._find_jack_port(dest_pattern, is_output=False)

                    if src_port and dest_port:
                        # Detection of routing change
                        if src_port.name != self._last_connected_src_id or dest_port.name != self._last_connected_dest_id or target_idx != self._last_routing_target_idx:
                            print(f"[Conductor] Routing change detected: {self._last_routing_target_idx} -> {target_idx}")

                            # --- 1. DISCONNECT ---
                            try:
                                connections = self.jack_client.get_all_connections(src_port)
                                for connection in connections:
                                    try: self.jack_client.disconnect(src_port, connection)
                                    except: pass
                            except Exception: pass

                            # --- 2. WAIT A BIT ---
                            time.sleep(0.02)

                            # --- 3. NUCLEAR SILENCE ---
                            if self._last_routing_target_idx != -1:
                                self._silence_instrument_at_index(self._last_routing_target_idx)
                            if target_idx != self._last_routing_target_idx:
                                self._silence_instrument_at_index(target_idx)

                            # --- 4. SETTLING DELAY ---
                            time.sleep(0.08)

                            # Connect new
                            print(f"[Conductor] New Route: {src_port.name} -> {dest_port.name}")
                            try: self.jack_client.connect(src_port, dest_port)
                            except jack.JackError: pass # Already connected

                            self._last_connected_src_id = src_port.name
                            self._last_connected_dest_id = dest_port.name
                            self._last_routing_target_idx = target_idx
                    else:
                        # Target defined but port not found (instrument closed)
                        if self._last_connected_src_id:
                            if self._last_routing_target_idx != -1:
                                self._silence_instrument_at_index(self._last_routing_target_idx)

                            # Cleanup all from source
                            src_port_to_clean = self._find_jack_port(src_pattern, is_output=True)
                            if src_port_to_clean:
                                try:
                                    connections = self.jack_client.get_all_connections(src_port_to_clean)
                                    for conn in connections:
                                        try: self.jack_client.disconnect(src_port_to_clean, conn)
                                        except: pass
                                except Exception: pass

                            self._last_connected_src_id = None
                            self._last_connected_dest_id = None
                            self._last_routing_target_idx = -1
                else:
                    # No target or not a MIDI track
                    if self._last_connected_src_id:
                         if self._last_routing_target_idx != -1:
                             self._silence_instrument_at_index(self._last_routing_target_idx)
                             time.sleep(0.1)

                         src_port_to_clean = self._find_jack_port(src_pattern, is_output=True)
                         if src_port_to_clean:
                             try:
                                 connections = self.jack_client.get_all_connections(src_port_to_clean)
                                 for conn in connections:
                                     try: self.jack_client.disconnect(src_port_to_clean, conn)
                                     except: pass
                             except Exception: pass

                         self._last_connected_src_id = None
                         self._last_connected_dest_id = None
                         self._last_routing_target_idx = -1

            except Exception as e:
                print(f"[Conductor] Error in routing loop: {e}", file=sys.stderr)

            time.sleep(0.1)

    def _prepare_automation_events(self):
        """Generates and sorts all automation events for the song."""
        self.automation_events.clear()
        
        # Link the global routing track from the song
        self._routing_track = self.sequencer.song.input_routing

        # Update cached indices for fallback routing
        self._cached_first_midi_idx = None
        self._cached_armed_idx = None
        tracks = self.sequencer.song.tracks
        for i, t in enumerate(tracks):
            if is_midi_track(t):
                if self._cached_first_midi_idx is None:
                    self._cached_first_midi_idx = i
                if getattr(t, 'record_mode', 'OFF') != 'OFF':
                    self._cached_armed_idx = i

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
                # Ensure routing track exists
                self.sequencer.get_input_routing_track()                
                
                tracks = self.sequencer.song.tracks

                # --- MIDI Port Setup ---
                self.open_ports.clear()
                required_ports = set()
                for track in tracks:
                    if is_midi_track(track):
                        # Port used by the sequencer for playback
                        if track.output_port_name:
                            required_ports.add(track.output_port_name)
                        # Port used as destination for live routing (instrument)
                        if track.input_port_name:
                            required_ports.add(track.input_port_name)

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
                state, pos_dict = self.get_safe_transport_pos()
                if pos_dict:
                    self.sequencer.song.tempo = pos_dict.get('beats_per_minute', self.sequencer.song.tempo)
                    frame = pos_dict.get('frame', 0)
                else:
                    frame = 0
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

                # --- Start the IPC worker thread ---
                self._ipc_worker_stop_event.clear()
                self._ipc_worker_thread = threading.Thread(target=self._ipc_worker_loop)
                self._ipc_worker_thread.daemon = True
                self._ipc_worker_thread.start()

                # --- Start the routing thread (Conductor) ---
                self._routing_stop_event.clear()
                self._routing_thread = threading.Thread(target=self._routing_worker_loop)
                self._routing_thread.daemon = True
                self._routing_thread.start()

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

        # Stop the IPC worker thread
        if self._ipc_worker_thread and self._ipc_worker_thread.is_alive():
            self._ipc_worker_stop_event.set()
            self._ipc_worker_thread.join(timeout=1.0)
            self._ipc_worker_thread = None

        # Stop the routing thread
        if self._routing_thread and self._routing_thread.is_alive():
            self._routing_stop_event.set()
            self._routing_thread.join(timeout=1.0)
            self._routing_thread = None

        # Cleanup physical keyboard connections
        try:
            src_pattern = self.sequencer.default_record_port or "MPK249 Port A"
            src_port = self._find_jack_port(src_pattern, is_output=True)
            if src_port:
                connections = self.jack_client.get_all_connections(src_port)
                for conn in connections:
                    try: self.jack_client.disconnect(src_port, conn)
                    except: pass
        except: pass

        self._last_connected_src_id = None
        self._last_connected_dest_id = None

        # Shut down all external audio player processes FIRST.
        # They depend on JACK, so they should be closed while the server is still reachable.
        self._shutdown_audio_processes()
        print("Audio processes terminated.")

        # Deactivate and close the JACK client
        if self.jack_client:
            try:
                # Deactivate first to stop the process callback
                self.jack_client.deactivate()
                # Then close the client connection
                self.jack_client.close()
                print("JACK client deactivated and closed.")
            except jack.JackError as e:
                print(f"Error during JACK client shutdown: {e}", file=sys.stderr)
            finally:
                self.jack_client = None

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
        self._routing_initialized = False
        self._last_connected_src_id = None
        self._last_connected_dest_id = None
        self._last_routing_target_idx = -1

    def get_current_beat(self) -> float:
        """Retourne la position actuelle du transport en beats."""
        return self.last_beat

    def get_safe_transport_pos(self):
        """
        Retrieves the transport state and position dictionary from JACK.
        Handles AssertionError caused by race conditions during transport polling
        by retrying the query.
        Returns: (state_code, pos_dict) or (None, None) on persistent failure.
        """
        if not self.jack_client:
            return None, None

        for _ in range(3):
            try:
                state, pos_struct = self.jack_client.transport_query_struct()
                pos_dict = jack.position2dict(pos_struct)
                return state, pos_dict
            except (AssertionError, Exception):
                # Race condition: position updated while reading. Retry.
                continue
        return None, None

    def silence_all_midi_notes(self):
        """
        Hard reset for all MIDI sound across all ports and channels.
        Used for Panic, stop, and project transitions.
        """
        # 1. First, snapshot and clear internal tracking under lock
        with self.sync_lock:
            active_notes_snapshot = list(self._active_notes.items())
            self._active_notes.clear()
            self._sustained_notes.clear()

        # 2. Identify all MIDI ports opened by Sequencer/JackManager
        unique_ports = set()
        for track in self.sequencer.song.tracks:
            if is_midi_track(track):
                if track.output_port_name in self.open_ports: unique_ports.add(self.open_ports[track.output_port_name])
                if track.input_port_name in self.open_ports: unique_ports.add(self.open_ports[track.input_port_name])

        m_port_name = self.sequencer.song.metronome_port_name
        if m_port_name in self.open_ports: unique_ports.add(self.open_ports[m_port_name])

        # 3. Perform robust silencing pass
        for port in unique_ports:
            if not port or getattr(port, 'closed', False): continue

            # --- Pass 1: CC Resets (All 16 Channels) ---
            # Broad and fast. CC 64 (Sustain Off) MUST be FIRST.
            for ch in range(16):
                port.send(mido.Message('control_change', channel=ch, control=64, value=0))  # Sustain Off
                port.send(mido.Message('control_change', channel=ch, control=123, value=0)) # All Notes Off
                port.send(mido.Message('control_change', channel=ch, control=120, value=0)) # All Sound Off
                port.send(mido.Message('control_change', channel=ch, control=121, value=0)) # Reset Controllers

            # --- Pass 2: Targeted Note Off sweep (Relevant Channels) ---
            # Perform 128-note sweep only on most common channels to avoid MIDI buffer overflow
            # and latency while ensuring keyboard-held notes are cut.
            targeted_channels = {0, 1, 9}
            for track in self.sequencer.song.tracks:
                if is_midi_track(track): targeted_channels.add(track.channel)

            for ch in targeted_channels:
                for pitch in range(128):
                    port.send(mido.Message('note_off', channel=ch, note=pitch, velocity=0))

        # 4. Specifically cut notes from the snapshot to be absolutely sure
        for (track_idx, pitch), (end_beat, velocity) in active_notes_snapshot:
            try:
                track = self.sequencer.song.tracks[track_idx]
                if is_midi_track(track) and track.output_port_name in self.open_ports:
                    port = self.open_ports[track.output_port_name]
                    port.send(mido.Message('note_off', channel=track.channel, note=pitch, velocity=0))
            except: pass

    def _queue_ipc_command(self, socket_path, command_data):
        """Queues an IPC command for the worker thread to send (RT safe)."""
        self._ipc_queue.append((socket_path, command_data))

    def _ipc_worker_loop(self):
        """Worker thread that sends queued IPC commands."""
        while not self._ipc_worker_stop_event.is_set():
            try:
                if self._ipc_queue:
                    socket_path, command_data = self._ipc_queue.popleft()
                    self._send_ipc_command(socket_path, command_data)
                else:
                    time.sleep(0.01)
            except IndexError:
                time.sleep(0.01)
            except Exception as e:
                print(f"Error in IPC worker loop: {e}", file=sys.stderr)

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

        # If playback is active, seek the new track to the current position.
        # seek_audio_to_beat will handle unpausing if the track is within its active range.
        if self.jack_client and self.jack_client.transport_state == jack.ROLLING:
            current_beat = self.get_current_beat()
            self.seek_audio_to_beat(current_beat)
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
                    duration_beats = self.sequencer._get_audio_duration_in_beats(track)
                    end_beat = track.start_time + duration_beats

                    is_rolling = (self.jack_client and self.jack_client.transport_state == jack.ROLLING)

                    if beat_pos >= end_beat:
                        # Past end: Pause and seek to almost-end
                        self._send_ipc_command(ap.socket_path, {"command": ["set_property", "pause", True]})
                        mpv_time = (duration_beats - 0.01) / beats_per_second
                        if mpv_time < 0: mpv_time = 0.0
                    elif beat_pos < track.start_time:
                        # Before start: Pause and seek to 0
                        self._send_ipc_command(ap.socket_path, {"command": ["set_property", "pause", True]})
                        mpv_time = 0.0
                    else:
                        # Within track
                        mpv_time = (beat_pos - track.start_time) / beats_per_second
                        if is_rolling:
                            self._send_ipc_command(ap.socket_path, {"command": ["set_property", "pause", False]})

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
        num_tracks = len(tracks)
        self.next_event_indices = [0] * num_tracks
        self._active_notes.clear()
        self._sustained_notes.clear()

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

    def _time_callback(self, state, blocksize, pos, new_pos):
        if new_pos:
            pos_dict = jack.position2dict(pos)
            self.sequencer.song.tempo = pos_dict.get('beats_per_minute', self.sequencer.song.tempo)
            frame = pos_dict.get('frame', 0)
            samplerate = self.jack_client.samplerate
            beats_per_second = self.sequencer.song.tempo / 60.0
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

            # Suppression for OVERWRITE mode during recording:
            # we skip playing existing sequencer notes for any track armed for overwrite.
            if self.sequencer.is_recording and getattr(track, 'record_mode', 'OFF') == 'OVERWRITE':
                should_be_audible = False

            port = self.open_ports[track.output_port_name]

            if i >= len(self.next_event_indices):
                self.next_event_indices.extend([0] * (i - len(self.next_event_indices) + 1))

            while self.next_event_indices[i] < len(track.events):
                event = track.events[self.next_event_indices[i]]

                if start_beat_of_block <= event.start_time < end_beat_of_block:
                    if should_be_audible:
                        for note in event.notes:
                            final_velocity = int(note.velocity * track.velocity)
                            final_velocity = max(0, min(127, final_velocity))
                            note_on_msg = mido.Message('note_on', channel=track.channel, note=note.pitch, velocity=final_velocity)
                            port.send(note_on_msg)
                            note_end_beat = event.start_time + note.duration
                            self._active_notes[(i, note.pitch)] = (note_end_beat, final_velocity)
                        for cc in event.cc_messages:
                            cc_msg = mido.Message('control_change', channel=track.channel, control=cc.control, value=cc.value)
                            port.send(cc_msg)
                            self._last_cc_values[(i, cc.control)] = cc.value

                            # If sustain pedal is released, clear sustained notes for this track
                            if cc.control == 64 and cc.value < 64:
                                self._sustained_notes = {p for p in self._sustained_notes if p[0] != i}
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
                        midi_value = int((value + 1.0) / 2.0 * 127)
                    else:
                        midi_value = int(value)

                    # --- FIX: Clamp the final value to the valid MIDI range ---
                    midi_value = max(0, min(127, midi_value))

                    msg = mido.Message('control_change', channel=target_track.channel, control=param_config['control'], value=midi_value)
                    port.send(msg)
                    self._last_cc_values[(target_track_index, param_config['control'])] = midi_value
            elif param_config.get('type') == 'program_change':
                if target_track.output_port_name in self.open_ports:
                    port = self.open_ports[target_track.output_port_name]
                    program_value = max(0, min(127, int(value)))
                    msg = mido.Message('program_change', channel=target_track.channel, program=program_value)
                    port.send(msg)
            elif param_config.get('type') == 'velocity_multiplier':
                target_track.velocity = float(value)

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
            # We use a simple try/except here for RT-safety (avoid retry loop)
            try:
                state, pos_struct = self.jack_client.transport_query_struct()
                pos = jack.position2dict(pos_struct)
            except (AssertionError, Exception):
                # Fallback to last known frame + frames per second
                pos = {'frame': int(self.last_beat * (self.jack_client.samplerate / (self.sequencer.song.tempo / 60.0)))}

            samplerate = self.jack_client.samplerate
            tempo = self.sequencer.song.tempo
            beats_per_second = tempo / 60.0

            current_frame = pos.get('frame', 0)
            if samplerate > 0 and beats_per_second > 0:
                authoritative_beat_now = (current_frame / samplerate) * beats_per_second
            else:
                authoritative_beat_now = self.last_beat

            current_transport_state = self.jack_client.transport_state
            if current_transport_state != self.last_transport_state:
                if current_transport_state == jack.ROLLING:
                    # Reset manual routing override when playback starts
                    self._manual_routing_override = -1

                    # Selective unpause: only tracks that should be playing now
                    # NOTE: We access active_audio_processes without lock for RT safety.
                    # It's only modified in main thread during track add/start/stop.
                    for ap in self.active_audio_processes:
                        track = self.sequencer.song.tracks[ap.track_index]
                        if is_audio_track(track):
                            duration = track.duration_beats or 0.0
                            if track.start_time <= authoritative_beat_now < (track.start_time + duration):
                                self._queue_ipc_command(ap.socket_path, {"command": ["set_property", "pause", False]})
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
                        for (track_idx, pitch), (end_beat, velocity) in list(self._active_notes.items()):
                            track = self.sequencer.song.tracks[track_idx]
                            if is_midi_track(track) and track.output_port_name in self.open_ports:
                                port = self.open_ports[track.output_port_name]
                                # Cut the note
                                port.send(mido.Message('note_off', channel=track.channel, note=pitch, velocity=0))
                                # Sustain and All notes off to be sure
                                port.send(mido.Message('control_change', channel=track.channel, control=64, value=0))
                                port.send(mido.Message('control_change', channel=track.channel, control=123, value=0))
                        self._active_notes.clear()
                    self._sustained_notes.clear()
                    return

                start_beat_of_block = self.last_beat
                end_beat_of_block = authoritative_beat_now + (frames / samplerate) * beats_per_second

                for (track_idx, pitch), (end_beat, velocity) in list(self._active_notes.items()):
                    if start_beat_of_block <= end_beat < end_beat_of_block:
                        track = self.sequencer.song.tracks[track_idx]
                        if is_midi_track(track) and track.output_port_name in self.open_ports:
                            port = self.open_ports[track.output_port_name]
                            port.send(mido.Message('note_off', channel=track.channel, note=pitch, velocity=0))

                            # If sustain pedal is ON, move the note to sustained_notes
                            if self._last_cc_values.get((track_idx, 64), 0) >= 64:
                                self._sustained_notes.add((track_idx, pitch))

                        del self._active_notes[(track_idx, pitch)]

                self._process_midi_events(start_beat_of_block, end_beat_of_block)
                self._process_automation_events(start_beat_of_block, end_beat_of_block)
                self._process_metronome(start_beat_of_block, end_beat_of_block)

            # Update audio track pause states based on boundaries (RT safe)
            for ap in self.active_audio_processes:
                track = self.sequencer.song.tracks[ap.track_index]
                if is_audio_track(track):
                    duration_beats = track.duration_beats or 0.0
                    end_beat = track.start_time + duration_beats

                    # Stop at end of track
                    if end_beat_of_block >= end_beat and start_beat_of_block < end_beat:
                        self._queue_ipc_command(ap.socket_path, {"command": ["set_property", "pause", True]})

                    # Start at beginning of track
                    if start_beat_of_block <= track.start_time < end_beat_of_block:
                        self._queue_ipc_command(ap.socket_path, {"command": ["set_property", "pause", False]})

            self._check_for_loop_and_play_range(start_beat_of_block, end_beat_of_block)

            self.last_beat = end_beat_of_block
            #if self.sequencer.gui_mode:
            #    self.sequencer.current_beat = start_beat_of_block
            #    self.sequencer.last_beat_update_time = time.perf_counter()
        except Exception as e:
            print(f"\nError in JACK process callback: {e}")



    def _get_input_routing_value(self, beat: float) -> Optional[int]:
        """
        Returns the target track index for MIDI input routing at the given beat.
        Prioritization:
        0. Manual override (only if transport is stopped).
        1. Automation points on the routing track.
        2. Armed track index (cached).
        3. Final Fallback: First MIDI track (cached).
        """
        # 0. Manual Override Priority (Stopped state only)
        if self._manual_routing_override != -1 and self.sequencer.playback_state == "stopped":
            return self._manual_routing_override

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
        fallback_idx = getattr(self, '_cached_first_midi_idx', None)
        return fallback_idx

    def _silence_instrument_at_index(self, track_idx: int):
        """
        Surgically silences an instrument by sending Note Offs and CC resets.
        Ensures silence is sent to both the sequencer's output and the instrument directly.
        Optimized to avoid MIDI buffer overflows while ensuring keyboard-held notes are cut.
        """
        if 0 <= track_idx < len(self.sequencer.song.tracks):
            track = self.sequencer.song.tracks[track_idx]
            if not is_midi_track(track): return

            # 1. Identify all ports that could reach this instrument
            unique_ports = set()
            out_port_name = track.output_port_name

            # Fallback for out_port if not assigned to this specific track
            if not out_port_name or out_port_name not in self.open_ports:
                out_port_name = next((n for n in self.open_ports), None)

            if out_port_name: unique_ports.add(self.open_ports[out_port_name])

            # Instrument port itself (routing destination)
            if track.input_port_name in self.open_ports: unique_ports.add(self.open_ports[track.input_port_name])

            # 2. Force a connection to deliver silence if needed
            if track.input_port_name and track.output_port_name:
                dest_jack = self._find_jack_port(track.input_port_name, is_output=False)
                src_jack = self._find_jack_port(track.output_port_name, is_output=True)
                if dest_jack and src_jack:
                    try: self.jack_client.connect(src_jack, dest_jack)
                    except jack.JackError: pass # already connected

            # 3. Deliver robust silence across all relevant ports
            for port in unique_ports:
                if not port or getattr(port, 'closed', False): continue

                # CC Resets (All 16 channels). Sustain Off MUST be FIRST.
                for ch in range(16):
                    port.send(mido.Message('control_change', channel=ch, control=64, value=0))  # Sustain Off
                    port.send(mido.Message('control_change', channel=ch, control=123, value=0)) # All Notes Off
                    port.send(mido.Message('control_change', channel=ch, control=120, value=0)) # All Sound Off
                    port.send(mido.Message('control_change', channel=ch, control=121, value=0)) # Reset Controllers

                # Targeted individual Note Off sweep for relevant channels
                # Covers common keyboard outputs and track-specific channel.
                for ch in {0, 1, 9, track.channel}:
                    for pitch in range(128):
                        port.send(mido.Message('note_off', channel=ch, note=pitch, velocity=0))

            # 4. Cleanup internal state tracking under lock
            with self.sync_lock:
                self._sustained_notes = {p for p in self._sustained_notes if p[0] != track_idx}
                active_notes_copy = list(self._active_notes.items())

            # 5. Restore Sequencer Playback and Controllers for OTHER tracks sharing this output port
            restored_count = 0
            if out_port_name and out_port_name in self.open_ports:
                out_port = self.open_ports[out_port_name]

                # Restore Notes
                for (t_idx, pitch), (end_beat, velocity) in active_notes_copy:
                    if t_idx == track_idx: continue # Skip the track we just silenced

                    t = self.sequencer.song.tracks[t_idx]
                    if is_midi_track(t) and t.output_port_name == out_port_name:
                        out_port.send(mido.Message('note_on', channel=t.channel, note=pitch, velocity=velocity))
                        restored_count += 1

                # Restore Controllers (Vol, Pan, Sustain) for OTHER tracks
                current_beat = self.last_beat
                for i, t in enumerate(self.sequencer.song.tracks):
                    if i == track_idx: continue
                    if is_midi_track(t) and t.output_port_name == out_port_name:
                        primed_params = self._prime_automation_at_beat_for_track(i, current_beat)
                        if (i, 'vol') not in primed_params:
                            out_port.send(mido.Message('control_change', channel=t.channel, control=7, value=int(t.volume * 127)))
                        if (i, 'pan') not in primed_params:
                            out_port.send(mido.Message('control_change', channel=t.channel, control=10, value=int((t.pan + 1.0) / 2.0 * 127)))
                        if (i, 'cc64') not in primed_params:
                             with self.sync_lock:
                                 last_sustain = self._last_cc_values.get((i, 64))
                                 if last_sustain is not None:
                                     out_port.send(mido.Message('control_change', channel=t.channel, control=64, value=last_sustain))

            print(f"[Conductor] Silenced instrument via {out_port_name} (restored {restored_count} sequencer notes)")

    def _prime_automation_at_beat_for_track(self, track_index: int, beat: float) -> set:
        """
        Calculates and applies automation values for a specific track at a given beat
        by reusing the logic in AutomationTrack.
        Returns a set of (track_index, parameter_name) tuples that were primed.
        """
        primed_params = set()
        param_map = {
            "vol": {"type": "midi_cc", "control": 7},
            "pan": {"type": "midi_cc", "control": 10},
            "vel": {"type": "velocity_multiplier"},
            "prog": {"type": "program_change"},
            **{f"cc{i}": {"type": "midi_cc", "control": i} for i in range(128)}
        }

        for track in self.sequencer.song.tracks:
            if isinstance(track, AutomationTrack) and track.target_track_index == track_index:
                # Identify all unique parameters automated on this track
                parameters = {p.parameter for p in track.points}
                for param in parameters:
                    value = track.get_value_at(beat, param)
                    param_config = param_map.get(param.lower())
                    if param_config:
                        event_dict = {
                            "target_track_index": track_index,
                            "parameter": param,
                            "param_config": param_config,
                            "value": value
                        }
                        self._apply_automation_event(event_dict)
                        primed_params.add((track_index, param))

        return primed_params
    