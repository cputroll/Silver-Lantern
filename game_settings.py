"""The game's own settings file (hookshot_options.ini): one reader/writer for the launcher, the CLI and the preset.

The option table mirrors the engine's menu table in bin/hookshot_vk.dll (HeyListen v0.4.0, 64 rows of 0x48 bytes).
The engine reads a row's ini key as the row's label in lower case with spaces turned into '_', except four rows it
names itself: output_res (Output resolution), dlss (Upscaler), resolution (Render resolution) and upscaling
(Window scaling filter). Values are integers clamped to the row's range; pad_/key_ lines are bindings; the game
writes options_version=3 and microphone_device=<name> itself. See docs/graphics-options.md.
"""
import os
import subprocess
from pathlib import Path

INI_NAME = 'hookshot_options.ini'
DLSS_DLL = Path('ngx') / 'nvngx_dlss.dll'
OFF_ON = ['Off', 'On']
# Keys older presets wrote; the engine never read them (its keys are the label-derived names on the right).
LEGACY_KEYS = {'framegen': 'frame_generation', 'pace': 'frame_pacing', 'reflex': 'nvidia_reflex'}
GROUPS = ('Upscaling', 'Display', 'Performance', 'Interface', 'Gameplay', 'Controls', 'Motion', 'Advanced')


class Option:
    """One row: `choices` is a label list (value = index) or a (low, high, step, format) range."""

    def __init__(self, key, label, group, choices, default=0, desc='', restart=False):
        self.key, self.label, self.group, self.choices = key, label, group, choices
        self.default, self.desc, self.restart = default, desc, restart

    @property
    def ranged(self):
        return isinstance(self.choices, tuple)

    @property
    def low(self):
        return self.choices[0] if self.ranged else 0

    @property
    def high(self):
        return self.choices[1] if self.ranged else len(self.choices) - 1

    def values(self):
        if self.ranged:
            low, high, step, _ = self.choices
            return list(range(low, high + 1, step))
        return list(range(len(self.choices)))

    def clamp(self, value):
        return max(self.low, min(self.high, int(value)))

    def text(self, value):
        if self.ranged:
            fmt = self.choices[3]
            return fmt(value) if callable(fmt) else fmt.format(value)
        return self.choices[self.clamp(value)]


def _pct(v):
    return 'Off' if v == 0 else f'{v}%'


