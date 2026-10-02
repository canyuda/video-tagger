// Tauri 2 shell — kept as thin as possible (docs/04 §2):
// spawn the Python sidecar binary, parse its one-line handshake, hand
// {port, token} to the frontend, and provide tray + notifications.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use tauri::{
    Emitter, Manager, RunEvent,
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};

#[derive(Clone, Serialize, Deserialize)]
struct SidecarInfo {
    port: u16,
    token: String,
}

#[derive(Default)]
struct AppState {
    info: Mutex<Option<SidecarInfo>>,
    child: Mutex<Option<CommandChild>>,
}

#[tauri::command]
fn sidecar_info(state: tauri::State<AppState>) -> Result<SidecarInfo, String> {
    state
        .info
        .lock()
        .unwrap()
        .clone()
        .ok_or_else(|| "sidecar not ready".to_string())
}

#[tauri::command]
fn notify(app: tauri::AppHandle, title: String, body: String) {
    use tauri_plugin_notification::NotificationExt;
    let _ = app
        .notification()
        .builder()
        .title(title)
        .body(body)
        .show();
}

// Sidecar stdout/stderr land here so first-launch failures are diagnosable
// (the window alone only shows "sidecar not ready").
fn sidecar_log_path(app: &tauri::AppHandle) -> std::path::PathBuf {
    let dir = app
        .path()
        .app_data_dir()
        .unwrap_or_else(|_| std::path::PathBuf::from("/tmp"));
    let _ = std::fs::create_dir_all(&dir);
    dir.join("sidecar.log")
}

fn log_sidecar(app: &tauri::AppHandle, line: &str) {
    use std::io::Write;
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(sidecar_log_path(app))
    {
        let _ = writeln!(file, "{}", line);
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![sidecar_info, notify])
        .setup(|app| {
            let handle = app.handle().clone();

            // -- sidecar lifecycle ------------------------------------------------
            let sidecar = app.shell().sidecar("video-tagger-sidecar")?;
            let (mut rx, child) = sidecar.spawn()?;
            handle.state::<AppState>().child.lock().unwrap().replace(child);
            std::thread::spawn(move || {
                while let Some(event) = rx.blocking_recv() {
                    match event {
                        CommandEvent::Stdout(line) => {
                            let text = String::from_utf8_lossy(&line).to_string();
                            log_sidecar(&handle, &format!("OUT {text}"));
                            if let Some(payload) = text.strip_prefix("SIDECAR_READY ") {
                                match serde_json::from_str::<SidecarInfo>(payload.trim()) {
                                    Ok(info) => {
                                        *handle.state::<AppState>().info.lock().unwrap() =
                                            Some(info.clone());
                                        let _ = handle.emit("sidecar-ready", &info);
                                    }
                                    Err(e) => {
                                        log_sidecar(&handle, &format!("HANDSHAKE-ERROR {e}"));
                                    }
                                }
                            }
                        }
                        CommandEvent::Stderr(line) => {
                            log_sidecar(&handle, &format!("ERR {}", String::from_utf8_lossy(&line)));
                        }
                        CommandEvent::Error(err) => {
                            log_sidecar(&handle, &format!("SPAWN-ERROR {err}"));
                            let _ = handle.emit("sidecar-failed", err.clone());
                        }
                        CommandEvent::Terminated(status) => {
                            log_sidecar(&handle, &format!("TERMINATED {status:?}"));
                            let _ = handle.emit(
                                "sidecar-failed",
                                format!("sidecar 进程退出: {:?}", status.code),
                            );
                        }
                        _ => {}
                    }
                }
            });

            // -- tray (D36: minimize-to-tray keeps batches running) ----------------
            let show = MenuItem::with_id(app, "show", "显示主窗口", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            TrayIconBuilder::with_id("main-tray")
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(window) = app.get_webview_window("main") {
                            let _ = window.show();
                            let _ = window.set_focus();
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Close button hides to tray instead of quitting (D36).
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app, event| {
            if let RunEvent::ExitRequested { .. } = event {
                // Window close is intercepted above; reaching here means real exit.
            }
        });
}
