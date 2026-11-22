# Arcade Controllers, Dual-Mode Gamepads & Coin Acceptors

## 1. Zero Delay Arcade USB Encoder (`0079:0006` — DragonRise)
- Common arcade PCB (`DragonRise Inc.   Generic   USB  Joystick  `, `VID 0x0079`, `PID 0x0006`).
- Kernel support: Linux `hid-dr` (`dragonrise`) driver (verify `/sys/bus/hid/drivers/dragonrise`).
- Android 7.1 TV Box ROMs frequently ship `Vendor_0079_Product_0011.kl` but **omit** `Vendor_0079_Product_0006.kl`.
- Deploy [Vendor_0079_Product_0006.kl](../resources/keylayouts/Vendor_0079_Product_0006.kl) to `/system/usr/keylayout/` (`chmod 644`, `chown root:root`) and [DragonRise_ZeroDelay.cfg](../resources/autoconfig/DragonRise_ZeroDelay.cfg) to `/sdcard/RetroArch/autoconfig/android/DragonRise Inc.   Generic   USB  Joystick  .cfg`.
- **Important:** Exact physical button numbers (`K1..K12`) depend on how microswitches are wired; if any buttons are swapped after plugging in, inspect raw scancodes with `getevent -l /dev/input/eventX` and adjust the `.cfg` keycodes.

## 2. Dual-Mode PS3 / PC Gamepads (`054c:0268` vs `0e8f:0003`)
- Clone/adapter controllers (e.g., `My-Power CO.,LTD. PS(R) Controller Adaptor`) toggle between two USB IDs on reboot or when holding the central **Mode / PS / Home** button:
  - **PS3 mode (`054c:0268`):** Bound by kernel 3.10 `hid-sony` (`Fixing up Sony Sixaxis report descriptor`), which can stall clones so no events reach userspace.
  - **PC / GreenAsia mode (`0e8f:0003`):** Events reach Android cleanly, but Android 7.1 lacks `Vendor_0e8f_Product_0003.kl` and falls back to `Generic.kl` (buttons `188..195` logged as unhandled in Pegasus).
- Fix: Install [Vendor_0e8f_Product_0003.kl](../resources/keylayouts/Vendor_0e8f_Product_0003.kl) and use [MyPower_PS3_PC.cfg](../resources/autoconfig/MyPower_PS3_PC.cfg) without `input_vendor_id`/`input_product_id` locks so RetroArch matches both modes by `input_device` name.

## 3. Standard 6-Button Fighting Layout + Exit Hotkey
Both included `.cfg` profiles map the standard 6-button SNES/Arcade fighting layout (`1=LP/Y`, `2=MP/X`, `6=HP/L`, `4=LK/B`, `3=MK/A`, `8=HK/R`) plus:
- **`Select + Start`**: Exit game immediately back to Pegasus (`input_enable_hotkey_btn = "109"`, `input_exit_emulator_btn = "108"`, `quit_press_twice = "false"`, `input_menu_toggle_gamepad_combo = "0"`).
- **`Select + Button 4`**: Toggle RetroArch Quick Menu.
- **`Select + Button 6 / 5`**: Save State / Load State.

## 4. Electronic Coin Acceptor Integration
- **Arcade / MAME / FBNeo (Insert Coin):**
  - Power the coin acceptor with an external **12V DC** supply (PSU `12V` and `GND`).
  - Set the coin acceptor switch to **NO (Normally Open)** and pulse width to **Medium (`50 ms`)**.
  - Wire **only** the dry-contact/open-collector **`COIN` signal + `GND`** pair to the **`Select`** 2-pin header on the Zero Delay USB encoder.
  - **SAFETY WARNING:** Never connect `+12V` directly to any pin on the 5V USB Zero Delay board — doing so will permanently destroy the USB controller IC.
  - Because `Select` (`109`) is mapped as Coin/Select in `mame2003_plus` and `fbneo`, every coin pulse adds 1 credit in-game.
- **Home Consoles (SNES, Mega Drive, NES, GBA, GBC):**
  - Home consoles have no native "Insert Coin" mechanism. For pay-to-play cabinets across all systems, use an external **coin-timer relay board** wired to gate the common `GND` of the player controls/switches (simpler and far more reliable than custom Android input-blocking daemons).
