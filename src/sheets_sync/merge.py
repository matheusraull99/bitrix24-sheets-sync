"""Merge de três vias entre o CRM e a planilha.

Sincronização bidirecional feita com "quem salvou por último ganha" perde
dados em silêncio: o vendedor atualiza o telefone no CRM às 10h, a analista
corrige o valor na planilha às 10h05, e a sincronização das 11h escolhe um
lado inteiro — apagando a alteração do outro.

A saída é comparar **campo a campo contra uma linha de base**: o estado da
última sincronização bem-sucedida. Com três versões (base, CRM, planilha) dá
para distinguir "mudou de um lado" de "mudou dos dois", e só o segundo caso é
conflito de verdade.

    base:      telefone=1111  valor=100
    CRM:       telefone=2222  valor=100     -> telefone mudou no CRM
    planilha:  telefone=1111  valor=250     -> valor mudou na planilha
    resultado: telefone=2222  valor=250     -> nenhum conflito, os dois entram
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

Linha = dict[str, str]


class Origem(str, Enum):
    """Quem vence quando os dois lados mudaram o mesmo campo."""

    CRM = "crm"
    PLANILHA = "planilha"
    MANUAL = "manual"


class Acao(str, Enum):
    NADA = "nada"
    ATUALIZAR_CRM = "atualizar_crm"
    ATUALIZAR_PLANILHA = "atualizar_planilha"
    CRIAR_NO_CRM = "criar_no_crm"
    CRIAR_NA_PLANILHA = "criar_na_planilha"
    CONFLITO = "conflito"


@dataclass
class Decisao:
    """O que fazer com um registro, e por quê."""

    chave: str
    acao: Acao
    campos_para_crm: Linha = field(default_factory=dict)
    campos_para_planilha: Linha = field(default_factory=dict)
    conflitos: dict[str, tuple[str, str]] = field(default_factory=dict)

    def descrever(self) -> str:
        if self.acao is Acao.CONFLITO:
            detalhes = ", ".join(
                f"{campo}: CRM={crm!r} vs planilha={pl!r}"
                for campo, (crm, pl) in self.conflitos.items()
            )
            return f"{self.chave}: CONFLITO — {detalhes}"
        alvo = self.campos_para_crm or self.campos_para_planilha
        return f"{self.chave}: {self.acao.value} ({', '.join(alvo) or 'sem campos'})"


def normalizar(valor: Any) -> str:
    """Achata para comparação: ``None``, ``""`` e ``"  "`` são o mesmo vazio.

    Sem isso, uma célula em branco na planilha e um campo nulo no CRM
    apareceriam como diferença em toda execução, gerando escrita infinita.
    """
    if valor is None:
        return ""
    return str(valor).strip()


def comparar_registro(
    chave: str,
    base: Linha | None,
    crm: Linha | None,
    planilha: Linha | None,
    campos: list[str],
    vencedor: Origem = Origem.CRM,
) -> Decisao:
    """Decide o destino de um registro presente em algum dos três lados.

    Args:
        chave: identificador estável do registro nos dois lados.
        base: estado na última sincronização; ``None`` se é registro novo.
        crm: estado atual no CRM; ``None`` se não existe lá.
        planilha: estado atual na planilha; ``None`` se não existe lá.
        campos: campos sincronizados. O que não estiver aqui é ignorado.
        vencedor: quem ganha quando os dois lados mudaram o mesmo campo.
            ``MANUAL`` nunca resolve sozinho — devolve conflito para revisão.
    """
    if crm is None and planilha is None:
        return Decisao(chave, Acao.NADA)

    if base is None:
        # Registro novo: existe de um lado só e nunca foi sincronizado.
        if crm is None:
            return Decisao(chave, Acao.CRIAR_NO_CRM, campos_para_crm=_apenas(planilha, campos))
        if planilha is None:
            return Decisao(
                chave, Acao.CRIAR_NA_PLANILHA, campos_para_planilha=_apenas(crm, campos)
            )
        # Apareceu dos dois lados ao mesmo tempo: trata como divergencia
        # normal, com a base vazia — assim campo igual nao vira conflito.
        base = {}

    if crm is None or planilha is None:
        # Sumiu de um lado depois de ja ter sido sincronizado. Apagar do
        # outro seria destrutivo demais para um robo decidir sozinho.
        return Decisao(chave, Acao.NADA)

    para_crm: Linha = {}
    para_planilha: Linha = {}
    conflitos: dict[str, tuple[str, str]] = {}

    for campo in campos:
        antes = normalizar(base.get(campo))
        agora_crm = normalizar(crm.get(campo))
        agora_planilha = normalizar(planilha.get(campo))

        mudou_crm = agora_crm != antes
        mudou_planilha = agora_planilha != antes

        if agora_crm == agora_planilha:
            continue  # já iguais, nada a fazer
        if mudou_crm and not mudou_planilha:
            para_planilha[campo] = agora_crm
        elif mudou_planilha and not mudou_crm:
            para_crm[campo] = agora_planilha
        elif mudou_crm and mudou_planilha:
            if vencedor is Origem.CRM:
                para_planilha[campo] = agora_crm
            elif vencedor is Origem.PLANILHA:
                para_crm[campo] = agora_planilha
            else:
                conflitos[campo] = (agora_crm, agora_planilha)

    if conflitos:
        return Decisao(chave, Acao.CONFLITO, para_crm, para_planilha, conflitos)
    if para_crm and para_planilha:
        # Os dois lados mudaram campos diferentes: as duas escritas acontecem.
        return Decisao(chave, Acao.ATUALIZAR_CRM, para_crm, para_planilha)
    if para_crm:
        return Decisao(chave, Acao.ATUALIZAR_CRM, campos_para_crm=para_crm)
    if para_planilha:
        return Decisao(chave, Acao.ATUALIZAR_PLANILHA, campos_para_planilha=para_planilha)
    return Decisao(chave, Acao.NADA)


def planejar(
    base: dict[str, Linha],
    crm: dict[str, Linha],
    planilha: dict[str, Linha],
    campos: list[str],
    vencedor: Origem = Origem.CRM,
) -> list[Decisao]:
    """Compara as três fotografias e devolve o plano de sincronização.

    Returns:
        Uma decisão por chave que existe em qualquer um dos lados, já sem as
        que não exigem ação.
    """
    chaves = sorted(set(base) | set(crm) | set(planilha))
    decisoes = [
        comparar_registro(c, base.get(c), crm.get(c), planilha.get(c), campos, vencedor)
        for c in chaves
    ]
    return [d for d in decisoes if d.acao is not Acao.NADA]


def nova_base(
    base_anterior: dict[str, Linha], decisoes: list[Decisao], campos: list[str]
) -> dict[str, Linha]:
    """Calcula a linha de base a gravar depois de aplicar as decisões.

    Conflitos **não** entram: o registro precisa continuar divergente na
    próxima execução, senão o conflito some sem ninguém ter resolvido nada.
    """
    nova = {k: dict(v) for k, v in base_anterior.items()}
    for decisao in decisoes:
        if decisao.acao is Acao.CONFLITO:
            continue
        atual = nova.setdefault(decisao.chave, {c: "" for c in campos})
        atual.update(decisao.campos_para_crm)
        atual.update(decisao.campos_para_planilha)
    return nova


def _apenas(linha: Linha | None, campos: list[str]) -> Linha:
    """Recorta a linha para os campos sincronizados."""
    return {c: normalizar((linha or {}).get(c)) for c in campos}