OPTIONS = [
    Option('quality_preset', 'Quality preset', 'Upscaling', ['Custom', 'Minimum (4GB)', 'Low (6GB)', 'Balanced (8GB)', 'High (12GB)', 'Ultra (16GB+)'], 0,
           'Sets the scaling rows for a GPU with that much memory. The Upscaler and Frame generation stay yours.'),
    Option('dlss', 'Upscaler', 'Upscaling', ["Game's own", 'NVIDIA DLSS', 'NVIDIA DLAA', 'AMD FSR 3.1', 'AMD FSR 4', 'AMD FSR 4 Native AA'], 0,
           'What scales the 3D up to the Output resolution. DLSS and DLAA need an NVIDIA RTX GPU.'),
    Option('resolution', 'Render resolution', 'Upscaling', ['Auto (dynamic)', '100%', '75%', '67% (Quality)', '50% (Performance)', '58% (Balanced)', '33% (Ultra Performance)'], 0,
           'The share of the output the 3D is drawn at; the Upscaler fills in the rest.'),
    Option('dlss_model', 'DLSS model', 'Upscaling', ['Transformer (K)', 'Transformer 2 (M)', 'Transformer 2 (L)', 'NVIDIA default'], 0,
           "Which of NVIDIA's DLSS networks draws the picture."),
    Option('sharpness', 'Sharpness', 'Upscaling', (0, 100, 10, _pct), 0,
           'Sharpens the picture as it goes to the window. DLSS and DLAA suit around 80%.'),
    Option('frame_generation', 'Frame generation', 'Upscaling', ['Off', 'Auto'] + [f'{n}x' for n in range(2, 9)], 0,
           "Makes frames between the game's own (AMD FSR 3.1 frame interpolation), for smoother motion at a little input delay."),
    Option('fullscreen', 'Fullscreen', 'Display', OFF_ON, 0, "Fills the whole screen without the window's border. The same as F11."),
    Option('output_res', 'Output resolution', 'Display', ["Game's own", '2560x1440', '3840x2160', '1920x1080', '5120x1440'], 0,
           "The game's whole frame, HUD included, before it is scaled to the window. Docked only.", True),
    Option('upscaling', 'Window scaling filter', 'Display', ['Plain', 'Catmull-Rom', 'Lanczos'], 0,
           'How the finished picture is scaled to the window when its size differs from the Output resolution.'),
    Option('ultrawide', 'Aspect ratio (Ultrawide)', 'Display', ['16:9', "Monitor's shape"], 0,
           "The camera's shape on a screen wider or narrower than 16:9. The HUD is still stretched.", True),
    Option('handheld_profile', 'Handheld profile', 'Display', OFF_ON, 0, 'Runs the game as if the console were undocked: lower resolutions.', True),
    Option('hdr', 'HDR', 'Display', ['Off', 'Expanded', "Native (the game's)"], 0, 'HDR10 out on an HDR display (Windows: Use HDR on).'),
    Option('hdr_paper_white', 'HDR paper white', 'Display', (80, 400, 10, '{} nits'), 200, "How bright the picture's white is with HDR on."),
    Option('hdr_peak_brightness', 'HDR peak brightness', 'Display', (400, 4000, 100, '{} nits'), 1000, "Set it to your display's peak brightness."),
    Option('frame_rate_limit', 'Frame rate limit', 'Performance', ['60', '90', '120', '144', '165', '240'], 0,
           'Above 60 it needs Game speed on Real time, and a display that fast.'),
    Option('frame_pacing', 'Frame pacing (V-Sync)', 'Performance', OFF_ON, 0, 'On: frames at an even interval, less stutter, up to one frame of delay.'),
    Option('nvidia_reflex', 'NVIDIA Reflex', 'Performance', ['Off', 'On', 'On + Boost'], 0, 'NVIDIA GPUs: less input delay. Not used with frame generation.'),
    Option('gpu_device', 'GPU device', 'Performance', ['Auto (best dedicated GPU)'] + [f'Device {n}' for n in range(1, 9)], 0,
           'Which graphics card the game runs on.', True),
    Option('robust_buffers', 'Robust buffers', 'Performance', ['Auto', 'Off', 'On'], 0, "Bounds-checked buffer reads. Off can be faster on some NVIDIA cards.", True),
    Option('fps_counter', 'FPS counter', 'Interface', ['Off', 'FPS', 'Full'], 0, 'Frame rate in the corner, or a full GPU/CPU panel. F10 cycles it in game.'),
    Option('menu_size', 'Menu size', 'Interface', ['Auto', 'Small', 'Medium', 'Large'], 0, "How large the game's own menu is drawn."),
    Option('menu_shortcut', 'Menu shortcut', 'Interface', ['Insert or F1', 'Insert only', 'F1 only'], 0, "Which key opens the game's own settings menu. To exit the game from that menu: its Quit row (the last row, under Restart), A or Enter twice."),
    Option('game_speed', 'Game speed', 'Gameplay', ['Per frame', 'Real time'], 1, 'How the game keeps time when it runs below 60 fps.'),
    Option('text_speed', 'Text speed', 'Gameplay', ['Normal', 'Twice as fast', 'Four times as fast', 'Instant'], 0, 'How fast dialogue letters appear.'),
    Option('audio_output', 'Audio output', 'Gameplay', ['Auto', 'Stereo', '5.1'], 0, 'The speakers the game mixes its sound for.', True),
    Option('skip_title_screen', 'Skip title screen', 'Gameplay', OFF_ON, 0, 'Starts with the most recent saved game.', True),
    Option('discord_status', 'Discord status', 'Gameplay', OFF_ON, 1, 'Shows the game in your Discord status.'),
    Option('controller_in_background', 'Controller in background', 'Controls', OFF_ON, 0, 'Whether the controller plays while another window has the focus.'),
    Option('mouse_camera', 'Mouse camera', 'Controls', ['As the stick', 'Native'], 0, 'How the captured mouse turns the camera.'),
    Option('mouse_sensitivity', 'Mouse sensitivity', 'Controls', (10, 500, 10, lambda v: f'{v / 100:.2f}'), 100, 'How far the camera turns for a mouse movement.'),
    Option('invert_mouse_vertical', 'Invert mouse vertical', 'Controls', OFF_ON, 0, 'Swaps up and down for the mouse.'),
    Option('invert_camera_x', 'Invert camera X', 'Controls', OFF_ON, 0, 'Inverts left/right look on the right stick.'),
    Option('invert_camera_y', 'Invert camera Y', 'Controls', OFF_ON, 0, 'Inverts up/down look on the right stick.'),
    Option('stick_dead_zone', 'Stick dead zone', 'Controls', ['Default (24%)', '5%', '10%', '15%', '20%', '30%'], 0, 'How far a stick moves before the game sees it.'),
    Option('rumble_strength', 'Rumble strength', 'Controls', (0, 150, 25, _pct), 100, "Percent of the game's rumble."),
    Option('layout_preset', 'Layout preset', 'Controls', ['Nintendo (Def)', 'Xbox / PC', 'Swap Face', 'Custom'], 0, 'Which face button is which.'),
    Option('button_prompts', 'Button prompts', 'Controls', ['Auto', 'Switch', 'Xbox', 'PlayStation 4', 'PlayStation 5', 'Steam Deck'], 0, 'The button icons the game shows.', True),
    Option('motion_controls', 'Motion controls', 'Motion', OFF_ON, 1, "Aims with the controller's gyroscope where the game uses motion."),
    Option('motion_sensitivity_x', 'Motion sensitivity X', 'Motion', (10, 600, 10, '{}%'), 100, "100% is the console's own."),
    Option('motion_sensitivity_y', 'Motion sensitivity Y', 'Motion', (10, 300, 10, '{}%'), 100, "100% is the console's own."),
    Option('invert_motion_x', 'Invert motion X', 'Motion', OFF_ON, 0, "Turns the motion aim's left and right around."),
    Option('invert_motion_y', 'Invert motion Y', 'Motion', OFF_ON, 0, "Turns the motion aim's up and down around."),
    Option('motion_deadzone', 'Motion deadzone', 'Motion', ['Low', 'Standard', 'High'], 1, 'How slow a turn counts as none.'),
    Option('debug_tools', 'Debug tools', 'Advanced', OFF_ON, 0, 'Testing tools in the game menu: flight, free camera, teleport.'),
    Option('descriptor_reuse', 'Descriptor reuse', 'Advanced', OFF_ON, 0, 'Fewer descriptor rebuilds while textures stream.', True),
    Option('sampler_cache', 'Sampler cache', 'Advanced', OFF_ON, 0, 'Sampler lookups without a lock while textures stream.', True),
    Option('intel_shader_compiles', 'Intel shader compiles', 'Advanced', ['One at a time', 'Side by side'], 0, 'Intel graphics only: side by side is faster but can crash.', True),
]
BY_KEY = {o.key: o for o in OPTIONS}

