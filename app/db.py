import os

from psycopg_pool import ConnectionPool

DATABASE_URL = os.environ["DATABASE_URL"]

pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False)


def normalizar_valor(valor):
    """0.0 from the PNCP means confidential or unreported, never zero.

    Storing it as 0.0 makes a range filter discard good tenders in silence,
    so it becomes NULL — which reads as unknown everywhere downstream.
    """
    if valor is None or valor == 0:
        return None
    return valor
