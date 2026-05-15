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
