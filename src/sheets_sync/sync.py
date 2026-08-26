"""Cola entre CRM, Google Sheets e o merge de três vias.

A linha de base fica num arquivo JSON local. Guardar no CRM ou numa aba
oculta seria "mais elegante" e traria o problema clássico: a leitura da base
passa a mexer no dado que ela mesma sincroniza.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bitrix24_client import Bitrix24

from .merge import Acao, Decisao, Linha, Origem, nova_base, planejar

log = logging.getLogger("sheets_sync")

#: Escopo mínimo. `spreadsheets` sem `.readonly` porque o robô escreve.
ESCOPOS = ["https://www.googleapis.com/auth/spreadsheets"]


@dataclass
class Mapeamento:
    """Como as colunas da planilha correspondem aos campos do CRM.

    Args:
        entidade: método base do CRM, ex.: ``crm.deal``.
        chave_crm: campo que identifica o registro, normalmente ``ID``.
        coluna_chave: cabeçalho da coluna equivalente na planilha.
        campos: mapa ``campo do CRM -> cabeçalho na planilha``.
    """

    entidade: str
    chave_crm: str
    coluna_chave: str
    campos: dict[str, str]

    @property
    def campos_crm(self) -> list[str]:
        return list(self.campos)

    @property
    def colunas(self) -> list[str]:
        return [self.coluna_chave, *self.campos.values()]

    def crm_para_neutro(self, registro: dict[str, Any]) -> Linha:
        """Converte o payload do CRM para o formato do merge."""
        return {campo: str(registro.get(campo) or "") for campo in self.campos}

    def planilha_para_neutro(self, linha: dict[str, str]) -> Linha:
        """Converte uma linha da planilha para o formato do merge."""
        return {campo: str(linha.get(coluna, "")) for campo, coluna in self.campos.items()}


class Planilha:
    """Acesso à aba do Google Sheets, com cabeçalho como contrato."""

    def __init__(self, service, spreadsheet_id: str, aba: str) -> None:
        self.service = service
        self.spreadsheet_id = spreadsheet_id
        self.aba = aba
        self._cabecalho: list[str] = []

    def ler(self) -> tuple[list[str], list[dict[str, str]]]:
        """Lê a aba inteira; devolve cabeçalho e linhas como dicionário."""
        resposta = (
            self.service.spreadsheets()
            .values()
            .get(spreadsheetId=self.spreadsheet_id, range=self.aba)
            .execute()
        )
        valores = resposta.get("values", [])
        if not valores:
            return [], []

        self._cabecalho = [str(c).strip() for c in valores[0]]
        linhas = []
        for bruta in valores[1:]:
            # A API omite células vazias no fim da linha; sem completar,
            # zip() truncaria e a última coluna sumiria do registro.
            completa = list(bruta) + [""] * (len(self._cabecalho) - len(bruta))
            linhas.append(dict(zip(self._cabecalho, completa)))
        return self._cabecalho, linhas

    def escrever(self, cabecalho: list[str], linhas: list[dict[str, str]]) -> None:
        """Regrava a aba inteira.

        Reescrever tudo em vez de mexer célula a célula troca N chamadas por
        uma e evita o descompasso de índice quando alguém insere uma linha no
        meio enquanto o robô roda.
        """
        matriz = [cabecalho] + [[linha.get(c, "") for c in cabecalho] for linha in linhas]
        self.service.spreadsheets().values().update(
            spreadsheetId=self.spreadsheet_id,
            range=self.aba,
            valueInputOption="RAW",
            body={"values": matriz},
        ).execute()


def abrir_planilha(credenciais: Path, spreadsheet_id: str, aba: str) -> Planilha:
    """Autentica com conta de serviço e devolve o acesso à aba.

    Conta de serviço, não OAuth de usuário: o robô roda sem ninguém para
    clicar em "permitir" quando o refresh token expira. A planilha precisa
    ser compartilhada com o e-mail da conta de serviço.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        str(credenciais), scopes=ESCOPOS
    )
    return Planilha(build("sheets", "v4", credentials=creds), spreadsheet_id, aba)


