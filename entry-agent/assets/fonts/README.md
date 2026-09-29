# Gate monitor fonts

Loaded privately at startup by `entry-agent/ui.py` (nothing is installed
system-wide). Tk on Windows can't select a width or weight from a variable
font, so each width/weight the redesign uses is a separate static file with
its own family name.

| File | Family name in Tk | Source |
|---|---|---|
| SecureTap-Cond-Bold.ttf | SecureTap Cond Bold | Archivo, wdth 75, wght 700 |
| SecureTap-Cond-Heavy.ttf | SecureTap Cond Heavy | Archivo, wdth 75, wght 800 |
| SecureTap-Semi-Bold.ttf | SecureTap Semi Bold | Archivo, wdth 112.5, wght 700 |
| SecureTap-Semi-Heavy.ttf | SecureTap Semi Heavy | Archivo, wdth 112.5, wght 800 |
| SecureTap-Wide-Bold.ttf | SecureTap Wide Bold | Archivo, wdth 125, wght 700 |
| SecureTap-Wide-Heavy.ttf | SecureTap Wide Heavy | Archivo, wdth 125, wght 800 |
| SecureTap-Wide-Black.ttf | SecureTap Wide Black | Archivo, wdth 125, wght 900 |
| SecureTap-Text-Regular.ttf / -Bold.ttf | SecureTap Text | Atkinson Hyperlegible Next, wght 400 / 700 |
| IBMPlexMono-Regular/Medium/SemiBold.ttf | IBM Plex Mono (Medium, SemiBold) | IBM Plex Mono, unmodified |
| Phosphor.ttf / Phosphor-Bold.ttf | Phosphor / Phosphor-Bold | @phosphor-icons/web 2.1.1 (MIT) |

The Archivo and Atkinson files are static instances cut from the Google Fonts
variable fonts with fontTools (`varLib.instancer`) and renamed to neutral
"SecureTap ..." family names, as the SIL Open Font License asks of modified
fonts. The licenses are the `OFL-*.txt` files in this folder. If a file is
missing, `ui.py` falls back to a stock Windows font.
