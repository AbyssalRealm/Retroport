# mod-retroport-racials

An AzerothCore module: the server half of the racials `retroport race` ports from BfA (and
from retail, `--retail`).

A race package gives its race BfA's own racial skill line as 3.3.5 spells. Most racials need
nothing more - 3.3.5 has their auras. What BfA does in a server script is here, bound to the
spells by the package's world SQL (`spell_script_names`, `spell_proc`), so nothing happens for a
spell whose race package isn't installed.

| Race | Spell | What the script does |
|---|---|---|
| Kul Tiran | Brush It Off (291628) | Each hit taken heals 2% of its damage over 4 sec (spell 291843; a running heal rolls into the new one). The damage-taken half of its 1% Versatility: 0.5% less damage taken. The damage and healing done half is the spell's own auras. |
| Pandaren | Bouncy (107076) | Falling damage scaled by its dummy aura's amount (-50: half). The combat log line, sent before the damage is dealt, still shows the whole fall. |
| Pandaren | Inner Peace (107074) | The rest bonus earned resting in game, and while logged out (added at login), earned again by its amount (100: twice). |
| Pandaren | Epicurean (107072) | `spell_retroport_epicurean`, bound by the SQL to every 3.3.5 spell named "Well Fed": their stat, rating, attack power, spell power, mp5 and resistance auras raised by its amount (100%) while the owner has Epicurean. |
| Mag'har Orc | Ancestral Call (274738) | `spell_retroport_ancestral_call`: casts one of the four clan buffs at random (BfA's rule; retail's "two highest secondary stats" has nothing to rank in 3.3.5, where characters have no mastery or versatility). |
| Mag'har Orc | Rictus of the Laughing Skull (274739), Zeal of the Burning Blade (274740) | `spell_retroport_rating_pct`: their MOD_RATING aura holds a percent (5%: what retail's level-scaled rating is worth at its top level, about the same at every level); the script makes it the crit or haste rating that gives it at the player's level (`Player::GetRatingMultiplier`, the mask's first rating). |
| Mag'har Orc | Might of the Blackrock (274742) | Versatility's damage-taken half, as Brush It Off's: half its damage and healing done (4%) less damage taken. Ferocity of the Frostwolf (mastery, 274741) is damage and healing done alone. |

Bouncy, Inner Peace and the Versatility damage-taken half go by the racial's own aura (no SQL
binding); Quaking Palm, Gourmand, Savage Blood (dispel-type durations) and Open Skies (mounted
and flight speed) need nothing here (3.3.5's own auras); Sympathetic Vigor's pet health is
AzerothCore's own `spell_pet_auras` (the SQL adds the row), every permanent pet (hunter pets,
warlock demons, the Death Knight's ghoul) - not guardians.

## Install

1. Copy this folder to `<AzerothCore>\modules\mod-retroport-racials`.
2. Re-run CMake (a new module), build, and restart worldserver.
3. Apply the race package's `server\<Race>_race.sql` (it binds the scripts).

Without the module the spells still load; Brush It Off then gives only its damage and healing
done, the Pandaren's three passives do nothing, and Ancestral Call grants no buff.
