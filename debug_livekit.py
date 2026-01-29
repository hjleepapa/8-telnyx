path = 'convonet/livekit_audio_bridge.py'
with open(path, 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if 'print(f"🔎 LiveKit ensure subscribe: no publications for {participant.identity}' in line:
        # Add deep introspection here
        indent = line[:line.find('print')]
        new_lines.append(f'{indent}try:\n')
        new_lines.append(f'{indent}    print(f"🕵️ DEBUG {participant.identity} keys: {{list(participant.__dict__.keys()) if hasattr(participant, \"__dict__\") else \"no __dict__\"}}", flush=True)\n')
        new_lines.append(f'{indent}    # Try to find anything track-related\n')
        new_lines.append(f'{indent}    all_attrs = dir(participant)\n')
        new_lines.append(f'{indent}    for a in all_attrs:\n')
        new_lines.append(f'{indent}        if "track" in a.lower() or "pub" in a.lower():\n')
        new_lines.append(f'{indent}            val = getattr(participant, a, "ERROR")\n')
        new_lines.append(f'{indent}            print(f"🕵️ DEBUG {participant.identity} attr {{a}} = {{val}} type={{type(val)}}", flush=True)\n')
        new_lines.append(f'{indent}except Exception as e:\n')
        new_lines.append(f'{indent}    print(f"🕵️ DEBUG failed: {{e}}", flush=True)\n')
    new_lines.append(line)

with open(path, 'w') as f:
    f.writelines(new_lines)
