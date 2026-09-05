"""Reader regression tests. The user's source file is optional and read-only."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from roompilot.importers import (
    MeasurementImportError, _calibration, _read_mdat,
    import_measurements, infer_channel, infer_position,
)


def inert_object(fields, name="roomeqwizard.CalData"):
    """Shape of a passive javaobj bean, without serializing any executable code."""
    class Field:
        def __init__(self, name):
            self.name = name
    return SimpleNamespace(classdesc=SimpleNamespace(name=name),
                           field_data={"class": {Field(k): v for k, v in fields.items()}})


def write_text(tmp_path, text, suffix=".txt", encoding="utf-8"):
    path = tmp_path / ("L_P0_A" + suffix)
    path.write_text(text, encoding=encoding)
    return path


def test_text_csv_phase_metadata_and_context(tmp_path):
    path = write_text(tmp_path, "* Measurement: R P+10 A\n* Mic/Meter Cal file: mic123.txt\n* Sample Rate: 48 kHz\n* Smoothing: None\n* Freq(Hz), SPL(dB), Phase(degrees)\n30.0,75.0,-1\n60,79,2\n120,72,-3\n", ".csv")
    items = import_measurements(str(path), role="verification", applied_peq_id="v1")
    assert len(items) == 1
    m = items[0]
    assert (m["channel"], m["position"], m["role"], m["applied_peq_id"]) == ("R", "P+10", "verification", "v1")
    assert m["frequency"] == [30., 60., 120.]
    assert m["phase"] == [-1., 2., -3.]
    assert m["metadata"]["sample_rate"] == 48000
    assert m["metadata"]["cal_status"] == "loaded"
    assert m["metadata"]["clipping"] is None
    json.dumps(items, allow_nan=False)


@pytest.mark.parametrize("body", ["30\t75\n60\t79\n", "30.0 75.0\n60.0 79.0\n", "30,0;75,0\n60,0;79,0\n"])
def test_text_separators_and_no_cal_does_not_mean_missing(tmp_path, body):
    m = import_measurements(str(write_text(tmp_path, body)))[0]
    assert m["frequency"] == [30, 60]
    assert m["spl"] == [75, 79]
    assert m["metadata"]["cal_status"] == "unknown"
    assert "phase" not in m


def test_explicit_no_cal_warning(tmp_path):
    path = write_text(tmp_path, "* Mic/Meter Cal file: No cal file\n30 75\n60 79\n")
    assert import_measurements(str(path))[0]["metadata"]["cal_status"] == "missing"


def test_utf16_and_dc(tmp_path):
    path = write_text(tmp_path, "* Measurement: L+R LR_P0\n0 0\n30 75\n60 79\n", encoding="utf-16")
    m = import_measurements(str(path))[0]
    assert m["channel"] == "LR"
    assert m["frequency"][0] == 30
    assert "DC" in m["metadata"]["notes"]


def test_rew_standard_headers_do_not_confuse_source_with_title(tmp_path):
    body = "* Source: EXCL: UMIK-2, R, volume: no control\n* Format: 128k Log Swept Sine, 1 sweep at -12.0 dBFSwith no timing reference\n* Measurement: L L_P0_A\n* Smoothing: None\n* Freq(Hz) SPL(dB) Phase(degrees)\n30 59.356 0\n60 69.373 1\n"
    m = import_measurements(str(write_text(tmp_path, body)))[0]
    assert m["name"] == "L L_P0_A"
    assert m["channel"] == "L"
    assert "UMIK-2" in m["metadata"]["input_device"]
    assert m["metadata"]["sweep_length"] == 131072
    assert m["metadata"]["sweep_level_dbfs"] == -12
    assert m["metadata"]["cal_status"] == "unknown"


@pytest.mark.parametrize("body", [
    "30 75\n20 79\n", "30 75\n30 79\n", "30 NaN\n60 79\n",
    "30 75 0\n60 79\n", "30 75\n60 damaged\n", "30 75 1 8\n60 79 2 9\n",
    "* Freq(Hz) Magnitude(dBFS) Phase\n30 -30 0\n60 -29 0\n",
    "* Freq(Hz) Impedance(Ohms) Phase\n30 4 0\n60 5 0\n",
])
def test_reject_ambiguous_or_corrupt_text(tmp_path, body):
    with pytest.raises(MeasurementImportError):
        import_measurements(str(write_text(tmp_path, body)))


def test_cal_is_per_measurement_and_requires_curve():
    loaded = inert_object({"sourceName": "mic.txt", "freqArray": [20, 1000, 20000], "gainArray": [1, 0, 2], "serialNum": 123})
    empty = inert_object({"sourceName": "", "freqArray": None, "gainArray": None, "inverseCSelected": False})
    name_only = inert_object({"sourceName": "mic.txt", "freqArray": None, "gainArray": None})
    records = [{"meterCal": loaded}, {"meterCal": empty}, {}, {"meterCal": None}, {"meterCal": name_only}]
    parsed = [_calibration(r) for r in records]
    assert [m["cal_status"] for m in parsed] == ["loaded", "missing", "unknown", "missing", "unknown"]
    assert parsed[0]["cal_serial"] == "123"
    assert parsed[0]["cal_points"] == 3
    # A malformed second curve cannot borrow the first record's calibration.
    malformed = inert_object({"sourceName": "mic.txt", "freqArray": [100, 20], "gainArray": [0, 1]})
    assert _calibration({"meterCal": malformed})["cal_status"] == "unknown"


def test_channel_suggestion_respects_recorded_output_route():
    assert infer_channel("L_P0_A", "DAC, LINE_OUT, R, volume: 1.0") == "R"
    assert infer_channel("living room") == "Unknown"
    assert infer_channel("LR_P0") == "LR"
    assert infer_position("R_P-10_A") == "P-10"


def test_bad_mdat_header_and_cancel(tmp_path):
    path = tmp_path / "bad.mdat"
    path.write_bytes(b"some mic.txt L_P0_A file bytes")
    with pytest.raises(MeasurementImportError, match="MDAT"):
        import_measurements(str(path))
    with pytest.raises(MeasurementImportError, match="取消"):
        import_measurements(str(path), cancel=lambda: True)


def test_native_axis_length_checks(monkeypatch, tmp_path):
    import javaobj.v2
    fields = {"splValues": [70, 72], "startFreq": 30., "endFreq": 60., "dataLength": 2,
              "isLogSpaced": False, "freqStep": 30., "sourceType": 5, "shortDesc": "L test"}
    count = SimpleNamespace(data=b"\0\0\0\1")
    obj = inert_object(fields, "roomeqwizard.MeasData")
    monkeypatch.setattr(javaobj.v2, "loads", lambda _: ["REW Measurement Data File V2", None, "", count, obj])
    data = b"\xac\xed\x00\x05t\x00\x1cREW Measurement Data File V2"
    result = _read_mdat(data, tmp_path / "x.mdat")
    assert result[0]["spl"] == [70, 72]
    assert result[0]["metadata"]["cal_status"] == "unknown"
    obj = inert_object({**fields, "endFreq": 90}, "roomeqwizard.MeasData")
    with pytest.raises(MeasurementImportError, match="頻率軸"):
        _read_mdat(data, tmp_path / "x.mdat")
    count.data = b"\0\0\0\2"
    with pytest.raises(MeasurementImportError, match="量測數量"):
        _read_mdat(data, tmp_path / "x.mdat")


SAMPLE = Path(os.environ.get("ROOMPILOT_TEST_MDAT", "C:/Users/Jimmy/Downloads/UMIK-2/8030C_ADI2_P0_Baseline.mdat"))


@pytest.mark.skipif(not SAMPLE.exists(), reason="User's private MDAT fixture is intentionally not distributed")
def test_real_five_measurement_mdat_is_lossless_read_only():
    before = hashlib.sha256(SAMPLE.read_bytes()).hexdigest()
    timestamps = SAMPLE.stat().st_mtime_ns
    progress = []
    items = import_measurements(str(SAMPLE), progress=progress.append)
    assert [m["name"] for m in items] == ["L L_P0_A", "L L_P0_B", "R R_P0_A", "R R_P0_B", "L+R LR_P0"]
    assert [m["channel"] for m in items] == ["L", "L", "R", "R", "LR"]
    for m in items:
        assert len(m["frequency"]) == len(m["spl"]) == len(m["phase"]) == 54533
        assert m["frequency"][0] == 30.029296875
        assert m["frequency"][-1] == 20000.244140625
        assert m["metadata"]["cal_status"] == "loaded"
        assert m["metadata"]["cal_name"] == "8112838.txt"
        assert m["metadata"]["cal_serial"] == "8112838"
        assert m["metadata"]["cal_points"] == 615
        assert m["metadata"]["sample_rate"] == 48000
        assert m["metadata"]["sweep_length"] == 131072
        assert m["metadata"]["sweep_level_dbfs"] == -12
        assert m["metadata"]["rew_version"] == "5.31.3"
        assert m["metadata"]["clipping"] is None
    # Reference floats from REW's saved SPL arrays, without double-applying Cal.
    assert np.allclose(np.array(items[0]["spl"])[[0, 82, 273, 27224]],
                       [59.35588455200195, 69.37264251708984, 82.611083984375, 73.07174682617188], atol=1e-6)
    assert progress[-1] == 1
    json.dumps(items, allow_nan=False)
    assert hashlib.sha256(SAMPLE.read_bytes()).hexdigest() == before
    assert SAMPLE.stat().st_mtime_ns == timestamps
