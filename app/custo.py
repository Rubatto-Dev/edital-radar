"""Daily spend cap for cascade layer 3 — the only step that costs money.

Estourou o teto → TetoEstourado, so the caller stops and notifies instead of
quietly burning money past what a self-funded intern can absorb. Every
attempt is logged to an append-only ledger; that ledger doubles as the
cost/latency record Phase 3 (observability) consumes, so the eval loop stays
decoupled from Postgres.
"""

import datetime
import json
from pathlib import Path

TETO_PADRAO_USD = 0.40
CAMINHO_PADRAO = Path(__file__).resolve().parent.parent / "data" / "custo-ledger.jsonl"


class TetoEstourado(Exception):
    pass


def _hoje_utc() -> str:
    """The ledger's day key. UTC rather than local time because the file is
    written from the host and read from the container, which do not share a
    zone — keying on local time makes the cap count a different "today"
    depending on where the process ran."""
    return datetime.datetime.now(datetime.UTC).date().isoformat()


class Orcamento:
    def __init__(self, teto_usd: float = TETO_PADRAO_USD, caminho: Path | str = CAMINHO_PADRAO):
        self.teto_usd = teto_usd
        self.caminho = Path(caminho)

    def _gasto_hoje(self) -> float:
        if not self.caminho.exists():
            return 0.0
        hoje = _hoje_utc()
        total = 0.0
        for linha in self.caminho.read_text(encoding="utf-8").splitlines():
            if not linha.strip():
                continue
            registro = json.loads(linha)
            if registro["data"] == hoje:
                total += registro["custo_usd"]
        return total

    def verificar(self) -> None:
        gasto = self._gasto_hoje()
        if gasto >= self.teto_usd:
            raise TetoEstourado(
                f"teto diário de ${self.teto_usd:.2f} atingido (${gasto:.4f} já gastos)"
            )

    def registrar(self, custo_usd: float, latencia_s: float = 0.0) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        registro = {
            "data": _hoje_utc(),
            "custo_usd": custo_usd,
            "latencia_s": latencia_s,
        }
        with open(self.caminho, "a", encoding="utf-8") as f:
            f.write(json.dumps(registro, ensure_ascii=False) + "\n")
