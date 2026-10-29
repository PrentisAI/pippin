// AURORA on the macOS desktop.
//
// * The orb: a small ball at the edge of the screen. Drop a link, a selection or a file on it to save it into
//   your memory; click it for the memory panel; it says in a bubble when something you saved bears on what you
//   are doing.
// * The panel: the console's memory panel (`/panel`), in a window of its own.
// * What you are doing: the frontmost app and its window title (macOS Accessibility), sent to AURORA as context.
//   Browsers are left to the browser extension, which knows the page and the selection.
// * The server: the local AURORA console is started in the background when it is not running.
// * Voice: hold ⌥⇧Space and speak, let go to send. The words are recognised on this Mac (whisper.cpp, the command
//   in the settings) or by the console; the window in front is captured at that moment only (for "this figure").
//   The answer is spoken short with the system voice and shown in full on the panel. Speaking again stops it.
//
// Settings (the console's address, a token when it has accounts, how to start it) live in the app's
// configuration folder, settings.json.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

use base64::Engine;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, State, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const BROWSERS: &[&str] = &["Google Chrome", "Safari", "Arc", "Microsoft Edge", "Firefox", "Brave Browser", "Chromium"];

#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
struct Settings {
    server: String,
    token: String,
    start_server: bool,
    server_command: String,
    report_context: bool,
    voice: bool,
    whisper_command: String, // "{audio}" is the recording; empty: the console recognises it
    say_voice: String,       // a macOS voice for `say` (empty: the system's)
    cloud_voice: bool,       // long things (the digest) in the console's cloud voice, when it has one
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            server: "http://localhost:8200".into(),
            token: String::new(),
            start_server: true,
            server_command: "aurora serve --port 8200".into(),
            report_context: true,
            voice: true,
            whisper_command: String::new(),
            say_voice: String::new(),
            cloud_voice: false,
        }
    }
}

struct AppState {
    settings: Mutex<Settings>,
    server: Mutex<Option<Child>>,
    speaking: Mutex<Option<Child>>,
    shot: Mutex<Option<String>>, // the window in front when you pressed the key (a data: URL)
}

fn settings_path(app: &AppHandle) -> Option<std::path::PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("settings.json"))
}

fn load_settings(app: &AppHandle) -> Settings {
    settings_path(app)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

fn api(s: &Settings, path: &str) -> String {
    format!("{}/api{}", s.server.trim_end_matches('/'), path)
}

fn client(s: &Settings) -> reqwest::blocking::Client {
    let mut headers = reqwest::header::HeaderMap::new();
    if !s.token.is_empty() {
        if let Ok(v) = reqwest::header::HeaderValue::from_str(&format!("Bearer {}", s.token)) {
            headers.insert(reqwest::header::AUTHORIZATION, v);
        }
    }
    reqwest::blocking::Client::builder()
        .default_headers(headers)
        .timeout(Duration::from_secs(120))
        .build()
        .expect("http client")
}

fn reachable(s: &Settings) -> bool {
    client(s).get(api(s, "/memory/questions")).timeout(Duration::from_secs(3)).send()
        .map(|r| r.status().is_success()).unwrap_or(false)
}

// ------------------------------------------------------------------------------------------- commands

#[tauri::command]
fn get_settings(state: State<AppState>) -> Settings {
    state.settings.lock().unwrap().clone()
}

#[tauri::command]
fn set_settings(app: AppHandle, state: State<AppState>, settings: Settings) -> Result<(), String> {
    if let Some(p) = settings_path(&app) {
        if let Some(dir) = p.parent() {
            std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
        }
        std::fs::write(p, serde_json::to_string_pretty(&settings).unwrap()).map_err(|e| e.to_string())?;
    }
    *state.settings.lock().unwrap() = settings;
    Ok(())
}

#[tauri::command]
fn capture(state: State<AppState>, url: String, text: String) -> Result<serde_json::Value, String> {
    let s = state.settings.lock().unwrap().clone();
    let body = serde_json::json!({ "url": url, "text": text, "selection": if url.is_empty() { text.clone() } else { String::new() }, "via": "orb" });
    let r = client(&s).post(api(&s, "/memory/capture")).json(&body).send().map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        return Err(r.text().unwrap_or_default());
    }
    r.json().map_err(|e| e.to_string())
}

