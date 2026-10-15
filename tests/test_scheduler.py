from scheduler import WissellijstScheduler, SCHEDULER_TZ


def test_jobs_draaien_in_amsterdam():
    assert SCHEDULER_TZ == "Europe/Amsterdam"
    ws = WissellijstScheduler()
    ws.start()
    try:
        ws.update_job({"id": "tz000001", "naam": "TZ", "rotatie_schema": "dagelijks",
                       "rotatie_tijdstip": "08:00", "rotatie_dag": 0})
        nrt = ws.scheduler.get_job("wl_tz000001").next_run_time
        assert (nrt.hour, nrt.minute) == (8, 0)
        assert "Amsterdam" in str(nrt.tzinfo)

        ws.update_job({"id": "tz000001", "naam": "TZ", "rotatie_schema": "uit"})
        assert ws.scheduler.get_job("wl_tz000001") is None
        assert ws.get_jobs() == []
    finally:
        ws.shutdown()
