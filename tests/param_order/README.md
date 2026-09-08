# Golden param-order files

One file per model / sensor, one field name per line, in the order the field
appears in the **firmware C parameter struct**
(`firmware/peripheral_cgms/src/models/cgmsim_*.h`). Sensor files list only the
*configurable* subset the protocol sends (internal state fields excluded).

This is the wire order: `api.protocol` packs float32 params in exactly this
sequence, and the firmware reads them back the same way. A silent drift between
the Python `PARAM_NAMES` and this order corrupts every expected-vs-received
comparison in the thesis.

`tests/test_param_order.py` asserts each model/sensor's Python `PARAM_NAMES`
equals its golden file. **When a firmware struct changes, update the matching
`.golden` file by hand** — the diff is the review signal.

See `.scratch/thesis-stabilization/issues/05-collapse-param-order-invariant.md`.