_V043_CHANGED = {
    'dlss': [*BY_KEY['dlss'].choices, 'Off'],
    'resolution': [*BY_KEY['resolution'].choices, 'Native AA (DLAA / FSR 4)'],
    'dlss_model': ['DLSS 4 (K)', 'DLSS 4.5 (M)', 'DLSS 4.5 (L)', 'NVIDIA default', 'DLSS 3 (CNN, E)'],
    'frame_rate_limit': [*BY_KEY['frame_rate_limit'].choices, 'Unlimited'],
    'button_prompts': [*BY_KEY['button_prompts'].choices, 'Keyboard'],
}
OPTIONS_043 = [Option(o.key, 'Frame pacing' if o.key == 'frame_pacing' else o.label, o.group,
                      _V043_CHANGED.get(o.key, o.choices), o.default, o.desc, o.restart) for o in OPTIONS]
OPTIONS_043 += [
    Option('frame_generation_by', 'Frame generation by', 'Upscaling', ['Built-in', 'NVIDIA DLSS FG', 'AMD FSR 3.1 FG']),
    Option('dlss_pre_bloom', 'DLAA before the bloom', 'Upscaling', OFF_ON),
    Option('vsync', 'V-Sync', 'Display', OFF_ON),
    Option('vsync_mode', 'V-Sync mode', 'Display', ['FIFO double', 'FIFO triple', 'Mailbox'], 1),
    Option('vrr', 'VRR (G-Sync / FreeSync)', 'Display', ['Off', 'On', 'Auto']),
    Option('hdr_contrast', 'HDR contrast', 'Display', (50, 200, 5, '{}%'), 100),
    Option('hdr_tone_mapper', 'HDR tone mapper', 'Display', ['Vanilla', 'PsychoV35']),
    Option('psychov35_ui_brightness', 'PsychoV35 UI brightness', 'Display', (80, 500, 1, '{} nits'), 203),
    Option('psychov35_color_space', 'PsychoV35 color space', 'Display', ['Vanilla', 'BT.709', 'DCI-P3'], 1),
    Option('psychov35_midgray', 'PsychoV35 midgray', 'Display', ['Render', 'Vanilla'], 1),
    Option('psychov35_match_tone_slope', 'PsychoV35 match tone slope', 'Display', OFF_ON, 1),
    Option('psychov35_exposure', 'PsychoV35 exposure', 'Display', (0, 200, 5, '{}%'), 100),
    *(Option('psychov35_' + label.lower().replace(' ', '_'), 'PsychoV35 ' + label,
             'Display', (0, 100, 1, '{}'), 50) for label in
      ('tone slope', 'hue clip emulation', 'highlights', 'shadows', 'contrast', 'saturation', 'bloom')),
    Option('hud_layout', 'HUD layout', 'Interface', ['Screen edges', 'Middle 16:9']),
    Option('update_check', 'In-game update check', 'Advanced', OFF_ON, 1,
           'Controls the game’s standalone update prompt, not launcher updates.', True),
]
_frame_provider = OPTIONS_043.pop(next(i for i, o in enumerate(OPTIONS_043) if o.key == 'frame_generation_by'))
OPTIONS_043.insert(next(i for i, o in enumerate(OPTIONS_043) if o.key == 'frame_generation'), _frame_provider)
OPTIONS_043.sort(key=lambda o: GROUPS.index(o.group))
BY_KEY_043 = {o.key: o for o in OPTIONS_043}