class Sincronizador:
    """Executa o plano nos dois lados e atualiza a linha de base."""

    def __init__(
        self,
        bx: Bitrix24,
        planilha: Planilha,
        mapa: Mapeamento,
        *,
        base: Path,
        vencedor: Origem = Origem.CRM,
        dry_run: bool = True,
    ) -> None:
        self.bx = bx
        self.planilha = planilha
        self.mapa = mapa
        self.caminho_base = base
        self.vencedor = vencedor
        self.dry_run = dry_run

    def rodar(self) -> list[Decisao]:
        """Sincroniza e devolve o que foi decidido."""
        base = self._carregar_base()
        registros_crm = {
            str(r[self.mapa.chave_crm]): self.mapa.crm_para_neutro(r)
            for r in self.bx.fetch_all(
                f"{self.mapa.entidade}.list",
                {"select": [self.mapa.chave_crm, *self.mapa.campos_crm]},
            )
        }

        cabecalho, linhas = self.planilha.ler()
        if cabecalho and self.mapa.coluna_chave not in cabecalho:
            raise ValueError(
                f"a aba nao tem a coluna-chave {self.mapa.coluna_chave!r}; "
                f"colunas encontradas: {cabecalho}"
            )
        registros_planilha = {
            str(linha[self.mapa.coluna_chave]): self.mapa.planilha_para_neutro(linha)
            for linha in linhas
            if str(linha.get(self.mapa.coluna_chave, "")).strip()
        }

        decisoes = planejar(
            base, registros_crm, registros_planilha, self.mapa.campos_crm, self.vencedor
        )
        log.info(
            "%d registros no CRM, %d na planilha, %d decisoes",
            len(registros_crm), len(registros_planilha), len(decisoes),
        )

        if self.dry_run:
            for decisao in decisoes:
                log.info("[simulacao] %s", decisao.descrever())
            return decisoes

        self._aplicar_no_crm(decisoes)
        self._aplicar_na_planilha(decisoes, registros_crm, registros_planilha)
        self._salvar_base(nova_base(base, decisoes, self.mapa.campos_crm))
        return decisoes

    def _aplicar_no_crm(self, decisoes: list[Decisao]) -> None:
        atualizacoes = [d for d in decisoes if d.campos_para_crm and d.acao is not Acao.CONFLITO]
        for decisao in atualizacoes:
            campos = {c: v for c, v in decisao.campos_para_crm.items()}
            if decisao.acao is Acao.CRIAR_NO_CRM:
                self.bx.call(f"{self.mapa.entidade}.add", {"fields": campos})
            else:
                self.bx.call(
                    f"{self.mapa.entidade}.update", {"id": decisao.chave, "fields": campos}
                )

    def _aplicar_na_planilha(
        self,
        decisoes: list[Decisao],
        registros_crm: dict[str, Linha],
        registros_planilha: dict[str, Linha],
    ) -> None:
        """Reconstrói a aba com as alterações aplicadas."""
        atual = {k: dict(v) for k, v in registros_planilha.items()}
        for decisao in decisoes:
            if decisao.acao is Acao.CONFLITO:
                continue
            if decisao.acao is Acao.CRIAR_NA_PLANILHA:
                atual[decisao.chave] = dict(registros_crm.get(decisao.chave, {}))
            elif decisao.campos_para_planilha:
                atual.setdefault(decisao.chave, {}).update(decisao.campos_para_planilha)

        linhas = [
            {
                self.mapa.coluna_chave: chave,
                **{self.mapa.campos[c]: v for c, v in campos.items() if c in self.mapa.campos},
            }
            for chave, campos in sorted(atual.items())
        ]
        self.planilha.escrever(self.mapa.colunas, linhas)

    def _carregar_base(self) -> dict[str, Linha]:
        if not self.caminho_base.exists():
            log.info("sem linha de base; primeira sincronizacao")
            return {}
        try:
            return json.loads(self.caminho_base.read_text("utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # Base ilegivel e pior que base ausente: sem ela, tudo vira
            # "registro novo" e o robo pode duplicar a planilha inteira.
            raise RuntimeError(
                f"linha de base corrompida em {self.caminho_base}: {exc}. "
                "Apague o arquivo de proposito se quiser recomecar do zero."
            ) from exc

    def _salvar_base(self, base: dict[str, Linha]) -> None:
        self.caminho_base.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.caminho_base.with_suffix(".tmp")
        tmp.write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.caminho_base)