#[tauri::command]
fn capture_file(state: State<AppState>, name: String, data: String) -> Result<serde_json::Value, String> {
    let s = state.settings.lock().unwrap().clone();
    let bytes = base64::engine::general_purpose::STANDARD.decode(data).map_err(|e| e.to_string())?;
    let url = api(&s, &format!("/memory/capture/file?name={}&via=orb", urlencoding::encode(&name)));
    let r = client(&s).post(url).header("Content-Type", "application/octet-stream").body(bytes).send()
        .map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        return Err(r.text().unwrap_or_default());
    }
    r.json().map_err(|e| e.to_string())
}

#[tauri::command]
fn source(state: State<AppState>, id: String) -> Result<serde_json::Value, String> {
    let s = state.settings.lock().unwrap().clone();
    client(&s).get(api(&s, &format!("/memory/sources/{}", id))).send().and_then(|r| r.json()).map_err(|e| e.to_string())
}

#[tauri::command]
fn open_panel(app: AppHandle) -> Result<(), String> {
    toggle_panel(&app, true)
}

#[tauri::command]
fn open_settings(app: AppHandle) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("settings") {
        return w.set_focus().map_err(|e| e.to_string());
    }
    WebviewWindowBuilder::new(&app, "settings", WebviewUrl::App("settings.html".into()))
        .title("AURORA settings").inner_size(500.0, 760.0).resizable(false).build().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn peek(app: AppHandle, head: String, body: String) -> Result<(), String> {
    show_peek(&app, &head, &body);
    Ok(())
}

#[tauri::command]
async fn hear(app: AppHandle, audio: String) -> Result<serde_json::Value, String> {
    // off the main thread: recognising and answering take seconds
    tauri::async_runtime::spawn_blocking(move || hear_now(&app, audio)).await.map_err(|e| e.to_string())?
}

fn on_main(app: &AppHandle, f: impl FnOnce(&AppHandle) + Send + 'static) {
    let a = app.clone();
    let _ = app.run_on_main_thread(move || f(&a));
}

fn hear_now(app: &AppHandle, audio: String) -> Result<serde_json::Value, String> {
    let state = app.state::<AppState>();
    let s = state.settings.lock().unwrap().clone();
    let shot = state.shot.lock().unwrap().take().unwrap_or_default();
    let wav = base64::engine::general_purpose::STANDARD.decode(&audio).map_err(|e| e.to_string())?;
    let mut body = serde_json::json!({ "screenshot": shot, "via": "voice" });
    if s.whisper_command.trim().is_empty() {
        body["audio"] = serde_json::Value::String(audio);
        body["audio_type"] = "wav".into();
    } else {
        let text = transcribe(&s.whisper_command, &wav)?;
        if text.trim().is_empty() {
            return Err("I did not hear anything".into());
        }
        body["text"] = serde_json::Value::String(text);
    }
    // the same conversation as the panel's: memory, projects, what is in front
    let r = client(&s).post(api(&s, "/assistant")).json(&body).send().map_err(|e| e.to_string())?;
    if !r.status().is_success() {
        return Err(r.text().unwrap_or_default());
    }
    let turn: serde_json::Value = r.json().map_err(|e| e.to_string())?;
    let speech = turn["speech"].as_str().unwrap_or("").to_string();
    speak(app, &s, &speech);
    let said = turn["said"].as_str().unwrap_or("").to_string();
    let full = !turn["text"].as_str().unwrap_or("").is_empty() || turn["items"].as_array().map(|a| !a.is_empty()).unwrap_or(false);
    on_main(app, move |a| {
        show_peek(a, &said, &speech);
        if full {
            let _ = toggle_panel(a, true); // the full answer, with its sources
        }
        refresh_panel(a);
    });
    Ok(turn)
}

#[tauri::command]
async fn keep_thought(app: AppHandle, text: String) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let s = app.state::<AppState>().settings.lock().unwrap().clone();
        let r = client(&s).post(api(&s, "/memory/thought")).json(&serde_json::json!({ "text": text })).send()
            .map_err(|e| e.to_string())?;
        if !r.status().is_success() {
            return Err(r.text().unwrap_or_default());
        }
        let turn: serde_json::Value = r.json().map_err(|e| e.to_string())?;
        let speech = turn["speech"].as_str().unwrap_or("Kept.").to_string();
        let said = turn["said"].as_str().unwrap_or("").chars().take(120).collect::<String>();
        on_main(&app, move |a| { show_peek(a, &speech, &said); refresh_panel(a); });
        Ok(())
    }).await.map_err(|e| e.to_string())?
}