def options_for(folder):
    try:
        lines = (Path(folder) / 'hookshot_version.txt').read_text(encoding='utf-8-sig').splitlines()
    except (OSError, UnicodeError):
        return OPTIONS
    return OPTIONS_043 if [s for s in lines if s.startswith('version=')] == ['version=v0.4.3'] else OPTIONS

# Rows whose manual change returns Quality preset to Custom, and what each preset sets (from the game's own texts).
SCALING_ROWS = ('resolution', 'upscaling', 'sharpness', 'handheld_profile')
QUALITY_ROWS = {1: (4, 2, 30, 1), 2: (2, 2, 30, 1), 3: (1, 1, 30, 0), 4: (1, 2, 40, 0), 5: (1, 2, 50, 0)}

PRESET_DLSS = {'fullscreen': '1', 'quality_preset': '0', 'dlss': '1', 'resolution': '4', 'sharpness': '80',
               'nvidia_reflex': '1', 'frame_pacing': '1', 'frame_generation': '1', 'options_version': '3'}
PRESET_FSR = dict(PRESET_DLSS, dlss='3', nvidia_reflex='0')
XBOX_DEFAULTS = {'layout_preset': '1', 'button_prompts': '2'}
GPU_QUERY = ('Get-CimInstance Win32_VideoController | Sort-Object AdapterRAM -Descending | '
             'Select-Object -First 1 -ExpandProperty Name')
OUTPUT_SIZES = {'2560x1440': 1, '3840x2160': 2, '1920x1080': 3, '5120x1440': 4}


def ini_path(folder):
    return Path(folder) / INI_NAME


