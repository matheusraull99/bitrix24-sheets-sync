"""Sincronizacao bidirecional entre o CRM do Bitrix24 e o Google Sheets."""

from .merge import Acao, Decisao, Origem, comparar_registro, nova_base, normalizar, planejar
from .sync import Mapeamento, Planilha, Sincronizador, abrir_planilha

__version__ = "1.0.0"

__all__ = [
    "Acao",
    "Decisao",
    "Mapeamento",
    "Origem",
    "Planilha",
    "Sincronizador",
    "abrir_planilha",
    "comparar_registro",
    "normalizar",
    "nova_base",
    "planejar",
]
