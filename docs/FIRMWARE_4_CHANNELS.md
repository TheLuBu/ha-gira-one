# Firmware 4.0 channel support

The integration selects a platform by function type. Channel names below omit
the shared `de.gira.schema.channels.` prefix. Functions omit
`de.gira.schema.functions.`. R, W and E mean read, write and event. Points
marked optional may be absent independently.

| Function and channel | Shared contract | New or changed in v3 | Home Assistant behavior verified with fixtures |
| --- | --- | --- | --- |
| Switch, Switch / Switch2 | OnOff (Switch, RWE) | Optional Status (DWord, RE) and Lock (Switch, RWE) | Switch reads and writes OnOff. No extra status or lock entity. |
| Covering, BlindWithPos / Blind2 | Step-Up-Down and Up-Down (UpDown, W). Optional Movement (Binary, RE), Position and Slat-Position (Percent, RWE). | Optional Upper-Endstop and Lower-Endstop (Bool, RE), Status (DWord, RE), Lock (Switch, RWE). | Cover with open, close and stop. Absolute positioning and tilt depend on their respective writable points. Gira position 0 is fully open. Added points have no separate controls. |
| ColoredLight, DimmerRGBW / DimmerRGBW2 | OnOff (Switch, RWE), Red, Green and Blue (Percent, RWE). Optional White and Brightness (Percent, RWE). | Optional packed RGB (DWord, RWE) and RGBW (QWord, RWE). | Native RGB with three channels, RGBW when White is present. Commands and feedback use individual percentages. Packed values are unnecessary for these channels. |
| Trigger or PressAndHold, Trigger / Trigger2 | Trigger (Trigger, WE). | Optional Status (DWord, RE) and Lock (Switch, RWE). | Discovery accepts the descriptions. A button or press/release action needs a dedicated platform. [Follow-up #7](https://github.com/TheLuBu/ha-gira-one/issues/7) |
| Camera / Camera2, channel Camera | Camera (Binary, RWE) in both descriptions. | Camera2 changes the function name. The binary point was mandatory in the v2 camera table and is optional in v3. | Discovery accepts cameras with or without the point. There is no camera entity or stream source in this mapping. [Follow-up #8](https://github.com/TheLuBu/ha-gira-one/issues/8) |

The synthetic [channel fixtures](../tests/test_channels.py) exercise platform
setup, commands and feedback. They establish behavior against the published
interface. Availability on a particular server depends on firmware and project
configuration. These checks ran without physical actuation.

Sources are the [v3 function and channel definitions, sections 10.1 and 10.2](https://partner.gira.de/data3/Gira_IoT_REST_API_v3_DE.pdf#page=27)
and the [v2 definitions, sections 9.1 and 9.2](https://partner.gira.de/data3/Gira_IoT_REST_API_v2_EN.pdf).