def dlss_available(folder):
    try:
        return (Path(folder) / DLSS_DLL).is_file()
    except OSError:
        return False


def parse_value(key, raw, schema=BY_KEY):
    raw = raw.strip()
    if key == 'output_res' and 'x' in raw:
        return OUTPUT_SIZES.get(raw.replace(' ', ''), 0)
    try:
        return schema[key].clamp(int(raw))
    except ValueError:
        return None


def read_lines(folder):
    path = ini_path(folder)
    return path.read_text(encoding='ascii', errors='replace').splitlines() if path.exists() else []


def read_values(folder):
    """What the game will use: every option's value (its default when absent or unreadable)."""
    options = options_for(folder)
    schema = BY_KEY_043 if options is OPTIONS_043 else BY_KEY
    values = {o.key: o.default for o in options}
    raw_values = {}
    for line in read_lines(folder):
        key, eq, raw = line.partition('=')
        key = key.strip()
        if eq and key in ('options_version', 'dynamic_resolution'):
            raw_values[key] = raw.strip()
        if eq and key in schema:
            raw_values[key] = raw.strip()
            value = parse_value(key, raw, schema)
            if value is not None:
                values[key] = value
    if schema is BY_KEY_043:
        if 'options_version' not in raw_values:
            values['game_speed'] = 1
        if 'resolution' not in raw_values and raw_values.get('dynamic_resolution') == '0':
            values['resolution'] = 1
        if values['dlss'] in (2, 5):
            values['dlss'], values['resolution'] = (1 if values['dlss'] == 2 else 4), 7
        if 'vsync_mode' not in raw_values and raw_values.get('vsync') == '2':
            values['vsync_mode'] = 1
        if raw_values.get('hdr') == '3':
            values['hdr'], values['hdr_tone_mapper'] = 2, 1
    return values


def merge_lines(lines, wanted, drop=LEGACY_KEYS):
    """Replace owned keys in place (first occurrence), drop duplicates and dead keys, keep every other line."""
    out, seen = [], set()
    for line in lines:
        eq = line.find('=')
        key = line[:eq].strip() if eq > 0 else None
        if key in wanted:
            if key not in seen:
                out.append(f'{key}={wanted[key]}')
                seen.add(key)
        elif key is None or key not in drop:
            out.append(line)
    out.extend(f'{k}={v}' for k, v in wanted.items() if k not in seen)
    return out


