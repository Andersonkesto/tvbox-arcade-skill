# Arcade Controllers, Dual-Mode Gamepads & Coin Acceptors

## 1. Zero Delay Arcade USB Encoder (`0079:0006` — DragonRise)
- Common arcade PCB (`DragonRise Inc.   Generic   USB  Joystick  `, `VID 0x0079`, `PID 0x0006`).
- Kernel support: Linux `hid-dr` (`dragonrise`) driver (verify `/sys/bus/hid/drivers/dragonrise`).
- Android 7.1 TV Box ROMs frequently ship `Vendor_0079_Product_0011.kl` but **omit** `Vendor_0079_Product_0006.kl`.
- Deploy [Vendor_0079_Product_0006.kl](../resources/keylayouts/Vendor_0079_Product_0006.kl) to `/system/usr/keylayout/` (`chmod 644`, `chown root:root`) and [DragonRise_ZeroDelay.cfg](../resources/autoconfig/DragonRise_ZeroDelay.cfg) to `/sdcard/RetroArch/autoconfig/android/` (`deploy_tvbox.py --keylayouts --configs --su` does both, with backups). RetroArch matches the profile by the `input_device` line inside the file, so the filename does not matter.
- **Important:** Exact physical button numbers (`K1..K12`) depend on how microswitches are wired; if any buttons are swapped after plugging in, inspect raw scancodes with `getevent -l /dev/input/eventX` and adjust the `.cfg` keycodes.

## 2. Dual-Mode PS3 / PC Gamepads (`054c:0268` vs `0e8f:0003`)
- Clone/adapter controllers (e.g., `My-Power CO.,LTD. PS(R) Controller Adaptor`) toggle between two USB IDs on reboot or when holding the central **Mode / PS / Home** button:
  - **PS3 mode (`054c:0268`):** Bound by kernel 3.10 `hid-sony` (`Fixing up Sony Sixaxis report descriptor`), which can stall clones so no events reach userspace.
  - **PC / GreenAsia mode (`0e8f:0003`):** Events reach Android cleanly, but Android 7.1 lacks `Vendor_0e8f_Product_0003.kl` and falls back to `Generic.kl` (buttons `188..195` logged as unhandled in Pegasus).
- Fix: Install [Vendor_0e8f_Product_0003.kl](../resources/keylayouts/Vendor_0e8f_Product_0003.kl) and use [MyPower_PS3_PC.cfg](../resources/autoconfig/MyPower_PS3_PC.cfg) without `input_vendor_id`/`input_product_id` locks so RetroArch matches both modes by `input_device` name.

## 3. Standard 6-Button Fighting Layout + Hotkeys

### Button map (Zero Delay header → Android keycode → RetroPad)
The `.kl` and `.cfg` remap twice on purpose; this table is the single source of truth. If you rewire a microswitch, edit the `.cfg` keycode, not the `.kl`.

| Header | Linux code | `.kl` → Android key (code) | RetroPad (`.cfg`) | Arcade role |
|---|---|---|---|---|
| K1 | 288 | `BUTTON_Y` (100) | Y | LP |
| K2 | 289 | `BUTTON_B` (97) | X | MP |
| K3 | 290 | `BUTTON_A` (96) | A | MK |
| K4 | 291 | `BUTTON_X` (99) | B | LK / *hotkey: menu* |
| K5 | 292 | `BUTTON_L1` (102) | L2 | *hotkey: load state* |
| K6 | 293 | `BUTTON_R1` (103) | L | HP / *hotkey: save state* |
| K7 | 294 | `BUTTON_L2` (104) | R2 | — |
| K8 | 295 | `BUTTON_R2` (105) | R | HK |
| K9 | 296 | `BUTTON_SELECT` (109) | Select | **Coin** (coin acceptor) |
| K10 | 297 | `BUTTON_START` (108) | Start | Start / *hotkey: exit* |
| K11 | 298 | `BUTTON_THUMBL` (106) | — | **Service button = hotkey enable** |
| K12 | 299 | `BUTTON_THUMBR` (107) | R3 | — |

### Hotkeys
- **Zero Delay profile:** hotkey enable is a hidden **service button on K11** (`input_enable_hotkey_btn = "106"`), **not** `Select`. `Select` carries the coin pulse, so `Select`-based hotkeys would quit the game (`Select + Start`) or overwrite save states whenever a coin drops while a player holds a button.
  - **`Service + Start`**: Exit game back to Pegasus (`input_exit_emulator_btn = "108"`, `quit_press_twice = "false"`, `input_menu_toggle_gamepad_combo = "0"`).
  - **`Service + K4`**: Toggle RetroArch Quick Menu.
  - **`Service + K6 / K5`**: Save State / Load State.
- **Dual-mode gamepad profile (no coin):** keeps `Select` (109) as hotkey enable: `Select + Start` exits, `Select + B` (menu), `Select + R1/L1` (save/load state).

## 4. Electronic Coin Acceptor Integration
- **Arcade / MAME / FBNeo (Insert Coin):**
  - Power the coin acceptor with an external **12V DC** supply (PSU `12V` and `GND`).
  - Set the coin acceptor switch to **NO (Normally Open)** and pulse width to **Medium (`50 ms`)**.
  - Wire **only** the dry-contact/open-collector **`COIN` signal + `GND`** pair to the **`Select`** 2-pin header on the Zero Delay USB encoder.
  - **SAFETY WARNING:** Never connect `+12V` directly to any pin on the 5V USB Zero Delay board — doing so will permanently destroy the USB controller IC.
  - Because `Select` (`109`) is mapped as Coin/Select in `mame2003_plus` and `fbneo`, every coin pulse adds 1 credit in-game.
  - Keep hotkeys on the K11 service button (see §3) so a coin pulse can never combine with Start into "exit game".
  - Test: insert a coin while holding Start — the game must add a credit and keep running.
- **Home Consoles (SNES, Mega Drive, NES, GBA, GBC):**
  - Home consoles have no native "Insert Coin" mechanism. For pay-to-play cabinets across all systems, use an external **coin-timer relay board** wired to gate the common `GND` of the player controls/switches (simpler and far more reliable than custom Android input-blocking daemons).
