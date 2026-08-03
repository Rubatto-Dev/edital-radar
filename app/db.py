import os

from psycopg_pool import ConnectionPool


# Falls back to the docker-compose database as seen from the host, so the CLI
# and the tests work without ceremony. Inside the container DATABASE_URL is set.
#
# The fallback is assembled from the same variables compose reads rather than
# written out as a URL with credentials in it. Two reasons, and neither is the
# linter: a literal `user:password@host` in source is the pattern that gets
# deployed by accident, and the old hardcoded string ignored POSTGRES_PASSWORD
# entirely — so exporting it moved the container and left the host CLI behind.
def _url_padrao() -> str:
    usuario = os.environ.get("POSTGRES_USER", "edital")
    senha = os.environ.get("POSTGRES_PASSWORD", "edital")
    porta = os.environ.get("POSTGRES_PORT", "5440")
    banco = os.environ.get("POSTGRES_DB", "edital_radar")
    return f"postgresql://{usuario}:{senha}@localhost:{porta}/{banco}"


DATABASE_URL = os.environ.get("DATABASE_URL") or _url_padrao()

pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False)


def normalizar_valor(valor):
    """0.0 from the PNCP means confidential or unreported, never zero.

    Storing it as 0.0 makes a range filter discard good tenders in silence,
    so it becomes NULL — which reads as unknown everywhere downstream.
    """
    if valor is None or valor == 0:
        return None
    return valor
