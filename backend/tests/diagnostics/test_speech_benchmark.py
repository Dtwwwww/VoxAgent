from voxagent.diagnostics.speech_benchmark import measure_call
from voxagent.speech.model_manifest import SPEECH_MODELS


def test_speech_manifest_has_unique_names_and_https_urls():
    assert len({model.name for model in SPEECH_MODELS}) == 3
    assert all(model.url.startswith("https://github.com/k2-fsa/") for model in SPEECH_MODELS)

def test_measure_call_records_elapsed_and_result(monkeypatch):
    times = iter([5.0, 5.25])
    monkeypatch.setattr("voxagent.diagnostics.speech_benchmark.perf_counter", lambda: next(times))
    timed = measure_call("tts", lambda: 24000)
    assert timed.label == "tts"
    assert timed.elapsed_seconds == 0.25
    assert timed.result == 24000
