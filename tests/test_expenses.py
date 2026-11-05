def test_first_start_with_parallel_requests_never_fails(tmp_path):
    """Lena Runde 67: on the very first start the browser asks for several pages at once.
    Every new connection checks the schema; none of them may fail with "duplicate column"."""
    import threading

    from frits import store
    from frits.dbspalte import spalte_dazu

    for n in range(15):
        db = tmp_path / f"frits-{n}.db"
        fehler, tor = [], threading.Barrier(6)

        def oeffnen(db=db, fehler=fehler, tor=tor):
            tor.wait()
            try:
                store.connect(db).close()
            except Exception as e:  # noqa: BLE001 - the test collects every failure
                fehler.append(e)

        faeden = [threading.Thread(target=oeffnen) for _ in range(6)]
        for f in faeden:
            f.start()
        for f in faeden:
            f.join()
        assert fehler == []
    # a column that is already there (another connection was quicker) is no error
    conn = store.connect(tmp_path / "frits-0.db")
    spalte_dazu(conn, "expenses", "struck", "TEXT")
    conn.execute("ALTER TABLE expenses ADD COLUMN neu TEXT")
    spalte_dazu(conn, "expenses", "neu", "TEXT")
