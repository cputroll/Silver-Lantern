# Game options (hookshot_options.ini)

Every option the game (HeyListen v0.4.0, `bin\hookshot_vk.dll`) reads from its settings file, recovered from the
engine's own 64-row menu table (labels, ranges, steps, defaults and value texts) and its ini parser. Silver Lantern's
launcher (Settings -> Graphics), `--graphics` and the presets all use one module for this: `game_settings.py`.

## The file

- Path: `<game folder>\hookshot_options.ini` (e.g. `C:\Users\<you>\Games\HeyListen\hookshot_options.ini`). Plain `key=value` lines.
- Key name: the menu row's label in lower case with spaces turned into `_` (e.g. *Frame generation* -> `frame_generation`),
  except four rows the engine names itself: `output_res` (Output resolution), `dlss` (Upscaler), `resolution` (Render resolution),
  `upscaling` (Window scaling filter).
- Values are integers; the engine clamps each to the row's range. `output_res` also accepts `WIDTHxHEIGHT`.
- The game also writes `options_version=3`, `microphone_device=<name>`, and `pad_<button>=<n>` / `key_<button>=<k>,<k>` bindings. Silver Lantern keeps those lines untouched.
- **Bug fixed in v1.6.0:** presets up to v1.5.4 wrote `framegen`, `pace` and `reflex`. The engine has no such keys, so frame
  generation, frame pacing and Reflex were never actually turned on. The presets now write `frame_generation`, `frame_pacing`,
  `nvidia_reflex`, and remove the dead keys.
- Other user-editable files: `language.txt` (game language code, documented inside the file). Environment variables
  (`RX_VK_*`, `HOS_*`, e.g. `RX_VK_DLSS_PRESET`, `HOS_FULLSCREEN`) override some rows for one run; they are debugging aids, not settings.

## Opening the game's own menu

- **F1 or Insert** opens and closes the in-game settings menu (default; the *Menu shortcut* row can limit it to one key).
- **F11** toggles fullscreen, **F10** cycles the FPS counter, **-** / **=** change mouse sensitivity in game.
- No gamepad button for the menu was found in the engine; inside the menu the D-pad moves, A changes, B goes back.
- Nothing Silver Lantern does blocks it: the launcher starts `HeyListen.exe` with no flags, and no preset writes `menu_shortcut`.

## Options (47)

"This PC" is the value the game uses on the machine this was written on (from `hookshot_options.ini` there: `dlss=1`, `resolution=4`, `sharpness=80`, `reflex=1`, `pace=1`, `framegen=1`, `options_version=3`).

### Upscaling

| Key | Menu row | Values | Default | This PC | What it does |
|---|---|---|---|---|---|
| `quality_preset` | Quality preset | 0 Custom, 1 Minimum (4GB), 2 Low (6GB), 3 Balanced (8GB), 4 High (12GB), 5 Ultra (16GB+) | Custom | Custom | Sets the scaling rows for a GPU with that much memory. The Upscaler and Frame generation stay yours. |
| `dlss` | Upscaler | 0 Game's own, 1 NVIDIA DLSS, 2 NVIDIA DLAA, 3 AMD FSR 3.1, 4 AMD FSR 4, 5 AMD FSR 4 Native AA | Game's own | NVIDIA DLSS | What scales the 3D up to the Output resolution. DLSS and DLAA need an NVIDIA RTX GPU. |
| `resolution` | Render resolution | 0 Auto (dynamic), 1 100%, 2 75%, 3 67% (Quality), 4 50% (Performance), 5 58% (Balanced), 6 33% (Ultra Performance) | Auto (dynamic) | 50% (Performance) | The share of the output the 3D is drawn at; the Upscaler fills in the rest. |
| `dlss_model` | DLSS model | 0 Transformer (K), 1 Transformer 2 (M), 2 Transformer 2 (L), 3 NVIDIA default | Transformer (K) | Transformer (K) | Which of NVIDIA's DLSS networks draws the picture. |
| `sharpness` | Sharpness | 0-100 step 10 (Off .. 100%) | Off | 80% | Sharpens the picture as it goes to the window. DLSS and DLAA suit around 80%. |
| `frame_generation` | Frame generation | 0 Off, 1 Auto, 2 2x, 3 3x, 4 4x, 5 5x, 6 6x, 7 7x, 8 8x | Off | Off | Makes frames between the game's own (AMD FSR 3.1 frame interpolation), for smoother motion at a little input delay. |

