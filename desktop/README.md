# Pippin for macOS

The orb at the edge of your screen, and your memory panel. See
[Browser extension and desktop app](../docs/guide/extension-and-desktop.md) for what it does.

## Build

On a Mac (macOS 12 or later) with Rust (`curl https://sh.rustup.rs -sSf | sh`), Node 18+ and the Xcode command
line tools:

```sh
cd desktop
npm install
npm run icons        # the app icons, from src-tauri/icons/icon.png
npm run build        # src-tauri/target/release/bundle/macos/Pippin.app and a .dmg
```

`npm run dev` runs it without bundling.

The first time it runs, macOS asks to allow Accessibility access (System Settings → Privacy & Security →
Accessibility): the app reads which app and window are in front, to tell Pippin what you are doing. It works
without it; it then does not know. The first time you hold ⌥⇧Space it asks for the Microphone, and the first time
you say "this figure", for Screen Recording (only the window in front is captured, only then).

## Settings

Right-click the orb. The console's address (default `http://localhost:8200`), a token when the console has
accounts (made in the console under Memory → Browser extension and desktop app), whether to start the console
when it is not running (default: `aurora serve --port 8200`, run in a login shell), whether to report the app
and window in front, and voice: on or off, the whisper.cpp command that recognises speech on this Mac (see
[Voice](../docs/guide/voice.md)), the system voice to answer with, and whether long things use the console's cloud
voice. **Listen to today's digest** is there too.

| | |
|---|---|
| Click the orb | your memory panel |
| Drop a link, a selection or a file on it | saved into your memory |
| Drag it | it moves |
| ⌥ Space | show or hide the panel |
| Hold ⌥⇧Space | talk to Pippin; let go to send (pressing again stops it speaking) |
| ⌥⇧N | type a thought about what is in front |