/// ⌥⇧N: a small box to type a thought about what is in front.
fn open_note(app: &AppHandle) {
    let about = frontmost().map(|(_, t)| t).unwrap_or_default(); // before the box takes the focus
    let w = match app.get_webview_window("note") {
        Some(w) => w,
        None => match WebviewWindowBuilder::new(app, "note", WebviewUrl::App("note.html".into()))
            .inner_size(420.0, 130.0).decorations(false).transparent(true).always_on_top(true).skip_taskbar(true)
            .shadow(false).resizable(false).visible(false).build() {
            Ok(w) => w,
            Err(_) => return,
        },
    };
    if let (Some(orb), Ok(Some(m))) = (app.get_webview_window("orb"), w.current_monitor()) {
        if let Ok(p) = orb.outer_position() {
            let scale = m.scale_factor();
            let _ = w.set_position(PhysicalPosition::new(p.x - (430.0 * scale) as i32, p.y - (60.0 * scale) as i32));
        }
    }
    let _ = w.show();
    let _ = w.set_focus();
    let _ = app.emit_to("note", "note-open", about);
}

#[tauri::command]
async fn listen_digest(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let s = app.state::<AppState>().settings.lock().unwrap().clone();
        let d: serde_json::Value = client(&s).get(api(&s, "/memory/voice/digest")).send().and_then(|r| r.json())
            .map_err(|e| e.to_string())?;
        on_main(&app, |a| { let _ = toggle_panel(a, true); });
        speak(&app, &s, d["speech"].as_str().unwrap_or(""));
        Ok(())
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn quit(app: AppHandle) {
    stop_server(&app);
    app.exit(0);
}

// ------------------------------------------------------------------------------------------- windows

fn toggle_panel(app: &AppHandle, show: bool) -> Result<(), String> {
    if let Some(w) = app.get_webview_window("panel") {
        if !show && w.is_visible().unwrap_or(false) {
            return w.hide().map_err(|e| e.to_string());
        }
        w.show().map_err(|e| e.to_string())?;
        return w.set_focus().map_err(|e| e.to_string());
    }
    let s = app.state::<AppState>().settings.lock().unwrap().clone();
    let mut url = format!("{}/panel?via=orb", s.server.trim_end_matches('/'));
    if !s.token.is_empty() {
        url.push_str(&format!("&token={}", urlencoding::encode(&s.token)));
    }
    let w = WebviewWindowBuilder::new(app, "panel", WebviewUrl::External(url.parse().map_err(|e| format!("{e}"))?))
        .title("AURORA").inner_size(460.0, 840.0)
        // the panel stays the panel: a page of the console, or a source, opens in the browser
        .on_navigation(|u| {
            let panel = u.path() == "/panel";
            if !panel {
                let _ = Command::new("open").arg(u.as_str()).spawn();
            }
            panel
        })
        .on_new_window(|u, _| {
            let _ = Command::new("open").arg(u.as_str()).spawn();
            tauri::webview::NewWindowResponse::Deny
        })
        .build().map_err(|e| e.to_string())?;
    if let Ok(Some(m)) = w.current_monitor() {
        let size = m.size();
        let _ = w.set_position(PhysicalPosition::new(size.width as i32 - 520, 60));
    }
    Ok(())
}

fn refresh_panel(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("panel") {
        let _ = w.eval("window.postMessage({type: 'aurora:refresh'}, '*')");
    }
}

fn show_peek(app: &AppHandle, head: &str, body: &str) {
    let w = match app.get_webview_window("peek") {
        Some(w) => w,
        None => match WebviewWindowBuilder::new(app, "peek", WebviewUrl::App("peek.html".into()))
            .inner_size(340.0, 96.0).decorations(false).transparent(true).always_on_top(true).skip_taskbar(true)
            .shadow(false).resizable(false).focused(false).visible(false).build() {
            Ok(w) => w,
            Err(_) => return,
        },
    };
    if let (Some(orb), Ok(Some(m))) = (app.get_webview_window("orb"), w.current_monitor()) {
        if let Ok(p) = orb.outer_position() {
            let scale = m.scale_factor();
            let _ = w.set_position(PhysicalPosition::new(p.x - (350.0 * scale) as i32, p.y - (20.0 * scale) as i32));
        }
    }
    let _ = w.show();
    let _ = app.emit_to("peek", "peek", serde_json::json!({ "head": head, "body": body }));
}

/// After a drag, the orb settles against the nearest side of its screen (left or right), within the screen.
#[tauri::command]
fn snap_orb(app: AppHandle) -> Result<(), String> {
    let orb = app.get_webview_window("orb").ok_or("no orb")?;
    let m = orb.current_monitor().map_err(|e| e.to_string())?.ok_or("no screen")?;
    let (mp, ms, scale) = (m.position(), m.size(), m.scale_factor());
    let p = orb.outer_position().map_err(|e| e.to_string())?;
    let size = orb.outer_size().map_err(|e| e.to_string())?;
    let margin = (6.0 * scale) as i32;
    let (left, right) = (mp.x + margin, mp.x + ms.width as i32 - size.width as i32 - margin);
    let x = if p.x + size.width as i32 / 2 < mp.x + ms.width as i32 / 2 { left } else { right };
    let top = mp.y + (30.0 * scale) as i32;
    let bottom = mp.y + ms.height as i32 - size.height as i32 - margin;
    let y = p.y.clamp(top, bottom.max(top));
    if (x, y) != (p.x, p.y) {
        orb.set_position(PhysicalPosition::new(x, y)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

fn place_orb(app: &AppHandle) {
    if let Some(orb) = app.get_webview_window("orb") {
        if let Ok(Some(m)) = orb.current_monitor() {
            let size = m.size();
            let scale = m.scale_factor();
            let _ = orb.set_position(PhysicalPosition::new(
                size.width as i32 - (100.0 * scale) as i32,
                size.height as i32 - (140.0 * scale) as i32,
            ));
        }
    }
}

// ------------------------------------------------------------------------------------------- the server

fn start_server(app: &AppHandle) {
    let state = app.state::<AppState>();
    let s = state.settings.lock().unwrap().clone();
    if !s.start_server || reachable(&s) {
        return;
    }
    // a login shell, so the PATH has where `aurora` was installed (pip, pipx, uv)
    match Command::new("/bin/zsh").args(["-lc", &s.server_command]).spawn() {
        Ok(child) => *state.server.lock().unwrap() = Some(child),
        Err(e) => eprintln!("could not start AURORA: {e}"),
    }
}

fn stop_server(app: &AppHandle) {
    if let Some(mut child) = app.state::<AppState>().server.lock().unwrap().take() {
        let _ = child.kill();
    }
}

// ------------------------------------------------------------------------------------------- voice

fn transcribe(command: &str, wav: &[u8]) -> Result<String, String> {
    let path = std::env::temp_dir().join(format!("aurora-said-{}.wav", std::process::id()));
    std::fs::write(&path, wav).map_err(|e| e.to_string())?;
    let p = path.to_string_lossy().to_string();
    let cmd = if command.contains("{audio}") { command.replace("{audio}", &format!("'{p}'")) } else { format!("{command} '{p}'") };
    let out = Command::new("/bin/zsh").args(["-lc", &cmd]).output();
    let _ = std::fs::remove_file(&path); // recordings are not kept
    let out = out.map_err(|e| e.to_string())?;
    if !out.status.success() {
        return Err(format!("speech recognition failed: {}", String::from_utf8_lossy(&out.stderr)));
    }
    // whisper.cpp may print [00:00:00.000 --> 00:00:02.000] before each line
    let text = String::from_utf8_lossy(&out.stdout)
        .lines()
        .map(|l| if l.starts_with('[') { l.splitn(2, ']').nth(1).unwrap_or("").trim().to_string() } else { l.trim().to_string() })
        .collect::<Vec<_>>()
        .join(" ");
    Ok(text.trim().to_string())
}

fn stop_speaking(app: &AppHandle) {
    if let Some(mut child) = app.state::<AppState>().speaking.lock().unwrap().take() {
        let _ = child.kill();
    }
}

fn speak(app: &AppHandle, s: &Settings, text: &str) {
    stop_speaking(app);
    if text.trim().is_empty() {
        return;
    }
    // long things in the cloud voice, when there is one; otherwise (and on failure) the system voice
    if s.cloud_voice && text.chars().count() > 300 {
        if let Ok(r) = client(s).post(api(s, "/memory/voice/speak")).json(&serde_json::json!({ "text": text })).send() {
            if r.status().is_success() {
                if let Ok(bytes) = r.bytes() {
                    let path = std::env::temp_dir().join("aurora-speech.mp3");
                    if std::fs::write(&path, &bytes).is_ok() {
                        if let Ok(child) = Command::new("afplay").arg(&path).spawn() {
                            *app.state::<AppState>().speaking.lock().unwrap() = Some(child);
                            return;
                        }
                    }
                }
            }
        }
    }
    let mut cmd = Command::new("say");
    if !s.say_voice.trim().is_empty() {
        cmd.args(["-v", s.say_voice.trim()]);
    }
    if let Ok(child) = cmd.arg(text).spawn() {
        *app.state::<AppState>().speaking.lock().unwrap() = Some(child);
    }
}

/// The window in front, captured now (only it, only now), as a JPEG data: URL.
fn front_window_shot() -> Option<String> {
    let script = r#"tell application "System Events"
        set p to first application process whose frontmost is true
        set {x, y} to position of front window of p
        set {w, h} to size of front window of p
    end tell
    return (x as text) & "," & (y as text) & "," & (w as text) & "," & (h as text)"#;
    let out = Command::new("osascript").args(["-e", script]).output().ok()?;
    let rect = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if rect.split(',').count() != 4 {
        return None;
    }
    let path = std::env::temp_dir().join("aurora-window.jpg");
    let ok = Command::new("screencapture").args(["-x", "-t", "jpg", "-R", &rect]).arg(&path).status().ok()?.success();
    let bytes = if ok { std::fs::read(&path).ok() } else { None };
    let _ = std::fs::remove_file(&path);
    bytes.map(|b| format!("data:image/jpeg;base64,{}", base64::engine::general_purpose::STANDARD.encode(b)))
}

fn talk_key(app: &AppHandle, pressed: bool) {
    let s = app.state::<AppState>().settings.lock().unwrap().clone();
    if !s.voice {
        return;
    }
    let _ = app.emit_to("orb", "listen", pressed);
    if pressed {
        stop_speaking(app); // speaking again stops the answer
        let a = app.clone();
        std::thread::spawn(move || {
            let shot = front_window_shot();
            *a.state::<AppState>().shot.lock().unwrap() = shot;
        });
    }
}

// ------------------------------------------------------------------------------------------- what you are doing

fn frontmost() -> Option<(String, String)> {
    let script = r#"tell application "System Events"
        set p to first application process whose frontmost is true
        set n to name of p
        set t to ""
        try
            set t to name of front window of p
        end try
    end tell
    return n & linefeed & t"#;
    let out = Command::new("osascript").args(["-e", script]).output().ok()?;
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    let mut lines = text.trim_end().splitn(2, '\n');
    Some((lines.next()?.to_string(), lines.next().unwrap_or("").to_string()))
}

fn watch_context(app: AppHandle) {
    std::thread::spawn(move || {
        let mut last = String::new();
        let mut last_paper = String::new();
        loop {
            std::thread::sleep(Duration::from_secs(4));
            let s = app.state::<AppState>().settings.lock().unwrap().clone();
            if !s.report_context {
                continue;
            }
            let Some((name, title)) = frontmost() else { continue };
            if name == "AURORA" || BROWSERS.contains(&name.as_str()) || title.is_empty() {
                continue; // itself, and browsers (the extension knows the page better)
            }
            let key = format!("{name}|{title}");
            if key == last {
                continue;
            }
            last = key;
            let c = client(&s);
            let body = serde_json::json!({ "app": name, "title": title, "via": "orb" });
            let Ok(reported) = c.post(api(&s, "/memory/context")).json(&body).send().and_then(|r| r.json::<serde_json::Value>()) else {
                continue;
            };
            refresh_panel(&app);
            // a paper open in another PDF reader (told from its file name): what you saved that argues with it, in a
            // sentence by the orb (AURORA cannot mark that reader's margin)
            let paper = reported["paper_id"].as_str().unwrap_or("").to_string();
            if !paper.is_empty() && paper != last_paper {
                last_paper = paper.clone();
                if let Ok(d) = c.get(api(&s, &format!("/memory/dissent?paper={}", urlencoding::encode(&paper)))).send()
                    .and_then(|r| r.json::<serde_json::Value>()) {
                    let items = d["items"].as_array().cloned().unwrap_or_default();
                    if !items.is_empty() {
                        let pages: Vec<String> = items.iter().take(4).map(|it| format!("p. {}", it["page"].as_u64().unwrap_or(0) + 1)).collect();
                        let head = format!("{} of your saves argue with this paper ({})", items.len(), pages.join(", "));
                        let body = items[0]["why"].as_str().unwrap_or("").chars().take(140).collect::<String>();
                        let app2 = app.clone();
                        let _ = app.run_on_main_thread(move || show_peek(&app2, &head, &body));
                        continue;
                    }
                }
            }
            // say so when something you saved bears on it
            if let Ok(r) = c.get(api(&s, "/memory/surface?ctx=current")).send().and_then(|r| r.json::<serde_json::Value>()) {
                let n = r["surface"].as_array().map(|a| a.len()).unwrap_or(0);
                let conflicts = r["conflicts"].as_u64().unwrap_or(0);
                if n > 0 {
                    let head = format!("{} of your saves surface{}", n, if conflicts > 0 { format!(", {conflicts} in conflict") } else { String::new() });
                    let body = r["surface"][0]["claim"].as_str().unwrap_or("").chars().take(120).collect::<String>();
                    let app2 = app.clone();
                    let _ = app.run_on_main_thread(move || show_peek(&app2, &head, &body));
                }
            }
        }
    });
}

fn watch_alerts(app: AppHandle) {
    // what you would regret missing: a dot on the orb, a bubble, and a macOS notification, once each
    std::thread::spawn(move || {
        let mut told: Vec<String> = Vec::new();
        loop {
            std::thread::sleep(Duration::from_secs(60));
            let s = app.state::<AppState>().settings.lock().unwrap().clone();
            let Ok(list) = client(&s).get(api(&s, "/memory/alerts?unseen=true&hours=48")).send()
                .and_then(|r| r.json::<serde_json::Value>()) else { continue };
            let now: Vec<&serde_json::Value> = list.as_array().map(|a| a.iter().filter(|x| x["tier"] == "now").collect()).unwrap_or_default();
            let _ = app.emit_to("orb", "alerts", now.len());
            for a in now {
                let id = a["alert_id"].as_str().unwrap_or("").to_string();
                if id.is_empty() || told.contains(&id) {
                    continue;
                }
                told.push(id);
                let title = a["title"].as_str().unwrap_or("AURORA").replace('"', "'");
                let body = a["body"].as_str().unwrap_or("").chars().take(200).collect::<String>().replace('"', "'").replace('\n', " ");
                let _ = Command::new("osascript").args(["-e", &format!("display notification \"{body}\" with title \"{title}\"")]).spawn();
                let app2 = app.clone();
                let (h, b) = (title.clone(), body.clone());
                let _ = app.run_on_main_thread(move || show_peek(&app2, &h, &b));
            }
        }
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if shortcut.matches(Modifiers::ALT | Modifiers::SHIFT, Code::KeyN) {
                        if event.state() == ShortcutState::Pressed {
                            open_note(app);
                        }
                    } else if shortcut.matches(Modifiers::ALT | Modifiers::SHIFT, Code::Space) {
                        // hold to talk, let go to send
                        talk_key(app, event.state() == ShortcutState::Pressed);
                    } else if event.state() == ShortcutState::Pressed && shortcut.matches(Modifiers::ALT, Code::Space) {
                        let _ = toggle_panel(app, false);
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            get_settings, set_settings, capture, capture_file, source, open_panel, open_settings, peek, hear, listen_digest, keep_thought, snap_orb, quit
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            app.manage(AppState {
                settings: Mutex::new(load_settings(&handle)),
                server: Mutex::new(None),
                speaking: Mutex::new(None),
                shot: Mutex::new(None),
            });
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory); // no Dock icon: the orb is the app
            place_orb(&handle);
            let h = handle.clone();
            std::thread::spawn(move || start_server(&h));
            app.global_shortcut().register(Shortcut::new(Some(Modifiers::ALT), Code::Space))?;
            app.global_shortcut().register(Shortcut::new(Some(Modifiers::ALT | Modifiers::SHIFT), Code::Space))?;
            app.global_shortcut().register(Shortcut::new(Some(Modifiers::ALT | Modifiers::SHIFT), Code::KeyN))?;
            watch_alerts(handle.clone());
            watch_context(handle);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while starting AURORA")
        .run(|app, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                stop_server(app);
            }
        });
}
