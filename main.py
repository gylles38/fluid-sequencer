import mido
import jack
from sequencer.sequencer import Sequencer
import sys
import time
import os

# Platform-specific getch
try:
    # Windows
    import msvcrt
    def get_char():
        ch_b = msvcrt.getch()
        if ch_b in (b'\x00', b'\xe0'):  # Special key
            next_ch_b = msvcrt.getch()
            if ch_b == b'\xe0':
                if next_ch_b == b'K': return 'ARROW_LEFT'
                if next_ch_b == b'M': return 'ARROW_RIGHT'
            return '' # Ignore other special keys for now
        return ch_b.decode('utf-8', 'ignore')
except ImportError:
    # POSIX (Linux, macOS)
    import tty, termios
    def get_char():
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        ch = ''
        try:
            tty.setraw(sys.stdin.fileno())
            ch = sys.stdin.read(1)
            if ch == '\x1b':
                # This is a bit of a hack. We assume that if we get an escape character,
                # it's an arrow key sequence and we read the next two characters.
                # This will block if the user just presses Esc.
                # A more robust solution would use select() for non-blocking reads.
                next1 = sys.stdin.read(1)
                next2 = sys.stdin.read(1)
                if next1 == '[':
                    if next2 == 'D': return 'ARROW_LEFT'
                    if next2 == 'C': return 'ARROW_RIGHT'
                # If it's not a recognized arrow key, we effectively ignore the sequence.
                return ''
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch

def print_help():
    """Prints the help message with available commands."""
    help_text = """
Sequencer CLI Commands:
  help                    - Shows this help message.
  add <name> [prog]       - Adds a new MIDI track. `prog` is an optional program number (1-128).
  addaudio <name> <path>  - Adds a new Audio track with the audio file at <path>.
  addauto <name> <target_idx> - Adds an automation track targeting another track.
  addap <track> <pos> <p> <val> [curve] - Adds an automation point. Curves: none, linear, ease-in, ease-out, ease-in-out, sine.
  addcc <track> <pos> <cc> <val> - Adds a CC event to a track at a 'measure:beat' position.
  load <filepath>         - Loads a song from a MIDI file.
  loadproject <basename>  - Loads a full project (MIDI, vports, assignments).
  newproject <name>       - Creates a new, empty project.
  list                    - Shows all tracks in the current song.
  ports                   - Lists available MIDI input and output ports.
  vport <name>            - Creates a virtual MIDI output port.
  delvport                - Deletes an existing virtual port.
  assign <track_index>    - Assigns a track to an output port from a list of choices.
  assignmetro             - Assigns an output port for the metronome click.
  unassign <track_index>  - Un-assigns a track from its output port.
  setaudiocmd <cmd...>    - Sets the command for the external audio player (e.g., mpv --audio-device=jack).
  setbank <track> <msb> [lsb] - Sets the MIDI bank for a track (MSB=CC0, LSB=CC32).
  setch <track> <ch>      - Sets the MIDI channel (1-16) for a track.
  setprog <track> <prog>  - Sets the MIDI program (1-128) for a track.
  volume <track_index>    - Sets the volume for an audio or MIDI track (0.0 to 1.0).
  pan <track_index>       - Sets the pan for an audio or MIDI track (-1.0 to 1.0).
  velocity <track_index>  - Sets the velocity multiplier for a MIDI track (e.g., 1.0).
  mute <track_index>      - Toggles mute for a track.
  solo <track_index>      - Toggles solo for a track.
  rename <index> <new_name> - Renames a track.
  copy                    - Copies a section of a track using 'measure:beat' positions.
  move <track_index>      - Moves a section of a track using 'measure:beat' positions.
  transpose               - Transposes a section of a track using 'measure:beat' positions.
  record <track_index>    - Records MIDI to a track, with 'measure:beat' precision.
  bis                     - Re-records with the last used 'record' settings.
  delete <track_index>    - Deletes a track after confirmation.
  erase <track_index>     - Erases notes from a track using 'measure:beat' positions.
  tempo <bpm>             - Sets the song tempo in beats per minute.
  timesig <num> <den>     - Sets the song time signature (e.g., 4 4).
  save <filepath>         - Saves only the song to a MIDI file.
  saveproject <basename>  - Saves the full project (MIDI, vports, assignments).
  prime                   - Sends current program/bank state to all assigned ports.
  cc                      - Sends a single MIDI CC message to a port.
  play [pos]              - Seeks to 'measure:beat' position and plays, or just plays.
  pause                   - Toggles play/pause on the JACK transport (spacebar shortcut).
  loop [start] [end]      - Sets a playback loop ('measure:beat') or toggles if no args.
  stop                    - Stops the sequencer and disconnects from JACK.
  metronome <on|off>      - Enables or disables the metronome.
  quit                    - Exits the sequencer.

MIDI Mapping:
  setcontrolport <port>   - Sets the MIDI input port for control messages.
  unsetcontrolport        - Unsets the MIDI control port.
  map <chan> <cc> <track> <action> - Maps a MIDI CC to an action (volume, pan, program).
  unmap <chan> <cc>       - Removes a MIDI CC mapping.
  listmaps                - Lists all active MIDI CC mappings.
"""
    print(help_text)

