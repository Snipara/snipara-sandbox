"""A run's response carries the end of its log; the persisted receipt does not change."""

from rlm.validation.runner import OUTPUT_TAIL_CHARS, with_output_tail

RUN = "a" * 32


def store_with_log(tmp_path, text):
    run = tmp_path / RUN
    run.mkdir(mode=0o700)
    (run / "output.log").write_bytes(text.encode())
    return tmp_path


def test_the_end_of_the_log_is_added(tmp_path):
    store = store_with_log(
        tmp_path, "collected 3 items\nFAILED test_a.py::test_x - assert 1 == 2\n"
    )
    receipt = {"run_id": RUN, "status": "failed", "exit_code": 1}
    answer = with_output_tail(receipt, store)
    assert answer["output_tail"].endswith("assert 1 == 2\n")
    assert answer["output_tail_truncated"] is False
    assert receipt == {"run_id": RUN, "status": "failed", "exit_code": 1}, (
        "the receipt is not mutated"
    )


def test_a_long_log_keeps_its_end(tmp_path):
    store = store_with_log(tmp_path, "x" * 5000 + "THE END")
    answer = with_output_tail({"run_id": RUN}, store)
    assert len(answer["output_tail"]) == OUTPUT_TAIL_CHARS
    assert answer["output_tail"].endswith("THE END")
    assert answer["output_tail_truncated"] is True


def test_nothing_is_added_without_a_log_or_a_valid_run(tmp_path):
    assert with_output_tail({"status": "validation_in_progress"}, tmp_path) == {
        "status": "validation_in_progress"
    }
    assert with_output_tail({"run_id": "../../etc"}, tmp_path) == {"run_id": "../../etc"}
    assert with_output_tail({"run_id": "b" * 32}, tmp_path) == {"run_id": "b" * 32}
