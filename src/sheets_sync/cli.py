"""Linha de comando do sincronizador CRM <-> Google Sheets."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from bitrix24_client import from_env
from bitrix24_client.errors import BitrixError

from .merge import Acao, Origem
from .sync import Mapeamento, Sincronizador, abrir_planilha


def carregar_mapa(caminho: Path) -> Mapeamento:
    dados = json.loads(caminho.read_text("utf-8"))
    return Mapeamento(
        entidade=dados.get("entidade", "crm.deal"),
        chave_crm=dados.get("chave_crm", "ID"),
        coluna_chave=dados.get("coluna_chave", "ID"),
        campos=dados["campos"],
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="sync-sheets",
        description="Sincroniza CRM do Bitrix24 com Google Sheets nos dois sentidos.",
    )
    p.add_argument("--mapa", type=Path, default=Path("mapa.json"))
    p.add_argument("--base", type=Path, default=Path("state/base.json"))
    p.add_argument("--planilha", default=os.environ.get("SHEETS_ID", ""))
    p.add_argument("--aba", default=os.environ.get("SHEETS_ABA", "CRM"))
    p.add_argument(
        "--credenciais",
        type=Path,
        default=Path(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "service-account.json")),
    )
    p.add_argument(
        "--vencedor",
        choices=[o.value for o in Origem],
        default=Origem.CRM.value,
        help="quem ganha quando os dois lados mudaram o mesmo campo",
    )
    p.add_argument("--executar", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.planilha:
        print("informe --planilha ou SHEETS_ID", file=sys.stderr)
        return 2

    try:
        sincronizador = Sincronizador(
            from_env(),
            abrir_planilha(args.credenciais, args.planilha, args.aba),
            carregar_mapa(args.mapa),
            base=args.base,
            vencedor=Origem(args.vencedor),
            dry_run=not args.executar,
        )
        decisoes = sincronizador.rodar()
    except (BitrixError, RuntimeError, ValueError, OSError) as exc:
        print(f"falhou: {exc}", file=sys.stderr)
        return 2

    conflitos = [d for d in decisoes if d.acao is Acao.CONFLITO]
    modo = "EXECUTADO" if args.executar else "SIMULACAO (use --executar)"
    print(f"\n{modo}\n{len(decisoes)} mudancas | {len(conflitos)} conflitos")
    for decisao in decisoes[:20]:
        print(f"  {decisao.descrever()}")

    # Conflito nao resolvido precisa acordar alguem.
    return 1 if conflitos else 0


if __name__ == "__main__":
    raise SystemExit(main())
