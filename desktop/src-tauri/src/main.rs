// AURORA on the macOS desktop.
//
// * The orb: a small ball at the edge of the screen. Drop a link, a selection or a file on it to save it into
//   your memory; click it for the memory panel; it says in a bubble when something you saved bears on what you
//   are doing.
// * The panel: the console's memory panel (`/panel`), in a window of its own.
// * What you are doing: the frontmost app and its window title (macOS Accessibility), sent to AURORA as context.
//   Browsers are left to the browser extension, which knows the page and the selection.
// * The server: the local AURORA console is started in the background when it is not running.
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
struct Settings {
    server: String,
    token: String,
    start_server: bool,
    server_command: String,
    report_context: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            server: "http://localhost:8200".into(),
            token: String::new(),
            start_server: true,
            server_command: "aurora serve --port 8200".into(),
            report_context: true,
        }
    }
}

struct AppState {
    settings: Mutex<Settings>,
    server: Mutex<Option<Child>>,
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
        .title("AURORA settings").inner_size(480.0, 520.0).resizable(false).build().map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn peek(app: AppHandle, head: String, body: String) -> Result<(), String> {
    show_peek(&app, &head, &body);
    Ok(())
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
        .title("AURORA").inner_size(460.0, 840.0).build().map_err(|e| e.to_string())?;
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
            if c.post(api(&s, "/memory/context")).json(&body).send().is_err() {
                continue;
            }
            refresh_panel(&app);
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

fn main() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if event.state() == ShortcutState::Pressed && shortcut.matches(Modifiers::ALT, Code::Space) {
                        let _ = toggle_panel(app, false);
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            get_settings, set_settings, capture, capture_file, source, open_panel, open_settings, peek, quit
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            app.manage(AppState { settings: Mutex::new(load_settings(&handle)), server: Mutex::new(None) });
            #[cfg(target_os = "macos")]
            app.set_activation_policy(tauri::ActivationPolicy::Accessory); // no Dock icon: the orb is the app
            place_orb(&handle);
            let h = handle.clone();
            std::thread::spawn(move || start_server(&h));
            app.global_shortcut().register(Shortcut::new(Some(Modifiers::ALT), Code::Space))?;
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
