use std::net::TcpStream;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Emitter, Manager, WindowEvent};

const BACKEND_HOST: &str = "127.0.0.1";
const BACKEND_PORT: u16 = 7272;

struct BackendProcess(Mutex<Option<Child>>);

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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            // Grant microphone permission for WebView2 — required for Web Speech API.
            // Windows system permission alone is not enough; WebView2 has its own layer that
            // caches deny decisions. SetPermissionState overrides the cache for all Tauri origins.
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

                    // Force-grant mic for every origin Tauri may use (erases any cached deny).
                    // SetPermissionState is on ICoreWebView2Profile4, reached via ICoreWebView2_13.
                    if let Ok(wv13) = webview.cast::<ICoreWebView2_13>() {
                        if let Ok(profile) = wv13.Profile() {
                            if let Ok(profile4) = profile.cast::<ICoreWebView2Profile4>() {
                                for origin in &[
                                    "http://localhost:1420",   // dev mode
                                    "https://tauri.localhost", // prod (some Tauri versions)
                                    "tauri://localhost",        // prod (other Tauri versions)
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

                    // Also handle future permission requests (e.g. after navigation)
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

            // Locate bundled seraphim-server executable
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

            if !server_exe.exists() {
                // Dev mode: seraphim server must be started manually
                eprintln!(
                    "[seraphim] Backend not bundled at {:?} — assuming already running",
                    server_exe
                );
                return Ok(());
            }

            let child = Command::new(&server_exe)
                .args(["serve", "--host", BACKEND_HOST, "--port", &BACKEND_PORT.to_string()])
                .spawn()
                .unwrap_or_else(|e| panic!("Failed to start seraphim-server: {}", e));

            app.manage(BackendProcess(Mutex::new(Some(child))));

            // Wait for backend ready in background thread, then focus window
            let app_handle = app.handle().clone();
            std::thread::spawn(move || {
                if wait_for_backend(30) {
                    // Backend ready — nothing special needed, webview auto-retries
                } else {
                    eprintln!("[seraphim] Backend did not start within 30s");
                    // Optionally emit an event to the frontend
                    let _ = app_handle.emit("backend-error", "Backend failed to start");
                }
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                // Kill sidecar when main window closes
                if let Some(state) = window.app_handle().try_state::<BackendProcess>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