def process_command(user_input, seq):
    if not user_input:
        return True # Continue loop

    parts = user_input.split()
    command = parts[0].lower()
    args = parts[1:]

    if command == "quit":
        if seq.is_dirty:
            while True:
                choice = input("You have unsaved changes. (S)ave, (D)iscard, or (C)ancel? ").lower()
                if choice == 'c':
                    print("Quit cancelled.")
                    return True # Continue main loop
                elif choice == 'd':
                    break # Proceed to quit
                elif choice == 's':
                    basename_to_save = seq.last_project_basename
                    if basename_to_save:
                        overwrite = input(f"Save over '{basename_to_save}.proj.json'? [Y/n] ").lower()
                        if overwrite == 'n':
                            basename_to_save = input("Enter new project basename: ").strip()
                    else:
                        basename_to_save = input("Enter project basename to save: ").strip()

                    if basename_to_save:
                        seq.save_project(basename_to_save)
                        break # Proceed to quit
                    else:
                        print("Save cancelled. Please provide a name.")
                        # Loop again
                else:
                    print("Invalid choice.")

        if seq.playback_state != "stopped":
            print("Stopping playback before exiting...")
            seq.stop()
        return False # End loop
    elif command == "help":
        print_help()
    elif command == "add":
        if len(args) == 1:
            seq.add_track(name=args[0], track_type='midi')
        elif len(args) == 2:
            prog = int(args[1])
            if not 1 <= prog <= 128:
                print("Error: Program number must be between 1 and 128.")
            else:
                seq.add_track(name=args[0], track_type='midi', instrument=prog - 1)
        else:
            print("Usage: add <name> [program_number]")
    elif command == "addaudio":
        if len(args) == 2:
            seq.add_track(name=args[0], track_type='audio', filepath=args[1])
        else:
            print("Usage: addaudio <name> <filepath>")
    elif command == "addauto":
        if len(args) == 2:
            try:
                name = args[0]
                target_index = int(args[1])
                seq.add_automation_track(name, target_index)
            except ValueError:
                print("Error: Invalid target track index.")
        else:
            print("Usage: addauto <name> <target_track_index>")
    elif command == "addap":
        if len(args) >= 4:
            try:
                track_index = int(args[0])
                position_str = args[1]
                param = args[2]
                value = float(args[3])
                curve = args[4] if len(args) > 4 else "none"
                seq.add_automation_point(track_index, position_str, param, value, curve)
            except ValueError:
                print("Error: Invalid number for track index or value.")
            except Exception as e:
                print(f"Error: {e}")
        else:
            print("Usage: addap <track_index> <position> <param> <value> [curve]")
    elif command == "addcc":
        if len(args) == 4:
            try:
                track_index = int(args[0])
                position_str = args[1]
                control = int(args[2])
                value = int(args[3])
                seq.add_cc_event(track_index, position_str, control, value)
            except ValueError:
                print("Error: Invalid number for track index, CC, or value.")
        else:
            print("Usage: addcc <track_index> <position> <cc_number> <value>")
    elif command == "load":
        if len(args) == 1:
            confirm = input("Loading a new song will discard the current session. Are you sure? [y/N] ").lower()
            if confirm == 'y':
                seq.load_song(filepath=args[0])
            else:
                print("Load cancelled.")
        else:
            print("Usage: load <filepath>")
    elif command == "loadproject":
        if len(args) == 1:
            confirm = input("Loading a new project will discard the current session. Are you sure? [y/N] ").lower()
            if confirm == 'y':
                seq.load_project(basename=args[0])
            else:
                print("Load cancelled.")
        else:
            print("Usage: loadproject <basename>")
    elif command == "newproject":
        if len(args) == 1:
            project_name = args[0]
            project_filepath = f"{project_name}.proj.json"
            if os.path.exists(project_filepath):
                print(f"Error: Project '{project_name}' already exists.")
                return True

            confirm = input("Creating a new project will discard the current session. Are you sure? [y/N] ").lower()
            if confirm == 'y':
                seq.new_project()
                seq.last_project_basename = project_name
                seq.is_dirty = True
            else:
                print("New project cancelled.")
        else:
            print("Usage: newproject <name>")
    elif command == "list":
        print(seq.list_tracks())
    elif command == "vport":
        if len(args) == 1:
            seq.create_virtual_port(name=args[0])
        else:
            print("Usage: vport <port_name>")
    elif command == "delvport":
        if not seq.virtual_ports:
            print("No virtual ports to delete.")
            return True

        print("Available virtual ports:")
        for i, vp in enumerate(seq.virtual_ports):
            print(f"  [{i}] {vp.name}")

        try:
            idx = int(input("Choose a virtual port to delete: "))
            if 0 <= idx < len(seq.virtual_ports):
                seq.delete_virtual_port(seq.virtual_ports[idx].name)
            else:
                print("Error: Invalid index.")
        except (ValueError, IndexError):
            print("Error: Invalid input.")

    elif command == "ports":
        print(seq.list_ports())
    elif command == "assign":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                if not 0 <= track_index < len(seq.song.tracks):
                    print("Error: Invalid track index.")
                    return True

                hardware_ports = mido.get_output_names() # type: ignore
                virtual_port_names = [vp.name for vp in seq.virtual_ports]
                all_outputs = hardware_ports + virtual_port_names

                if not all_outputs:
                    print("No output ports available.")
                    return True

                print("Available output ports:")
                for i, name in enumerate(all_outputs):
                    print(f"  [{i}] {name}")

                port_index = int(input("Choose a port to assign: "))
                if 0 <= port_index < len(all_outputs):
                    port_name = all_outputs[port_index]
                    seq.assign_port(track_index, port_name)
                else:
                    print("Error: Invalid port index.")

            except (ValueError, IndexError):
                print("Error: Invalid input.")
        else:
            print("Usage: assign <track_index>")
    elif command == "assignmetro":
        hardware_ports = mido.get_output_names() # type: ignore
        virtual_port_names = [vp.name for vp in seq.virtual_ports]
        all_outputs = hardware_ports + virtual_port_names

        if not all_outputs:
            print("No output ports available.")
            return True

        print("Available output ports:")
        for i, name in enumerate(all_outputs):
            print(f"  [{i}] {name}")

        try:
            port_index = int(input("Choose a port to assign for the metronome: "))
            if 0 <= port_index < len(all_outputs):
                port_name = all_outputs[port_index]
                seq.song.metronome_port_name = port_name
                print(f"Metronome assigned to port '{port_name}'.")
            else:
                print("Error: Invalid port index.")
        except (ValueError, IndexError):
            print("Error: Invalid input.")
    elif command == "unassign":
        if len(args) == 1:
            seq.unassign_port(track_index=int(args[0]))
        else:
            print("Usage: unassign <track_index>")
    elif command == "setaudiocmd":
        if args:
            cmd_str = " ".join(args)
            seq.audio_player_command = cmd_str
            print(f"Audio player command set to: {cmd_str}")
            print("Note: The audio filepath will be appended to this command.")
        else:
            print("Usage: setaudiocmd <command...>")
            print(f"Current command: {seq.audio_player_command}")
    elif command == "setbank":
        if len(args) == 2:
            seq.set_bank(track_index=int(args[0]), msb=int(args[1]))
        elif len(args) == 3:
            seq.set_bank(track_index=int(args[0]), msb=int(args[1]), lsb=int(args[2]))
        else:
            print("Usage: setbank <track_index> <msb> [lsb]")
    elif command == "setch":
        if len(args) == 2:
            seq.set_channel(track_index=int(args[0]), channel=int(args[1]))
        else:
            print("Usage: setch <track_index> <channel>")
    elif command == "setprog":
        if len(args) == 2:
            prog = int(args[1])
            if not 1 <= prog <= 128:
                print("Error: Program number must be between 1 and 128.")
            else:
                seq.set_program(track_index=int(args[0]), program=prog - 1)
        else:
            print("Usage: setprog <track_index> <program>")
    elif command == "volume":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                volume_str = input("Enter volume (0.0 - 1.0): ").strip()
                volume = float(volume_str)
                seq.set_track_volume(track_index, volume)
            except ValueError:
                print("Error: Invalid track index or volume.")
        else:
            print("Usage: volume <track_index>")
    elif command == "pan":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                pan_str = input("Enter pan (-1.0 to 1.0): ").strip()
                pan = float(pan_str)
                seq.set_track_pan(track_index, pan)
            except ValueError:
                print("Error: Invalid track index or pan.")
        else:
            print("Usage: pan <track_index>")
    elif command == "velocity":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                velocity_str = input("Enter velocity multiplier (e.g., 1.0): ").strip()
                velocity = float(velocity_str)
                seq.set_track_velocity(track_index, velocity)
            except ValueError:
                print("Error: Invalid track index or velocity.")
        else:
            print("Usage: velocity <track_index>")
    elif command == "mute":
        if len(args) == 1:
            seq.toggle_mute(track_index=int(args[0]))
        else:
            print("Usage: mute <track_index>")
    elif command == "solo":
        if len(args) == 1:
            seq.toggle_solo(track_index=int(args[0]))
        else:
            print("Usage: solo <track_index>")
    elif command == "record":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                seq.record_track(track_index)
            except ValueError:
                print("Error: Invalid track index.")
        else:
            print("Usage: record <track_index>")
    elif command == "bis":
        seq.record_bis()
    elif command == "delete":
        if len(args) == 1:
            track_index = int(args[0])
            if 0 <= track_index < len(seq.song.tracks):
                track_name = seq.song.tracks[track_index].name
                confirm = input(f"Are you sure you want to delete track '{track_name}'? [y/N] ").lower()
                if confirm == 'y':
                    seq.delete_track(track_index)
                else:
                    print("Deletion cancelled.")
            else:
                print("Error: Invalid track index.")
        else:
            print("Usage: delete <track_index>")
    elif command == "erase":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                # The sequencer method will now handle all prompting
                seq.erase_track(track_index=track_index)
            except ValueError:
                print("Error: Invalid track index.")
        else:
            print("Usage: erase <track_index>")
    elif command == "rename":
        if len(args) == 2:
            seq.rename_track(track_index=int(args[0]), new_name=args[1])
        else:
            print("Usage: rename <track_index> <new_name>")
    elif command == "move":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                seq.move_track_section(track_index)
            except ValueError:
                print("Error: Invalid track index.")
        else:
            print("Usage: move <track_index>")
    elif command == "copy":
        seq.copy_track_section()
    elif command == "transpose":
        seq.transpose_track_section()
    elif command == "tempo":
        if len(args) == 1:
            seq.set_tempo(tempo=int(args[0]))
        else:
            print("Usage: tempo <bpm>")
    elif command == "timesig":
        if len(args) == 2:
            seq.set_time_signature(numerator=int(args[0]), denominator=int(args[1]))
        else:
            print("Usage: timesig <numerator> <denominator>")
    elif command == "save":
        if len(args) == 1:
            seq.save_song(filepath=args[0])
        else:
            print("Usage: save <filepath>")
    elif command == "saveproject":
        if len(args) == 1:
            seq.save_project(basename=args[0])
        else:
            print("Usage: saveproject <basename>")
    elif command == "prime":
        seq.prime_all_tracks()
    elif command == "cc":
        try:
            hardware_ports = mido.get_output_names()
            virtual_port_names = [vp.name for vp in seq.virtual_ports]
            all_outputs = hardware_ports + virtual_port_names

            if not all_outputs:
                print("No output ports available.")
                return True

            print("Available output ports:")
            for i, name in enumerate(all_outputs):
                print(f"  [{i}] {name}")

            port_idx_str = input("Choose a port to send to: ").strip()
            port_idx = int(port_idx_str)
            if not 0 <= port_idx < len(all_outputs):
                print("Error: Invalid port index.")
                return True
            port_name = all_outputs[port_idx]

            channel_str = input("Enter MIDI channel (1-16): ").strip()
            channel = int(channel_str) - 1 # To 0-indexed

            control_str = input("Enter CC number (0-127): ").strip()
            control = int(control_str)

            value_str = input("Enter CC value (0-127): ").strip()
            value = int(value_str)

            seq.send_cc_message(port_name, channel, control, value)

        except (ValueError, IndexError):
            print("Error: Invalid input.")

    elif command == "play":
        if len(args) == 0:
            # play
            seq.play_range_enabled = False
            seq.play()
        elif len(args) == 1:
            # play <start>
            start_beat = seq.parse_position_to_beats(args[0])
            if start_beat is not None:
                if seq.loop_enabled:
                    print("Looping disabled.")
                    seq.loop_enabled = False
                seq.play_range_enabled = False
                seq.play(start_beat=start_beat)
        elif len(args) == 2:
            # play <start> <end>
            start_beat = seq.parse_position_to_beats(args[0])
            end_beat = seq.parse_position_to_beats(args[1])
            if start_beat is None or end_beat is None:
                return True

            if end_beat <= start_beat:
                print("Error: End position must be after the start position.")
                return True

            # Set the play range and disable looping to avoid conflict
            seq.play_range_start_beat = start_beat
            seq.play_range_end_beat = end_beat
            seq.play_range_enabled = True
            if seq.loop_enabled:
                seq.loop_enabled = False
                print("Looping disabled to allow play range.")

            print(f"Set to stop at {args[1]}.")
            seq.play(start_beat=start_beat)
        else:
            print("Usage: play [start_position] [end_position]")
            print("Example: play 10:1 15:1")
    elif command == "pause":
        seq.pause()
    elif command == "loop":
        if len(args) == 0:
            seq.loop_enabled = not seq.loop_enabled
            status = "enabled" if seq.loop_enabled else "disabled"
            if seq.loop_enabled and seq.play_range_enabled:
                print("Disabling play range to enable looping.")
                seq.play_range_enabled = False
            print(f"Looping is now {status}.")
            if not seq.loop_enabled:
                print("Note: Loop points are still saved. Use 'loop <start> <end>' to set new points.")
            elif seq.loop_end_beat <= seq.loop_start_beat:
                print("Warning: Loop end is not after loop start. The loop will not function correctly.")
        elif len(args) == 2:
            start_beat = seq.parse_position_to_beats(args[0])
            end_beat = seq.parse_position_to_beats(args[1])
            if start_beat is None or end_beat is None:
                # Error is printed by parse_position_to_beats
                return True

            if end_beat <= start_beat:
                print("Error: Loop end position must be after the start position.")
                return True

            seq.loop_start_beat = start_beat
            seq.loop_end_beat = end_beat
            seq.loop_enabled = True
            if seq.play_range_enabled:
                print("Disabling play range to enable looping.")
                seq.play_range_enabled = False
            print(f"Loop enabled from {args[0]} to {args[1]}.")
            # Also start playback from the beginning of the loop
            seq.play(start_beat=start_beat)
        else:
            print("Usage: loop [start_position] [end_position]")
            print("Example: loop 1:1 5:1")
    elif command == "stop":
        seq.stop()
    elif command == "seek":
        if len(args) == 1:
            seq.seek(args[0])
        else:
            print("Usage: seek <amount> (e.g., +1m, -4b)")
    elif command == "metronome":
        if len(args) == 1 and args[0].lower() in ["on", "off"]:
            is_enabled = args[0].lower() == "on"
            seq.song.metronome_enabled = is_enabled
            status = "enabled" if is_enabled else "disabled"
            print(f"Metronome is now {status}.")
            if is_enabled and seq.playback_state != "stopped":
                seq.start_metronome()
        else:
            print("Usage: metronome <on|off>")
    elif command == "setcontrolport":
        if len(args) == 1:
            try:
                port_index = int(args[0])
                input_ports = mido.get_input_names()
                if 0 <= port_index < len(input_ports):
                    port_name = input_ports[port_index]
                    seq.set_control_port(port_name)
                else:
                    print("Error: Invalid port index.")
            except (ValueError, IndexError):
                print("Error: Invalid input.")
        else:
            print("Usage: setcontrolport <port_index>")
    elif command == "unsetcontrolport":
        seq.unset_control_port()
    elif command == "map":
        if len(args) == 4:
            try:
                channel = int(args[0]) - 1 # to 0-indexed
                control = int(args[1])
                track_index = int(args[2])
                action = args[3].lower()

                if not 0 <= channel <= 15:
                    print("Error: Channel must be between 1 and 16.")
                    return True
                if not 0 <= control <= 127:
                    print("Error: CC number must be between 0 and 127.")
                    return True
                if not 0 <= track_index < len(seq.song.tracks):
                    print("Error: Invalid track index.")
                    return True

                valid_actions = ['volume', 'pan', 'program']
                if action not in valid_actions:
                    print(f"Error: Invalid action. Must be one of {valid_actions}.")
                    return True

                from sequencer.models import MidiMapping
                mapping = MidiMapping(channel=channel, control=control, track_index=track_index, action=action)

                # Remove any existing mapping for this channel/cc
                seq.song.midi_mappings = [m for m in seq.song.midi_mappings if not (m.channel == channel and m.control == control)]
                seq.song.midi_mappings.append(mapping)
                print(f"Mapped Ch:{channel+1} CC:{control} to {action} on track {track_index}.")
                seq.is_dirty = True

            except ValueError:
                print("Error: Invalid number for channel, CC, or track index.")
        else:
            print("Usage: map <channel> <cc> <track_index> <action>")
    elif command == "unmap":
        if len(args) == 2:
            try:
                channel = int(args[0]) - 1 # to 0-indexed
                control = int(args[1])

                initial_len = len(seq.song.midi_mappings)
                seq.song.midi_mappings = [m for m in seq.song.midi_mappings if not (m.channel == channel and m.control == control)]
                if len(seq.song.midi_mappings) < initial_len:
                    print(f"Unmapped Ch:{channel+1} CC:{control}.")
                    seq.is_dirty = True
                else:
                    print("Mapping not found.")

            except ValueError:
                print("Error: Invalid number for channel or CC.")
        else:
            print("Usage: unmap <channel> <cc>")
    elif command == "listmaps":
        if not seq.song.midi_mappings:
            print("No MIDI mappings defined.")
            return True

        print("Active MIDI Mappings:")
        for m in seq.song.midi_mappings:
            print(f"  Ch:{m.channel+1} CC:{m.control} -> Track {m.track_index} {m.action.capitalize()}")

    else:
        print(f"Unknown command: '{command}'. Type 'help' for a list of commands.")
    return True

