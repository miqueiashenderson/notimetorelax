from sqlalchemy import event


def instalar_contador(engine):
    @event.listens_for(engine, "before_cursor_execute")
    def _contar(conn, cursor, statement, params, context, executemany):
        conn.info.setdefault("queries", []).append(statement)
    return engine
