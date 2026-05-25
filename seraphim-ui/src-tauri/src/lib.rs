use std::net::TcpStream;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 7272;

struct BackendProcess(Mutex<Option<Child>>);

fn kill_backend(app: &tauri::AppHandle) {
    if let Some(state) = app.try_state::<BackendProcess>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
            }
        }
    }
}

fn wait_for_backend(timeout_secs: u64) -> bool {
    let addr = format!("{}:{}", BACKEND_HOST, BACKEND_PORT);
    let deadline = std::time::Instant::now() + Duration::from_secs(timeout_secs);
    while std::time::Instant::now() < deadline {
        if TcpStream::connect_timeout(&addr.parse().unwrap(), Duration::from_millis(300)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    false
}

// Called from the frontend (wake word detected while window is hidden)
#[tauri::command]
fn show_main_window(app: tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![show_main_window])
        .setup(|app| {
            // ── System tray ───────────────────────────────────────────────────────
            let show_item = MenuItem::with_id(app, "show", "Ouvrir Seraphim", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Quitter", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &quit_item])?;

            TrayIconBuilder::new()
                .icon(app.default_window_icon().expect("No window icon").clone())
                .menu(&menu)
                .tooltip("Seraphim")
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "quit" => {
                        kill_backend(app);
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    // Left-click on tray icon → show window
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                })
                .build(app)?;

            // ── Microphone permission for WebView2 (Windows) ──────────────────────
            // WebView2 caches permission denials independently of Windows settings.
            // SetPermissionState overrides the cache for all Tauri origins.
            #[cfg(target_os = "windows")]
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.with_webview(|wv| unsafe {
                    use webview2_com::{
                        PermissionRequestedEventHandler,
                        SetPermissionStateCompletedHandler,
                        Microsoft::Web::WebView2::Win32::{
                            ICoreWebView2_13, ICoreWebView2Profile4,
                            COREWEBVIEW2_PERMISSION_KIND, COREWEBVIEW2_PERMISSION_KIND_MICROPHONE,
                            COREWEBVIEW2_PERMISSION_STATE_ALLOW,
                        },
                    };
                    use windows_core::{Interface, HSTRING};

                    let Ok(webview) = wv.controller().CoreWebView2() else { return };

                    if let Ok(wv13) = webview.cast::<ICoreWebView2_13>() {
                        if let Ok(profile) = wv13.Profile() {
                            if let Ok(profile4) = profile.cast::<ICoreWebView2Profile4>() {
                                for origin in &[
                                    "http://localhost:1420",
                                    "https://tauri.localhost",
                                    "tauri://localhost",
                                ] {
                                    let _ = profile4.SetPermissionState(
                                        COREWEBVIEW2_PERMISSION_KIND_MICROPHONE,
                                        &HSTRING::from(*origin),
                                        COREWEBVIEW2_PERMISSION_STATE_ALLOW,
                                        &SetPermissionStateCompletedHandler::create(Box::new(|_| Ok(()))),
                                    );
                                }
                            }
                        }
                    }

                    let mut token = Default::default();
                    let _ = webview.add_PermissionRequested(
                        &PermissionRequestedEventHandler::create(Box::new(|_, args| {
                            let Some(args) = args else { return Ok(()) };
                            let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
                            args.PermissionKind(&mut kind)?;
                            if kind == COREWEBVIEW2_PERMISSION_KIND_MICROPHONE {
                                args.SetState(COREWEBVIEW2_PERMISSION_STATE_ALLOW)?;
                            }
                            Ok(())
                        })),
                        &mut token,
                    );
                });
            }

            // ── Backend process ───────────────────────────────────────────────────
            let resource_dir = app
                .path()
                .resource_dir()
                .expect("Failed to resolve resource dir");

            #[cfg(target_os = "windows")]
            let server_exe = resource_dir
                .join("seraphim-server")
                .join("seraphim-server.exe");
            #[cfg(not(target_os = "windows"))]
            let server_exe = resource_dir
                .join("seraphim-server")
                .join("seraphim-server");

            if server_exe.exists() {
                let child = Command::new(&server_exe)
                    .args(["serve", "--host", BACKEND_HOST, "--port", &BACKEND_PORT.to_string()])
                    .spawn()
                    .unwrap_or_else(|e| panic!("Failed to start seraphim-server: {}", e));

                app.manage(BackendProcess(Mutex::new(Some(child))));

                let app_handle = app.handle().clone();
                std::thread::spawn(move || {
                    if !wait_for_backend(30) {
                        eprintln!("[seraphim] Backend did not start within 30s");
                        let _ = app_handle.emit("backend-error", "Backend failed to start");
                    }
                });
            } else {
                eprintln!(
                    "[seraphim] Backend not bundled at {:?} — assuming already running",
                    server_exe
                );
            }

            // ── Global shortcut: Ctrl+Space → toggle listening ────────────────────
            let shortcut = Shortcut::new(Some(Modifiers::CONTROL), Code::Space);
            app.handle().global_shortcut().on_shortcut(shortcut, |app_handle, _shortcut, event| {
                if event.state() == ShortcutState::Pressed {
                    // Show window if hidden, then toggle listening
                    if let Some(w) = app_handle.get_webview_window("main") {
                        let _ = w.show();
                        let _ = w.set_focus();
                    }
                    let _ = app_handle.emit("toggle-listening", ());
                }
            })?;

            Ok(())
        })
        .on_window_event(|window, event| {
            // Hide to tray instead of quitting — backend and wake word stay alive.
            // Use tray "Quitter" to actually exit.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
