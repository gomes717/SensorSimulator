# G6 codec: Python + pure C, shared golden vectors

Status: done 2026-10-18 (Python + C, 61 tests; vectors cross-checked against FIPS-197, the CRC check value and openssl)
Type: task
Blocked by: 01

## Build

- `src/api/dexcom_g6.py`: CRC-16/XMODEM, `challenge_hash(token, tx_id)`, builders/parsers for the
  auth messages (`01/03/04/05/06/07`) and the data messages (`4E/4F`, `30/31`, `2E/2F`, `24/25`,
  version, battery, session, calibrate, disconnect).
- `firmware/peripheral_cgms/src/dexcom_g6.c/.h`: the same in C with **no Zephyr includes**
  (CRC, framing, `handle_request(state, in, out)`); AES is injected as a function pointer so host
  tests can use a reference AES and the board uses PSA.
- `tests/data/dexcom_g6_vectors.json`: golden vectors (CRC cases, hash cases for a fixed token +
  ID, one encoded message of each type). Python tests and a host C test (mintest, like `cgmsim/tests/`)
  both read it, so the two implementations cannot drift.

## Done when

- Both test suites green, including: bad CRC, short packet, unknown opcode, wrong-length token.
- Hash vectors cross-checked against an independent AES (`cryptography` or OpenSSL) and CRC against
  xDrip's `FastCRC16Test`.
- `api/protocol.py`'s old 14-byte Dexcom decoder is left alone until ticket 06.