def write_values(folder, wanted, *, drop=LEGACY_KEYS):
    """Merge `wanted` into the ini atomically (temp file + replace). Returns the ini path."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = ini_path(folder)
    wanted = {k: str(v) for k, v in wanted.items()}
    lines = read_lines(folder)
    if options_for(folder) is OPTIONS_043:
        saved_keys = {key.strip() for key, sep, _ in (line.partition('=') for line in lines) if sep}
        if 'options_version' not in saved_keys and 'game_speed' in saved_keys:
            wanted.setdefault('game_speed', '1')
    wanted.setdefault('options_version', '3')
    text = '\n'.join(merge_lines(lines, wanted, drop)) + '\n'
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text, encoding='ascii', errors='replace')
    os.replace(tmp, path)
    return path


def lock_reason(key, values):
    """Why a row has no effect with the other rows as they are (the engine's own rules), or None."""
    up = values.get('dlss', 0)
    if 'frame_generation_by' in values:
        hdr = values.get('hdr', 0)
        psycho = hdr == 2 and values.get('hdr_tone_mapper') == 1
        if key == 'vsync_mode' and not values.get('vsync'):
            return 'Needs V-Sync on'
        if key == 'dlss_pre_bloom' and (up, values.get('resolution')) != (1, 7):
            return 'Needs DLAA'
        if key == 'hdr_tone_mapper' and hdr != 2:
            return 'Needs Native HDR'
        if key.startswith('psychov35_') and not psycho:
            return 'Needs Native HDR + PsychoV35'
        if key == 'hdr_contrast' and (not hdr or psycho):
            return 'Uses PsychoV35 contrast' if psycho else 'Needs HDR on'
    if key == 'dlss_model' and up not in (1, 2):
        return 'Used by DLSS and DLAA only'
    if key == 'resolution' and up in (2, 5):
        return 'Full size with ' + ('DLAA' if up == 2 else 'FSR 4 Native AA')
    if key == 'nvidia_reflex' and values.get('frame_generation', 0):
        return 'Not used with frame generation'
    if key in ('hdr_paper_white', 'hdr_peak_brightness') and not values.get('hdr', 0):
        return 'Needs HDR on'
    return None


def resolution_steps(upscaler):
    if upscaler in (1, 2, 4, 5):
        return [0, 7, 3, 5, 4, 6]
    if upscaler == 3:
        return [0, 3, 5, 4, 6]
    return [0, 1, 2, 3, 4]


def snap_resolution(upscaler, value):
    steps = resolution_steps(upscaler)
    if value in steps:
        return value
    percent = (0, 100, 75, 67, 50, 58, 33, 100)
    return min((v for v in steps if v not in (0, 7)),
               key=lambda v: (abs(percent[v] - percent[value]), -percent[v]))


def display_value(option, value, values, schema=BY_KEY):
    if schema is BY_KEY_043 and option.key == 'resolution':
        upscaler = values.get('dlss', 0)
        value = snap_resolution(upscaler, option.clamp(value))
        if value == 7:
            return 'DLAA (native)' if upscaler in (1, 2) else 'Native AA (FSR 4)'
    return option.text(value)


def allowed(key, dlss_ok=True, *, schema=BY_KEY, current=None, dlss_fg_max=0):
    values = schema[key].values()
    if schema is BY_KEY_043:
        current = current or {}
        if key == 'dlss':
            return [v for v in values if v not in (2, 5) and (dlss_ok or v != 1)]
        if key == 'resolution':
            return resolution_steps(current.get('dlss', 0))
        if key == 'frame_generation_by' and not dlss_fg_max:
            return [0, 2]
        if key == 'frame_generation':
            provider = current.get('frame_generation_by', 0)
            if provider == 2:
                return [0, 2]
            if provider == 1:
                return [0, *range(2, max(2, min(8, dlss_fg_max)) + 1)]
    return [v for v in values if v not in (1, 2)] if key == 'dlss' and not dlss_ok else values


def cycle(key, value, direction=1, dlss_ok=True, **context):
    """The next allowed value in `direction`, wrapping around."""
    values = allowed(key, dlss_ok, **context)
    if value not in values:
        return values[0] if direction > 0 else values[-1]
    return values[(values.index(value) + direction) % len(values)]


def change(values, key, value, schema=BY_KEY):
    """The rows to write for one change, with the game's linked rows (Quality preset <-> scaling rows)."""
    value = schema[key].clamp(value)
    out = {key: value}
    if schema is BY_KEY_043 and key == 'frame_generation_by' and value in (1, 2):
        multiplier = values.get('frame_generation', 0)
        if multiplier:
            out['frame_generation'] = 2 if value == 2 or multiplier == 1 else multiplier
    resets_preset = key in SCALING_ROWS or (schema is BY_KEY_043 and key in ('dlss', 'frame_pacing'))
    if resets_preset and values.get('quality_preset', 0):
        out['quality_preset'] = 0
    if key == 'quality_preset' and value in QUALITY_ROWS:
        out.update(zip(SCALING_ROWS, QUALITY_ROWS[value]))
        if schema is BY_KEY_043:
            out.update(resolution=snap_resolution(values.get('dlss', 0), out['resolution']),
                       frame_pacing=1, game_speed=1)
    return out


def set_value(folder, key, value, values=None):
    values = dict(values or read_values(folder))
    schema = BY_KEY_043 if options_for(folder) is OPTIONS_043 else BY_KEY
    updates = change(values, key, value, schema)
    if schema is BY_KEY_043:
        for pair in (('dlss', 'resolution'), ('hdr', 'hdr_tone_mapper')):
            if any(name in updates for name in pair):
                for name in pair:
                    updates.setdefault(name, values.get(name, schema[name].default))
    write_values(folder, updates)
    values.update(updates)
    return values


def reset_defaults(folder):
    options = options_for(folder)
    skip = set(XBOX_DEFAULTS)
    if options is OPTIONS_043:
        skip.add('dlss_model')
        write_values(folder, {o.key: o.default for o in options if o.key not in skip},
                     drop={*LEGACY_KEYS, 'dlss_model'})
    else:
        write_values(folder, {o.key: o.default for o in options if o.key not in skip})
    return read_values(folder)


def is_dlss_gpu(name):
    n = (name or '').upper()
    return 'NVIDIA' in n and 'RTX' in n


def detect_gpu_name():
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', GPU_QUERY],
                       capture_output=True, text=True, timeout=30, creationflags=flags)
    name = (r.stdout or '').strip().splitlines()
    return name[0].strip() if r.returncode == 0 and name and name[0].strip() else None


