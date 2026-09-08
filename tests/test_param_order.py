"""Issue 05: pin the model/sensor parameter order against the firmware C structs.

`models/<m>.py::PARAM_NAMES` is the single source of truth on the Python side
(`api.protocol` and `gui.person_config_window` both reference it live, no
copy). These tests guard the two things that can still silently drift:

1. Python ``PARAM_NAMES`` vs the firmware C struct field order (golden files).
2. The single-source wiring: ``protocol`` must reference the module's list, not
   a copy of it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from api import protocol
from models import cambridge, deichmann, royparker, uva_padova
from models.types import ModelId, SensorId

_GOLDEN = Path(__file__).parent / "param_order"

_MODEL_MODULE = {
    ModelId.CAMBRIDGE: cambridge,
    ModelId.UVA_PADOVA: uva_padova,
    ModelId.ROYPARKER: royparker,
    ModelId.DEICHMANN: deichmann,
}
_MODEL_GOLDEN = {
    ModelId.CAMBRIDGE: "cambridge.golden",
    ModelId.UVA_PADOVA: "uva_padova.golden",
    ModelId.ROYPARKER: "royparker.golden",
    ModelId.DEICHMANN: "deichmann.golden",
}
_SENSOR_GOLDEN = {
    SensorId.IDEAL: "sensor_ideal.golden",
    SensorId.BRETON: "sensor_breton.golden",
    SensorId.FACCHINETTI: "sensor_facchinetti.golden",
}


def _golden(name: str) -> list[str]:
    return [ln.strip() for ln in (_GOLDEN / name).read_text().splitlines() if ln.strip()]


@pytest.mark.parametrize("model_id", list(ModelId))
def test_model_param_order_matches_firmware_struct(model_id):
    want = _golden(_MODEL_GOLDEN[model_id])
    assert want == _MODEL_MODULE[model_id].PARAM_NAMES
    assert protocol.model_param_names(model_id) == want


@pytest.mark.parametrize("sensor_id", list(SensorId))
def test_sensor_param_order_matches_firmware_struct(sensor_id):
    assert protocol.sensor_param_names(sensor_id) == _golden(_SENSOR_GOLDEN[sensor_id])


@pytest.mark.parametrize("model_id", list(ModelId))
def test_protocol_references_the_module_list_not_a_copy(model_id):
    # identity, not equality: proves there is one list, not two that agree today
    assert protocol.model_param_names(model_id) is _MODEL_MODULE[model_id].PARAM_NAMES


def test_param_count_fits_the_firmware_buffer():
    # MAX_MODEL_PARAMS / MAX_SENSOR_PARAMS in the firmware's sim_config.h
    assert max(len(m.PARAM_NAMES) for m in _MODEL_MODULE.values()) <= protocol.MAX_MODEL_PARAMS
    assert max(len(protocol.sensor_param_names(s)) for s in SensorId) <= protocol.MAX_SENSOR_PARAMS
