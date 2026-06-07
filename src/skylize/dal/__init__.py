"""
IF-DATA: the Data Access Layer — the sole holder of the database driver.

Only this package may import `asyncpg`. Business logic (app/) depends on the
repository *Protocols* in `ports.py`, never on the concrete implementations in
`repositories.py`. `memory.py` provides in-memory fakes for tests and the
no-infra local backend. Importing `skylize.dal.ports` or `skylize.dal.memory`
does NOT pull in asyncpg.
"""
