# User still shows connected after disconnect

Status: ready
Track: A
Phase: 1
Blocked by: —

## Problem

`docs/TODO.md`: "Usuário aparece conectado mesmo depois de desconectar (estado de
conexão não é limpo)." Connection state survives a disconnect somewhere between
`BleSession` teardown, `BluetoothWindow._sessions` / `_on_session_finished`
(`bluetooth_window.py:243-250`, `:276-283`) and the `MainWindow` tree row /
`_selected_user` / `_history` state.

## What to do

Diagnose which layer keeps the stale state — likely a signal left connected, a
`_sessions` entry not popped, or the tree row + alert badge not reset on
`disconnected` / `_on_session_finished`. No upfront design decision; fix at the
layer that owns the lifecycle.

## Done when

- After a disconnect (user-initiated and link-loss), the tree row shows
  disconnected, no further notifications are plotted for that user, and a
  reconnect starts clean.
- A regression test or scripted E2E step pins it (pairs with issue 11).
