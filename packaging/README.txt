Retroport Workbench
===================

Browse retail World of Warcraft's items and transmog sets in 3D, pick what you want, and backport
it to your 3.3.5a (WotLK 12340) client - and to your AzerothCore or TrinityCore server.


Start
-----
1. Unpack this whole folder anywhere. Keep runtime\ and app\ next to the .exe.
2. Run "Retroport Workbench.exe". It opens in a window of its own; closing the window closes it.
   (Windows may say the program is from an unknown publisher - it isn't code-signed. Choose
   "More info", then "Run anyway".)
3. The first time, Settings opens: choose your 3.3.5a client folder (the one with Wow.exe).
   Optionally your server's dbc folder too, so Install adds the new items there as well.
4. Pick items and sets (+), look them over in the Basket, give them stats in Stats (item level,
   classes, stats, sockets, sets...), then Backport > Install.


What it needs
-------------
- Windows 10 or 11. Its window is Microsoft Edge's app mode (Edge comes with Windows).
- The internet. Retail's items, models and textures come from wago.tools, and the first Install
  downloads the retail backport kit and Equip Plus's tools from their authors' GitHub pages (always
  these copies, at the versions this release is tested with):
    https://github.com/Kirazul/wow-retail-backport
    https://github.com/TheDaveCalaz/wxl-equip-plus
  If wago.tools is down, Settings can read your own retail install instead.
- Any 3.3.5a client. Optional: the extra 3D pieces (skirts, boot shells, belt buckles, 3D capes)
  need WarcraftXL and Equip Plus in your client (https://github.com/WarcraftXL/wxl-core); Install
  adds them only when your client has Equip Plus. With Equip Plus 1.3.0 or newer, plumes, ribbons
  and chains can swing (live physics, experimental): turn it on in Settings.


Where it keeps things
---------------------
  %USERPROFILE%\.retroport\workbench.json     your settings
  %USERPROFILE%\.retroport\workbench\         its cache, your basket, the kit's project, its log

Install backs up everything it replaces in your client and server first; Undo puts it back.
It never runs SQL against your database: it shows the item SQL for you to apply.


Not affiliated with or endorsed by Blizzard Entertainment. It ships no Blizzard assets: game data
is read at run time from wago.tools or your own game installs, for your own use. World of
Warcraft and its assets are trademarks and copyright of Blizzard Entertainment.
