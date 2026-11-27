// Never allocate a console window on Windows.  Release builds must stay GUI,
// and a debug build launched outside a terminal used to add its own console
// next to the app even after the worker's console was suppressed.
#![cfg_attr(windows, windows_subsystem = "windows")]

fn main() {
    lodestar_lib::run();
}