### Display

| Key | Menu row | Values | Default | This PC | What it does |
|---|---|---|---|---|---|
| `fullscreen` | Fullscreen | 0 Off, 1 On | Off | Off | Fills the whole screen without the window's border. The same as F11. |
| `output_res` | Output resolution | 0 Game's own, 1 2560x1440, 2 3840x2160, 3 1920x1080, 4 5120x1440 | Game's own | Game's own | The game's whole frame, HUD included, before it is scaled to the window. Docked only. Applies after a restart. |
| `upscaling` | Window scaling filter | 0 Plain, 1 Catmull-Rom, 2 Lanczos | Plain | Plain | How the finished picture is scaled to the window when its size differs from the Output resolution. |
| `ultrawide` | Aspect ratio (Ultrawide) | 0 16:9, 1 Monitor's shape | 16:9 | 16:9 | The camera's shape on a screen wider or narrower than 16:9. The HUD is still stretched. Applies after a restart. |
| `handheld_profile` | Handheld profile | 0 Off, 1 On | Off | Off | Runs the game as if the console were undocked: lower resolutions. Applies after a restart. |
| `hdr` | HDR | 0 Off, 1 Expanded, 2 Native (the game's) | Off | Off | HDR10 out on an HDR display (Windows: Use HDR on). |
| `hdr_paper_white` | HDR paper white | 80-400 step 10 (80 nits .. 400 nits) | 200 nits | 200 nits | How bright the picture's white is with HDR on. |
| `hdr_peak_brightness` | HDR peak brightness | 400-4000 step 100 (400 nits .. 4000 nits) | 1000 nits | 1000 nits | Set it to your display's peak brightness. |

### Performance

| Key | Menu row | Values | Default | This PC | What it does |
|---|---|---|---|---|---|
| `frame_rate_limit` | Frame rate limit | 0 60, 1 90, 2 120, 3 144, 4 165, 5 240 | 60 | 60 | Above 60 it needs Game speed on Real time, and a display that fast. |
| `frame_pacing` | Frame pacing (V-Sync) | 0 Off, 1 On | Off | Off | On: frames at an even interval, less stutter, up to one frame of delay. |
| `nvidia_reflex` | NVIDIA Reflex | 0 Off, 1 On, 2 On + Boost | Off | Off | NVIDIA GPUs: less input delay. Not used with frame generation. |
| `gpu_device` | GPU device | 0 Auto (best dedicated GPU), 1 Device 1, 2 Device 2, 3 Device 3, 4 Device 4, 5 Device 5, 6 Device 6, 7 Device 7, 8 Device 8 | Auto (best dedicated GPU) | Auto (best dedicated GPU) | Which graphics card the game runs on. Applies after a restart. |
| `robust_buffers` | Robust buffers | 0 Auto, 1 Off, 2 On | Auto | Auto | Bounds-checked buffer reads. Off can be faster on some NVIDIA cards. Applies after a restart. |

### Interface

| Key | Menu row | Values | Default | This PC | What it does |
|---|---|---|---|---|---|
| `fps_counter` | FPS counter | 0 Off, 1 FPS, 2 Full | Off | Off | Frame rate in the corner, or a full GPU/CPU panel. F10 cycles it in game. |
| `menu_size` | Menu size | 0 Auto, 1 Small, 2 Medium, 3 Large | Auto | Auto | How large the game's own menu is drawn. |
| `menu_shortcut` | Menu shortcut | 0 Insert or F1, 1 Insert only, 2 F1 only | Insert or F1 | Insert or F1 | Which key opens the game's own settings menu. |

### Gameplay

| Key | Menu row | Values | Default | This PC | What it does |
|---|---|---|---|---|---|
| `game_speed` | Game speed | 0 Per frame, 1 Real time | Real time | Real time | How the game keeps time when it runs below 60 fps. |
| `text_speed` | Text speed | 0 Normal, 1 Twice as fast, 2 Four times as fast, 3 Instant | Normal | Normal | How fast dialogue letters appear. |
| `audio_output` | Audio output | 0 Auto, 1 Stereo, 2 5.1 | Auto | Auto | The speakers the game mixes its sound for. Applies after a restart. |
| `skip_title_screen` | Skip title screen | 0 Off, 1 On | Off | Off | Starts with the most recent saved game. Applies after a restart. |
| `discord_status` | Discord status | 0 Off, 1 On | On | On | Shows the game in your Discord status. |

### Controls

| Key | Menu row | Values | Default | This PC | What it does |
|---|---|---|---|---|---|
| `controller_in_background` | Controller in background | 0 Off, 1 On | Off | Off | Whether the controller plays while another window has the focus. |
| `mouse_camera` | Mouse camera | 0 As the stick, 1 Native | As the stick | As the stick | How the captured mouse turns the camera. |
| `mouse_sensitivity` | Mouse sensitivity | 10-500 step 10 (0.10 .. 5.00) | 1.00 | 1.00 | How far the camera turns for a mouse movement. |
| `invert_mouse_vertical` | Invert mouse vertical | 0 Off, 1 On | Off | Off | Swaps up and down for the mouse. |
| `invert_camera_x` | Invert camera X | 0 Off, 1 On | Off | Off | Inverts left/right look on the right stick. |
| `invert_camera_y` | Invert camera Y | 0 Off, 1 On | Off | Off | Inverts up/down look on the right stick. |
| `stick_dead_zone` | Stick dead zone | 0 Default (24%), 1 5%, 2 10%, 3 15%, 4 20%, 5 30% | Default (24%) | Default (24%) | How far a stick moves before the game sees it. |
| `rumble_strength` | Rumble strength | 0-150 step 25 (Off .. 150%) | 100% | 100% | Percent of the game's rumble. |
| `layout_preset` | Layout preset | 0 Nintendo (Def), 1 Xbox / PC, 2 Swap Face, 3 Custom | Nintendo (Def) | Nintendo (Def) | Which face button is which. |
| `button_prompts` | Button prompts | 0 Auto, 1 Switch, 2 Xbox, 3 PlayStation 4, 4 PlayStation 5, 5 Steam Deck | Auto | Auto | The button icons the game shows. Applies after a restart. |

### Motion

| Key | Menu row | Values | Default | This PC | What it does |
|---|---|---|---|---|---|
| `motion_controls` | Motion controls | 0 Off, 1 On | On | On | Aims with the controller's gyroscope where the game uses motion. |
| `motion_sensitivity_x` | Motion sensitivity X | 10-600 step 10 (10% .. 600%) | 100% | 100% | 100% is the console's own. |
| `motion_sensitivity_y` | Motion sensitivity Y | 10-300 step 10 (10% .. 300%) | 100% | 100% | 100% is the console's own. |
| `invert_motion_x` | Invert motion X | 0 Off, 1 On | Off | Off | Turns the motion aim's left and right around. |
| `invert_motion_y` | Invert motion Y | 0 Off, 1 On | Off | Off | Turns the motion aim's up and down around. |
| `motion_deadzone` | Motion deadzone | 0 Low, 1 Standard, 2 High | Standard | Standard | How slow a turn counts as none. |

### Advanced

| Key | Menu row | Values | Default | This PC | What it does |
|---|---|---|---|---|---|
| `debug_tools` | Debug tools | 0 Off, 1 On | Off | Off | Testing tools in the game menu: flight, free camera, teleport. |
| `descriptor_reuse` | Descriptor reuse | 0 Off, 1 On | Off | Off | Fewer descriptor rebuilds while textures stream. Applies after a restart. |
| `sampler_cache` | Sampler cache | 0 Off, 1 On | Off | Off | Sampler lookups without a lock while textures stream. Applies after a restart. |
| `intel_shader_compiles` | Intel shader compiles | 0 One at a time, 1 Side by side | One at a time | One at a time | Intel graphics only: side by side is faster but can crash. Applies after a restart. |

## Rules the engine applies

- *DLSS model* is used only by NVIDIA DLSS / DLAA; DLSS and DLAA need an NVIDIA RTX GPU and `ngx\nvngx_dlss.dll` (the launcher hides them without it).
- *Render resolution* does nothing with DLAA or FSR 4 Native AA (full size).
- *NVIDIA Reflex* is not used while frame generation is on.
- *HDR paper white* / *peak brightness* apply only with HDR on.
- *Quality preset* 1-5 sets Render resolution, Window scaling filter, Sharpness and Handheld profile; changing one of those returns it to Custom.
- Not exposed: *Microphone* (a device index the game manages with `microphone_device`), and menu actions without a value (Restart, Quit, debug actions).

Labels marked by the engine only as hints (Window scaling filter 0 = "the plain copy", Text speed 1/2) use short names derived from those hints.
