import mido
import jack
from sequencer.sequencer import Sequencer
import sys
import time
import os
import json

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

def get_help_text():
    """Prints the help message with available commands."""
    return """
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
  copy <track_index>      - Copies a section of a track using 'measure:beat' positions.
  move <track_index>      - Moves a section of a track using 'measure:beat' positions.
  transpose <track_index> - Transposes a section of a track using 'measure:beat' positions.
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

def process_command(user_input, seq, api_mode=False, confirmation_handler=None):
    if not user_input:
        return True, "" # Continue loop

    parts = user_input.split()
    command = parts[0].lower()
    args = parts[1:]

    if command == "quit":
        if seq.is_dirty:
            if len(args) == 0:
                if api_mode:
                    return True, json.dumps({"status": "prompt", "message": "You have unsaved changes. (S)ave, (D)iscard, or (C)ancel? ", "next_arg": "choice"})
                else:
                    choice = input("You have unsaved changes. (S)ave, (D)iscard, or (C)ancel? ").lower()
                    args.append(choice)

            choice = args[0]
            if choice == 'c':
                return True, "Quit cancelled."
            elif choice == 'd':
                pass # Proceed to quit
            elif choice == 's':
                basename_to_save = seq.last_project_basename
                if len(args) == 1:
                    if basename_to_save:
                        if api_mode:
                            return True, json.dumps({"status": "prompt", "message": f"Save over '{basename_to_save}.proj.json'? [Y/n] ", "next_arg": "overwrite"})
                        else:
                            overwrite = input(f"Save over '{basename_to_save}.proj.json'? [Y/n] ").lower()
                            args.append(overwrite)
                    else:
                        if api_mode:
                            return True, json.dumps({"status": "prompt", "message": "Enter project basename to save: ", "next_arg": "basename"})
                        else:
                            basename_to_save = input("Enter project basename to save: ").strip()
                            args.append(basename_to_save)

                if len(args) == 2:
                    if args[1].lower() == 'n':
                        if len(args) == 2: # We need to ask for a new name
                            if api_mode:
                                return True, json.dumps({"status": "prompt", "message": "Enter new project basename: ", "next_arg": "basename"})
                            else:
                                basename_to_save = input("Enter new project basename: ").strip()
                                # No args.append here, we'll fall through to the save
                        else: # This case should not be hit in api_mode
                            basename_to_save = args[2]

                    elif args[1].lower() != 'y':
                        # This case handles when a new basename is provided directly
                        basename_to_save = args[1]
                    # If args[1] is 'y', we do nothing and let basename_to_save keep its value.

                if len(args) == 3: # This handles the case where we asked for a new name after 'n'
                    basename_to_save = args[2]

                if basename_to_save:
                    seq.save_project(basename_to_save)
                else:
                    return True, "Save cancelled. Please provide a name."
            else:
                return True, "Invalid choice."

        if seq.playback_state != "stopped":
            if not api_mode:
                print("Stopping playback before exiting...")
            seq.stop()
        return False, "Exiting." # End loop
    elif command == "help":
        return True, get_help_text()
    elif command == "add":
        if len(args) == 1:
            result = seq.add_track(name=args[0], track_type='midi')
            if api_mode:
                return True, json.dumps(result)
            else:
                return True, result['message']
        elif len(args) == 2:
            try:
                prog = int(args[1])
                if not 1 <= prog <= 128:
                    return True, "Error: Program number must be between 1 and 128."
                else:
                    result = seq.add_track(name=args[0], track_type='midi', instrument=prog - 1)
                    if api_mode:
                        return True, json.dumps(result)
                    else:
                        return True, result['message']
            except ValueError:
                return True, "Error: Invalid program number."
        else:
            return True, "Usage: add <name> [program_number]"
    elif command == "addaudio":
        if len(args) == 2:
            result = seq.add_track(name=args[0], track_type='audio', filepath=args[1])
            if api_mode:
                return True, json.dumps(result)
            else:
                return True, result['message']
        else:
            return True, "Usage: addaudio <name> <filepath>"
    elif command == "addauto":
        if len(args) == 2:
            try:
                name = args[0]
                target_index = int(args[1])
                result = seq.add_automation_track(name, target_index)
                if api_mode:
                    return True, json.dumps(result)
                else:
                    return True, result['message']
            except ValueError:
                return True, "Error: Invalid target track index."
        else:
            return True, "Usage: addauto <name> <target_track_index>"
    elif command == "addap":
        if len(args) >= 4:
            try:
                track_index = int(args[0])
                position_str = args[1]
                param = args[2]
                value = float(args[3])
                curve = args[4] if len(args) > 4 else "none"
                return True, seq.add_automation_point(track_index, position_str, param, value, curve)
            except ValueError:
                return True, "Error: Invalid number for track index or value."
            except Exception as e:
                return True, f"Error: {e}"
        else:
            return True, "Usage: addap <track_index> <position> <param> <value> [curve]"
    elif command == "addcc":
        if len(args) == 4:
            try:
                track_index = int(args[0])
                position_str = args[1]
                control = int(args[2])
                value = int(args[3])
                return True, seq.add_cc_event(track_index, position_str, control, value)
            except ValueError:
                return True, "Error: Invalid number for track index, CC, or value."
        else:
            return True, "Usage: addcc <track_index> <position> <cc_number> <value>"
    elif command == "load":
        if len(args) == 1:
            if api_mode:
                return True, json.dumps({"status": "prompt", "message": "Loading a new song will discard the current session. Are you sure? [y/N] ", "next_arg": "confirm"})
            else:
                confirm = input("Loading a new song will discard the current session. Are you sure? [y/N] ").lower()
                if confirm == 'y':
                    return True, seq.load_song(filepath=args[0])
                else:
                    return True, "Load cancelled."
        elif len(args) == 2:
            if args[1] == 'y':
                return True, seq.load_song(filepath=args[0])
            else:
                return True, "Load cancelled."
        else:
            return True, "Usage: load <filepath>"
    elif command == "loadproject":
        if len(args) == 1:
            if api_mode:
                return True, json.dumps({"status": "prompt", "message": "Loading a new project will discard the current session. Are you sure? [y/N] ", "next_arg": "confirm"})
            else:
                confirm = input("Loading a new project will discard the current session. Are you sure? [y/N] ").lower()
                if confirm == 'y':
                    return True, seq.load_project(basename=args[0])
                else:
                    return True, "Load cancelled."
        elif len(args) == 2:
            if args[1] == 'y':
                return True, seq.load_project(basename=args[0])
            else:
                return True, "Load cancelled."
        else:
            return True, "Usage: loadproject <basename>"
    elif command == "newproject":
        if len(args) == 1:
            project_name = args[0]
            project_filepath = f"{project_name}.proj.json"
            if os.path.exists(project_filepath):
                return True, f"Error: Project '{project_name}' already exists."

            if api_mode:
                return True, json.dumps({"status": "prompt", "message": "Creating a new project will discard the current session. Are you sure? [y/N] ", "next_arg": "confirm"})
            else:
                confirm = input("Creating a new project will discard the current session. Are you sure? [y/N] ").lower()
                if confirm == 'y':
                    seq.new_project()
                    seq.last_project_basename = project_name
                    seq.is_dirty = True
                    return True, f"New project '{project_name}' created."
                else:
                    return True, "New project cancelled."
        elif len(args) == 2:
            if args[1] == 'y':
                project_name = args[0]
                seq.new_project()
                seq.last_project_basename = project_name
                seq.is_dirty = True
                return True, f"New project '{project_name}' created."
            else:
                return True, "New project cancelled."
        else:
            return True, "Usage: newproject <name>"
    elif command == "list":
        return True, seq.list_tracks()
    elif command == "vport":
        if len(args) == 1:
            return True, seq.create_virtual_port(name=args[0])
        else:
            return True, "Usage: vport <port_name>"
    elif command == "delvport":
        if not seq.virtual_ports:
            return True, "No virtual ports to delete."

        if len(args) == 0:
            output = "Available virtual ports:\n"
            for i, vp in enumerate(seq.virtual_ports):
                output += f"  [{i}] {vp.name}\n"
            if api_mode:
                return True, json.dumps({"status": "prompt", "message": output + "Choose a virtual port to delete: ", "next_arg": "idx_str"})
            else:
                print(output)
                idx_str = input("Choose a virtual port to delete: ")
                args.append(idx_str)

        try:
            idx = int(args[0])
            if 0 <= idx < len(seq.virtual_ports):
                return True, seq.delete_virtual_port(seq.virtual_ports[idx].name)
            else:
                return True, "Error: Invalid index."
        except (ValueError, IndexError):
            return True, "Error: Invalid input."

    elif command == "ports":
        return True, seq.list_ports()
    elif command == "assign":
        if len(args) == 1:
            try:
                track_index = int(args[0])
                if not 0 <= track_index < len(seq.song.tracks):
                    return True, "Error: Invalid track index."

                hardware_ports = mido.get_output_names() # type: ignore
                virtual_port_names = [vp.name for vp in seq.virtual_ports]
                all_outputs = hardware_ports + virtual_port_names

                if not all_outputs:
                    return True, "No output ports available."

                output = "Available output ports:\n"
                for i, name in enumerate(all_outputs):
                    output += f"  [{i}] {name}\n"

                if api_mode:
                    return True, json.dumps({"status": "prompt", "message": output + "Choose a port to assign: ", "next_arg": "port_index_str"})
                else:
                    print(output)
                    port_index_str = input("Choose a port to assign: ")
                    args.append(port_index_str)
            except (ValueError, IndexError):
                return True, "Error: Invalid input."

        if len(args) == 2:
            try:
                track_index = int(args[0])
                port_index = int(args[1])
                hardware_ports = mido.get_output_names() # type: ignore
                virtual_port_names = [vp.name for vp in seq.virtual_ports]
                all_outputs = hardware_ports + virtual_port_names
                if 0 <= port_index < len(all_outputs):
                    port_name = all_outputs[port_index]
                    return True, seq.assign_port(track_index, port_name)
                else:
                    return True, "Error: Invalid port index."
            except (ValueError, IndexError):
                return True, "Error: Invalid input."
        else:
            return True, "Usage: assign <track_index>"
    elif command == "assignmetro":
        if len(args) == 0:
            hardware_ports = mido.get_output_names() # type: ignore
            virtual_port_names = [vp.name for vp in seq.virtual_ports]
            all_outputs = hardware_ports + virtual_port_names

            if not all_outputs:
                return True, "No output ports available."

            output = "Available output ports:\n"
            for i, name in enumerate(all_outputs):
                output += f"  [{i}] {name}\n"

            if api_mode:
                return True, json.dumps({"status": "prompt", "message": output + "Choose a port to assign for the metronome: ", "next_arg": "port_index_str"})
            else:
                print(output)
                port_index_str = input("Choose a port to assign for the metronome: ")
                args.append(port_index_str)

        if len(args) == 1:
            try:
                port_index = int(args[0])
                hardware_ports = mido.get_output_names() # type: ignore
                virtual_port_names = [vp.name for vp in seq.virtual_ports]
                all_outputs = hardware_ports + virtual_port_names
                if 0 <= port_index < len(all_outputs):
                    port_name = all_outputs[port_index]
                    seq.song.metronome_port_name = port_name
                    return True, f"Metronome assigned to port '{port_name}'."
                else:
                    return True, "Error: Invalid port index."
            except (ValueError, IndexError):
                return True, "Error: Invalid input."
    elif command == "unassign":
        if len(args) == 1:
            return True, seq.unassign_port(track_index=int(args[0]))
        else:
            return True, "Usage: unassign <track_index>"
    elif command == "setaudiocmd":
        if args:
            cmd_str = " ".join(args)
            seq.audio_player_command = cmd_str
            return True, f"Audio player command set to: {cmd_str}\nNote: The audio filepath will be appended to this command."
        else:
            return True, f"Usage: setaudiocmd <command...>\nCurrent command: {seq.audio_player_command}"
    elif command == "setbank":
        if len(args) == 2:
            return True, seq.set_bank(track_index=int(args[0]), msb=int(args[1]))
        elif len(args) == 3:
            return True, seq.set_bank(track_index=int(args[0]), msb=int(args[1]), lsb=int(args[2]))
        else:
            return True, "Usage: setbank <track_index> <msb> [lsb]"
    elif command == "setch":
        if len(args) == 2:
            return True, seq.set_channel(track_index=int(args[0]), channel=int(args[1]))
        else:
            return True, "Usage: setch <track_index> <channel>"
    elif command == "setprog":
        if len(args) == 2:
            prog = int(args[1])
            if not 1 <= prog <= 128:
                return True, "Error: Program number must be between 1 and 128."
            else:
                return True, seq.set_program(track_index=int(args[0]), program=prog - 1)
        else:
            return True, "Usage: setprog <track_index> <program>"
    elif command == "volume":
        if len(args) >= 1:
            try:
                track_index = int(args[0])
                volume_str = args[1] if len(args) > 1 else None
                result = seq.set_track_volume(track_index, volume_str=volume_str, api_mode=api_mode, confirmation_handler=confirmation_handler)
                if result:
                    if api_mode:
                        return True, json.dumps(result)
                    else:
                        return True, result['message']
            except (ValueError, IndexError):
                return True, "Error: Invalid arguments for volume."
        else:
            return True, "Usage: volume <track_index> [volume]"
    elif command == "pan":
        if len(args) >= 1:
            try:
                track_index = int(args[0])
                pan_str = args[1] if len(args) > 1 else None
                result = seq.set_track_pan(track_index, pan_str=pan_str, api_mode=api_mode, confirmation_handler=confirmation_handler)
                if result:
                    if api_mode:
                        return True, json.dumps(result)
                    else:
                        return True, result['message']
            except (ValueError, IndexError):
                return True, "Error: Invalid arguments for pan."
        else:
            return True, "Usage: pan <track_index> [pan]"
    elif command == "velocity":
        if len(args) >= 1:
            try:
                track_index = int(args[0])
                velocity_str = args[1] if len(args) > 1 else None
                result = seq.set_track_velocity(track_index, velocity_str=velocity_str, api_mode=api_mode, confirmation_handler=confirmation_handler)
                if result:
                    if api_mode:
                        return True, json.dumps(result)
                    else:
                        return True, result['message']
            except (ValueError, IndexError):
                return True, "Error: Invalid arguments for velocity."
        else:
            return True, "Usage: velocity <track_index> [velocity]"
    elif command == "mute":
        if len(args) == 1:
            return True, seq.toggle_mute(track_index=int(args[0]))
        else:
            return True, "Usage: mute <track_index>"
    elif command == "solo":
        if len(args) == 1:
            return True, seq.toggle_solo(track_index=int(args[0]))
        else:
            return True, "Usage: solo <track_index>"
    elif command == "record":
        try:
            if len(args) == 0:
                return True, "Usage: record <track_index>"

            track_idx = int(args[0])
            if not 0 <= track_idx < len(seq.song.tracks):
                print("Error: Invalid track index.")
            target_track = seq.song.tracks[track_idx]
            if not isinstance(target_track, MidiTrack):
                return True, "Error: Recording is only supported for MIDI tracks."

            # Step 1: Get start position
            if len(args) == 1:
                prompt = f"Start recording at position on track '{target_track.name}' (measure:beat) [default: 1:1]: "
                if api_mode: return True, json.dumps({"status": "prompt", "message": prompt, "next_arg": "start_pos"})
                args.append(input(prompt).strip())

            start_pos_str = args[1] if len(args) > 1 else "1:1"
            start_beat = seq.parse_position_to_beats(start_pos_str, default="1:1")
            if start_beat is None: return True, "Invalid start position."

            # Step 2: Get duration
            if len(args) == 2:
                prompt = "Record for how long (measures:beats)? (Press Enter for unlimited) "
                if api_mode: return True, json.dumps({"status": "prompt", "message": prompt, "next_arg": "duration"})
                args.append(input(prompt).strip())

            measures_input = args[2] if len(args) > 2 else ""
            num_beats_to_record = None
            if measures_input:
                parts = measures_input.split(':')
                num_measures = int(parts[0])
                num_beats = int(parts[1]) if len(parts) == 2 else 0
                num_beats_to_record = (num_measures * seq.song.time_signature_numerator) + num_beats

            # Step 3: Check for existing notes and ask to replace
            replace_notes = False
            if len(args) == 3:
                existing_notes_in_range = [e for e in target_track.events if e.start_time >= start_beat]
                if existing_notes_in_range:
                    prompt = "There are existing notes. Do you want to (r)eplace them or (a)dd to them? [r/a] "
                    if api_mode: return True, json.dumps({"status": "prompt", "message": prompt, "next_arg": "replace"})
                    args.append(input(prompt).lower())
                else:
                    args.append('a') # Default to add if no notes

            replace_notes = True if len(args) > 3 and args[3].startswith('r') else False

            # Step 4: Ask for MIDI Thru
            enable_thru = True
            if len(args) == 4:
                if target_track.output_port_name:
                    prompt = "Enable MIDI Thru (hear instrument while recording)? [Y/n] "
                    if api_mode: return True, json.dumps({"status": "prompt", "message": prompt, "next_arg": "thru"})
                    args.append(input(prompt).lower())
                else:
                    args.append('y') # Default to yes if no output port

            enable_thru = False if len(args) > 4 and args[4].startswith('n') else True

            # Step 5: Get input port
            if len(args) == 5:
                input_ports = mido.get_input_names()
                if not input_ports: return True, "Error: No MIDI input ports found."
                output = "Available MIDI input ports:\n"
                for i, port in enumerate(input_ports): output += f"  [{i}] {port}\n"
                prompt = output + "Choose a port to record from: "
                if api_mode: return True, json.dumps({"status": "prompt", "message": prompt, "next_arg": "port_idx"})
                args.append(input(prompt).strip())

            inport_idx = int(args[5])
            input_ports = mido.get_input_names()
            if not 0 <= inport_idx < len(input_ports):
                return True, "Error: Invalid port index."
            inport_name = input_ports[inport_idx]

            return True, seq.record_track(track_idx, start_beat, num_beats_to_record, inport_name, replace_notes, enable_thru)

        except (ValueError, IndexError):
            return True, "Error: Invalid input for record command."

    elif command == "bis":
        if seq.last_record_settings is None:
            return True, "Error: No previous recording settings found. Use 'record' first."

        replace_notes = False
        if len(args) == 0:
            track_idx = seq.last_record_settings['track_index']
            start_beat = seq.last_record_settings['start_beat']
            target_track = seq.song.tracks[track_idx]
            existing_notes_in_range = [e for e in target_track.events if e.start_time >= start_beat]
            if existing_notes_in_range:
                prompt = "There are existing notes. Do you want to (r)eplace them or (a)dd to them? [r/a] "
                if api_mode: return True, json.dumps({"status": "prompt", "message": prompt, "next_arg": "replace"})
                args.append(input(prompt).lower())
            else:
                args.append('a')

        replace_notes = True if len(args) > 0 and args[0].startswith('r') else False
        return True, seq.record_bis(replace_notes)
    elif command == "delete":
        if len(args) >= 1:
            try:
                track_index = int(args[0])
                confirm_str = args[1] if len(args) > 1 else None
                result = seq.delete_track(track_index, confirm_str=confirm_str, api_mode=api_mode, confirmation_handler=confirmation_handler)
                if result:
                    if api_mode:
                        return True, json.dumps(result)
                    else:
                        return True, result['message']
            except (ValueError, IndexError):
                return True, "Error: Invalid arguments for delete."
        else:
            return True, "Usage: delete <track_index> [y/n]"
    elif command == "erase":
        if len(args) == 1:
            try:
                track_idx = int(args[0])
                result = seq.erase_track(track_idx=track_idx, confirmation_handler=confirmation_handler)
                if result and 'message' in result:
                    if api_mode:
                        return True, json.dumps(result)
                    else:
                        return True, result['message']
            except ValueError:
                return True, "Error: Invalid track index."
        else:
            return True, "Usage: erase <track_index>"
    elif command == "rename":
        if len(args) == 2:
            result = seq.rename_track(track_index=int(args[0]), new_name=args[1])
            if result:
                if api_mode:
                    return True, json.dumps(result)
                else:
                    return True, result['message']
        else:
            return True, "Usage: rename <track_index> <new_name>"
    elif command == "move":
        if len(args) == 1:
            try:
                source_track_idx = int(args[0])
                return True, seq.move_track_section(source_track_idx, confirmation_handler=confirmation_handler)
            except ValueError:
                return True, "Error: Invalid track index."
        else:
            return True, "Usage: move <track_index>"
    elif command == "copy":
        if len(args) == 1:
            try:
                source_track_idx = int(args[0])
                return True, seq.copy_track_section(source_track_idx, confirmation_handler=confirmation_handler)
            except ValueError:
                return True, "Error: Invalid track index."
        else:
            return True, "Usage: copy <track_index>"
    elif command == "transpose":
        if len(args) >= 1:
            try:
                track_idx = int(args[0])
                start_pos_str = args[1] if len(args) > 1 else None
                end_pos_str = args[2] if len(args) > 2 else None
                transpose_value_str = args[3] if len(args) > 3 else None
                confirm_str = args[4] if len(args) > 4 else None

                result = seq.transpose_track_section(
                    track_idx=track_idx,
                    start_pos_str=start_pos_str,
                    end_pos_str=end_pos_str,
                    transpose_value_str=transpose_value_str,
                    confirm_str=confirm_str,
                    api_mode=api_mode,
                    confirmation_handler=confirmation_handler
                )

                if result:
                    if api_mode:
                        return True, json.dumps(result)
                    else:
                        return True, result['message']

            except (ValueError, IndexError):
                return True, "Error: Invalid arguments for transpose."
        else:
            return True, "Usage: transpose <track_index> [start_pos] [end_pos] [semitones] [y/n]"
    elif command == "tempo":
        if len(args) == 1:
            return True, seq.set_tempo(tempo=int(args[0]))
        else:
            return True, "Usage: tempo <bpm>"
    elif command == "timesig":
        if len(args) == 2:
            return True, seq.set_time_signature(numerator=int(args[0]), denominator=int(args[1]))
        else:
            return True, "Usage: timesig <numerator> <denominator>"
    elif command == "save":
        if len(args) == 1:
            return True, seq.save_song(filepath=args[0])
        else:
            return True, "Usage: save <filepath>"
    elif command == "saveproject":
        if len(args) == 1:
            return True, seq.save_project(basename=args[0])
        else:
            return True, "Usage: saveproject <basename>"
    elif command == "prime":
        return True, seq.prime_all_tracks()
    elif command == "cc":
        if len(args) == 0:
            hardware_ports = mido.get_output_names()
            virtual_port_names = [vp.name for vp in seq.virtual_ports]
            all_outputs = hardware_ports + virtual_port_names

            if not all_outputs:
                return True, "No output ports available."

            output = "Available output ports:\n"
            for i, name in enumerate(all_outputs):
                output += f"  [{i}] {name}\n"

            if api_mode:
                return True, json.dumps({"status": "prompt", "message": output + "Choose a port to send to: ", "next_arg": "port_idx_str"})
            else:
                print(output)
                port_idx_str = input("Choose a port to send to: ").strip()
                args.append(port_idx_str)

        if len(args) == 1:
            if api_mode:
                return True, json.dumps({"status": "prompt", "message": "Enter MIDI channel (1-16): ", "next_arg": "channel_str"})
            else:
                channel_str = input("Enter MIDI channel (1-16): ").strip()
                args.append(channel_str)

        if len(args) == 2:
            if api_mode:
                return True, json.dumps({"status": "prompt", "message": "Enter CC number (0-127): ", "next_arg": "control_str"})
            else:
                control_str = input("Enter CC number (0-127): ").strip()
                args.append(control_str)

        if len(args) == 3:
            if api_mode:
                return True, json.dumps({"status": "prompt", "message": "Enter CC value (0-127): ", "next_arg": "value_str"})
            else:
                value_str = input("Enter CC value (0-127): ").strip()
                args.append(value_str)

        if len(args) == 4:
            try:
                port_idx = int(args[0])
                hardware_ports = mido.get_output_names()
                virtual_port_names = [vp.name for vp in seq.virtual_ports]
                all_outputs = hardware_ports + virtual_port_names
                if not 0 <= port_idx < len(all_outputs):
                    return True, "Error: Invalid port index."
                port_name = all_outputs[port_idx]

                channel = int(args[1]) - 1
                control = int(args[2])
                value = int(args[3])
                return True, seq.send_cc_message(port_name, channel, control, value)
            except (ValueError, IndexError):
                return True, "Error: Invalid input."

    elif command == "play":
        if len(args) == 0:
            # play
            seq.play_range_enabled = False
            seq.play()
            return True, "Playing."
        elif len(args) == 1:
            # play <start>
            start_beat = seq.parse_position_to_beats(args[0])
            if start_beat is not None:
                output = ""
                if seq.loop_enabled:
                    output += "Looping disabled.\n"
                    seq.loop_enabled = False
                seq.play_range_enabled = False
                seq.play(start_beat=start_beat)
                return True, output + f"Playing from {args[0]}."
            else:
                return True, "Invalid position."
        elif len(args) == 2:
            # play <start> <end>
            start_beat = seq.parse_position_to_beats(args[0])
            end_beat = seq.parse_position_to_beats(args[1])
            if start_beat is None or end_beat is None:
                return True, "Invalid position."

            if end_beat <= start_beat:
                return True, "Error: End position must be after the start position."

            # Set the play range and disable looping to avoid conflict
            seq.play_range_start_beat = start_beat
            seq.play_range_end_beat = end_beat
            seq.play_range_enabled = True
            output = ""
            if seq.loop_enabled:
                seq.loop_enabled = False
                output += "Looping disabled to allow play range.\n"

            output += f"Set to stop at {args[1]}."
            seq.play(start_beat=start_beat)
            return True, output
        else:
            return True, "Usage: play [start_position] [end_position]\nExample: play 10:1 15:1"
    elif command == "pause":
        seq.pause()
        return True, "Toggled pause."
    elif command == "loop":
        if len(args) == 0:
            seq.loop_enabled = not seq.loop_enabled
            status = "enabled" if seq.loop_enabled else "disabled"
            output = ""
            if seq.loop_enabled and seq.play_range_enabled:
                output += "Disabling play range to enable looping.\n"
                seq.play_range_enabled = False
            output += f"Looping is now {status}."
            if not seq.loop_enabled:
                output += "\nNote: Loop points are still saved. Use 'loop <start> <end>' to set new points."
            elif seq.loop_end_beat <= seq.loop_start_beat:
                output += "\nWarning: Loop end is not after loop start. The loop will not function correctly."
            return True, output
        elif len(args) == 2:
            start_beat = seq.parse_position_to_beats(args[0])
            end_beat = seq.parse_position_to_beats(args[1])
            if start_beat is None or end_beat is None:
                return True, "Invalid position."

            if end_beat <= start_beat:
                return True, "Error: Loop end position must be after the start position."

            seq.loop_start_beat = start_beat
            seq.loop_end_beat = end_beat
            seq.loop_enabled = True
            output = ""
            if seq.play_range_enabled:
                output += "Disabling play range to enable looping.\n"
                seq.play_range_enabled = False
            output += f"Loop enabled from {args[0]} to {args[1]}."
            # Also start playback from the beginning of the loop
            seq.play(start_beat=start_beat)
            return True, output
        else:
            return True, "Usage: loop [start_position] [end_position]\nExample: loop 1:1 5:1"
    elif command == "stop":
        seq.stop()
        return True, "Stopped."
    elif command == "seek":
        if len(args) == 1:
            return True, seq.seek(args[0])
        else:
            return True, "Usage: seek <amount> (e.g., +1m, -4b)"
    elif command == "metronome":
        if len(args) == 1 and args[0].lower() in ["on", "off"]:
            is_enabled = args[0].lower() == "on"
            seq.song.metronome_enabled = is_enabled
            status = "enabled" if is_enabled else "disabled"
            if is_enabled and seq.playback_state != "stopped":
                seq.start_metronome()
            return True, f"Metronome is now {status}."
        else:
            return True, "Usage: metronome <on|off>"
    elif command == "setcontrolport":
        if len(args) == 1:
            try:
                port_index = int(args[0])
                input_ports = mido.get_input_names()
                if 0 <= port_index < len(input_ports):
                    port_name = input_ports[port_index]
                    return True, seq.set_control_port(port_name)
                else:
                    return True, "Error: Invalid port index."
            except (ValueError, IndexError):
                return True, "Error: Invalid input."
        else:
            return True, "Usage: setcontrolport <port_index>"
    elif command == "unsetcontrolport":
        return True, seq.unset_control_port()
    elif command == "map":
        if len(args) == 4:
            try:
                channel = int(args[0]) - 1 # to 0-indexed
                control = int(args[1])
                track_index = int(args[2])
                action = args[3].lower()

                if not 0 <= channel <= 15:
                    return True, "Error: Channel must be between 1 and 16."
                if not 0 <= control <= 127:
                    return True, "Error: CC number must be between 0 and 127."
                if not 0 <= track_index < len(seq.song.tracks):
                    return True, "Error: Invalid track index."

                valid_actions = ['volume', 'pan', 'program']
                if action not in valid_actions:
                    return True, f"Error: Invalid action. Must be one of {valid_actions}."

                from sequencer.models import MidiMapping
                mapping = MidiMapping(channel=channel, control=control, track_index=track_index, action=action)

                # Remove any existing mapping for this channel/cc
                seq.song.midi_mappings = [m for m in seq.song.midi_mappings if not (m.channel == channel and m.control == control)]
                seq.song.midi_mappings.append(mapping)
                seq.is_dirty = True
                return True, f"Mapped Ch:{channel+1} CC:{control} to {action} on track {track_index}."

            except ValueError:
                return True, "Error: Invalid number for channel, CC, or track index."
        else:
            return True, "Usage: map <channel> <cc> <track_index> <action>"
    elif command == "unmap":
        if len(args) == 2:
            try:
                channel = int(args[0]) - 1 # to 0-indexed
                control = int(args[1])

                initial_len = len(seq.song.midi_mappings)
                seq.song.midi_mappings = [m for m in seq.song.midi_mappings if not (m.channel == channel and m.control == control)]
                if len(seq.song.midi_mappings) < initial_len:
                    seq.is_dirty = True
                    return True, f"Unmapped Ch:{channel+1} CC:{control}."
                else:
                    return True, "Mapping not found."

            except ValueError:
                return True, "Error: Invalid number for channel or CC."
        else:
            return True, "Usage: unmap <channel> <cc>"
    elif command == "listmaps":
        if not seq.song.midi_mappings:
            return True, "No MIDI mappings defined."

        output = "Active MIDI Mappings:\n"
        for m in seq.song.midi_mappings:
            output += f"  Ch:{m.channel+1} CC:{m.control} -> Track {m.track_index} {m.action.capitalize()}\n"
        return True, output

    else:
        return True, f"Unknown command: '{command}'. Type 'help' for a list of commands."
    return True, ""

def cli_main_loop():
    """The main entry point for the CLI application."""
    print("Welcome to the Python MIDI Sequencer!")
    seq = Sequencer()
    print(get_help_text())

    command_buffer = ""
    print("> ", end="", flush=True)

    def cli_confirmation_handler(prompt):
        return input(prompt)

    while True:
        try:
            char = get_char()

            if char == 'ARROW_LEFT':
                print() # Move to a new line to not mess up the current command line
                _, output = process_command("seek -1m", seq, confirmation_handler=cli_confirmation_handler)
                if output:
                    print(output)
                command_buffer = "" # Clear buffer after action
                print(f"> ", end="", flush=True)
                continue
            elif char == 'ARROW_RIGHT':
                print() # Move to a new line to not mess up the current command line
                _, output = process_command("seek +1m", seq, confirmation_handler=cli_confirmation_handler)
                if output:
                    print(output)
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
                    _, output = process_command("pause", seq, confirmation_handler=cli_confirmation_handler)
                    if output:
                        print(output)
                    command_buffer = "" # Clear buffer after pausing
                    print(f"> ", end="", flush=True)
                else:
                    # Otherwise, it's a normal character (part of a command)
                    command_buffer += ' '
                    print(' ', end="", flush=True)
                continue

            elif char in ('\r', '\n'):
                print()  # Move to the next line
                should_continue, output = process_command(command_buffer, seq, confirmation_handler=cli_confirmation_handler)
                if output:
                    print(output)
                if not should_continue:
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

def api_main_loop():
    """The main entry point for the API mode."""
    seq = Sequencer()
    for line in sys.stdin:
        command = line.strip()
        if command:
            should_continue, output = process_command(command, seq, api_mode=True)
            if output:
                print(output)
            if not should_continue:
                break

def main():
    """The main entry point for the application."""
    if '--gui' in sys.argv:
        from sequencer.kivy_ui import SequencerApp
        SequencerApp().run()
    elif '--api' in sys.argv:
        api_main_loop()
    else:
        cli_main_loop()

if __name__ == "__main__":
    if '--help' in sys.argv or '-h' in sys.argv:
        print_help()
        sys.exit(0)
    main()