def unset_xbox_defaults(folder):
    """Return only the Xbox layout/prompt defaults that are not explicitly saved in hookshot_options.ini."""
    schema = BY_KEY_043 if options_for(folder) is OPTIONS_043 else BY_KEY
    saved = set()
    for line in read_lines(folder):
        key, eq, raw = line.partition('=')
        key = key.strip()
        if eq and key in XBOX_DEFAULTS and parse_value(key, raw, schema) is not None:
            saved.add(key)
    return {k: v for k, v in XBOX_DEFAULTS.items() if k not in saved}


def apply_xbox_defaults(folder):
    """Write Xbox layout/prompt defaults only for keys not already saved. Intended for fresh installs."""
    missing = unset_xbox_defaults(folder)
    return write_values(folder, missing) if missing else ini_path(folder)


def preset_for(folder, preset, *, fresh_install=False):
    out = dict(preset, frame_generation_by='0') if options_for(folder) is OPTIONS_043 else preset
    if not fresh_install:
        return out
    missing = {k: v for k, v in unset_xbox_defaults(folder).items() if k not in out}
    return {**out, **missing} if missing else out


def choose_preset(folder, detect=None):
    """The DLSS or FSR 3.1 preset for this PC and build, with the reason. Never raises."""
    try:
        gpu = (detect or detect_gpu_name)()
    except Exception as e:
        return preset_for(folder, PRESET_FSR), f'GPU detection failed ({e.__class__.__name__}) -> FSR 3.1 Performance'
    if gpu is None:
        return preset_for(folder, PRESET_FSR), 'GPU could not be identified -> FSR 3.1 Performance'
    has_dll = dlss_available(folder)
    if is_dlss_gpu(gpu) and has_dll:
        return preset_for(folder, PRESET_DLSS), f'NVIDIA RTX with DLSS build ({gpu}) -> DLSS Performance'
    if is_dlss_gpu(gpu):
        return preset_for(folder, PRESET_FSR), f'No RTX/DLSS build ({gpu}, but {DLSS_DLL} missing) -> FSR 3.1 Performance'
    return preset_for(folder, PRESET_FSR), f'No RTX/DLSS build ({gpu}) -> FSR 3.1 Performance'


def describe(folder):
    """Machine-readable view for --graphics."""
    folder = Path(folder)
    values = read_values(folder)
    dlss_ok = dlss_available(folder)
    legacy = sorted(k for k in (line.partition('=')[0].strip() for line in read_lines(folder)) if k in LEGACY_KEYS)
    rows = []
    options = options_for(folder)
    schema = BY_KEY_043 if options is OPTIONS_043 else BY_KEY
    for o in options:
        v = values[o.key]
        rows.append({'key': o.key, 'label': o.label, 'group': o.group, 'value': v,
                     'text': display_value(o, v, values, schema),
                     'default': o.default, 'min': o.low, 'max': o.high,
                     'allowed': allowed(o.key, dlss_ok, schema=schema, current=values),
                     'choices': None if o.ranged else list(o.choices), 'restart': o.restart,
                     'locked': lock_reason(o.key, values)})
    return {'ini': str(ini_path(folder)), 'exists': ini_path(folder).exists(), 'dlss_available': dlss_ok,
            'ignored_legacy_keys': legacy, 'options': rows}