def main():
    """The main entry point for the CLI application."""
    print("Welcome to the Python MIDI Sequencer!")
    seq = Sequencer()
    print_help()

    command_buffer = ""
    print("> ", end="", flush=True)

    while True:
        try:
            char = get_char()

            if char == 'ARROW_LEFT':
                print() # Move to a new line to not mess up the current command line
                process_command("seek -1m", seq)
                command_buffer = "" # Clear buffer after action
                print(f"> ", end="", flush=True)
                continue
            elif char == 'ARROW_RIGHT':
                print() # Move to a new line to not mess up the current command line
                process_command("seek +1m", seq)
                command_buffer = "" # Clear buffer after action
                print(f"> ", end="", flush=True)
                continue

            # Handle Ctrl+C or Ctrl+D for exit
            if char in ('\x03', '\x04'):
                if seq.playback_state != "stopped":
                    print("\nStopping playback before exiting...")
                    seq.stop()
                break

            elif char == ' ':
                if seq.playback_state != "stopped" and not command_buffer:
                    # If playing and command buffer is empty, spacebar is a shortcut for pause
                    print() # Move to a new line to not mess up the current command line
                    process_command("pause", seq)
                    command_buffer = "" # Clear buffer after pausing
                    print(f"> ", end="", flush=True)
                else:
                    # Otherwise, it's a normal character (part of a command)
                    command_buffer += ' '
                    print(' ', end="", flush=True)
                continue

            elif char in ('\r', '\n'):
                print()  # Move to the next line
                if not process_command(command_buffer, seq):
                    break # Exit if process_command returns False (for 'quit')
                command_buffer = ""
                print("> ", end="", flush=True)

            elif char in ('\x7f', '\b'): # Handle backspace
                if len(command_buffer) > 0:
                    command_buffer = command_buffer[:-1]
                    print("\b \b", end="", flush=True) # Erase character on screen

            elif char.isprintable():
                command_buffer += char
                print(char, end="", flush=True)

        except (ValueError, IndexError) as e:
            print(f"\nError: Invalid argument. Please check your input. ({e})")
            command_buffer = ""
            print(f"> {command_buffer}", end="", flush=True)
        except Exception as e:
            print(f"\nAn unexpected error occurred: {e}")
            command_buffer = ""
            print(f"> {command_buffer}", end="", flush=True)

    # Clean up before exiting
    print("\nExiting sequencer. Goodbye!")
    if seq.midi_listener_thread and seq.midi_listener_thread.is_alive():
        seq.unset_control_port()
    seq.close_virtual_ports()

if __name__ == "__main__":
    if '--help' in sys.argv or '-h' in sys.argv:
        print_help()
        sys.exit(0)
    main()
