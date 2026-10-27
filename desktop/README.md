# AURORA for macOS

The orb at the edge of your screen, and your memory panel. See
[Browser extension and desktop app](../docs/guide/extension-and-desktop.md) for what it does.

## Build

On a Mac (macOS 12 or later) with Rust (`curl https://sh.rustup.rs -sSf | sh`), Node 18+ and the Xcode command
line tools:

```sh
cd desktop
npm install
npm run icons        # the app icons, from src-tauri/icons/icon.png
npm run build        # src-tauri/target/release/bundle/macos/AURORA.app and a .dmg
```

`npm run dev` runs it without bundling.

The first time it runs, macOS asks to allow Accessibility access (System Settings → Privacy & Security →
Accessibility): the app reads which app and window are in front, to tell AURORA what you are doing. It works
without it; it then does not know.

## Settings

Right-click the orb. The console's address (default `http://localhost:8200`), a token when the console has
accounts (made in the console under Memory → Browser extension and desktop app), whether to start the console
when it is not running (default: `aurora serve --port 8200`, run in a login shell), and whether to report the app
and window in front.

| | |
|---|---|
| Click the orb | your memory panel |
| Drop a link, a selection or a file on it | saved into your memory |
| Drag it | it moves |
| ⌥ Space | show or hide the panel |
