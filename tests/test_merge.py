"""Testes do merge de três vias — perda silenciosa de dado mora aqui."""

from __future__ import annotations

from sheets_sync.merge import (
    Acao,
    Origem,
    comparar_registro,
    normalizar,
    nova_base,
    planejar,
)

CAMPOS = ["telefone", "valor", "status"]


def comparar(base, crm, planilha, vencedor=Origem.CRM):
    return comparar_registro("D1", base, crm, planilha, CAMPOS, vencedor)


class TestNormalizacao:
    def test_none_espaco_e_vazio_sao_a_mesma_coisa(self):
        """Sem isso, celula em branco vs campo nulo gera escrita infinita."""
        assert normalizar(None) == normalizar("") == normalizar("   ") == ""

    def test_numero_vira_string_aparada(self):
        assert normalizar(250) == "250"
        assert normalizar(" abc ") == "abc"


class TestSemMudanca:
    def test_tudo_igual_nao_gera_acao(self):
        linha = {"telefone": "1111", "valor": "100", "status": "ok"}
        assert comparar(linha, linha, linha).acao is Acao.NADA

    def test_diferenca_so_de_espaco_nao_gera_acao(self):
        base = {"telefone": "1111"}
        assert comparar(base, {"telefone": " 1111 "}, {"telefone": "1111"}).acao is Acao.NADA


class TestMudancaDeUmLadoSo:
    def test_mudou_no_crm_vai_para_planilha(self):
        d = comparar({"telefone": "1111"}, {"telefone": "2222"}, {"telefone": "1111"})
        assert d.acao is Acao.ATUALIZAR_PLANILHA
        assert d.campos_para_planilha == {"telefone": "2222"}

    def test_mudou_na_planilha_vai_para_crm(self):
        d = comparar({"valor": "100"}, {"valor": "100"}, {"valor": "250"})
        assert d.acao is Acao.ATUALIZAR_CRM
        assert d.campos_para_crm == {"valor": "250"}


class TestOsDoisLadosMudaram:
    def test_campos_diferentes_nao_e_conflito(self):
        """O caso que 'quem salvou por ultimo ganha' destroi."""
        d = comparar(
            {"telefone": "1111", "valor": "100"},
            {"telefone": "2222", "valor": "100"},
            {"telefone": "1111", "valor": "250"},
        )
        assert d.acao is not Acao.CONFLITO
        assert d.campos_para_planilha == {"telefone": "2222"}
        assert d.campos_para_crm == {"valor": "250"}

    def test_mesmo_campo_com_vencedor_crm(self):
        d = comparar({"valor": "100"}, {"valor": "200"}, {"valor": "300"}, Origem.CRM)
        assert d.campos_para_planilha == {"valor": "200"}
        assert d.campos_para_crm == {}

    def test_mesmo_campo_com_vencedor_planilha(self):
        d = comparar({"valor": "100"}, {"valor": "200"}, {"valor": "300"}, Origem.PLANILHA)
        assert d.campos_para_crm == {"valor": "300"}

    def test_modo_manual_devolve_conflito_em_vez_de_escolher(self):
        d = comparar({"valor": "100"}, {"valor": "200"}, {"valor": "300"}, Origem.MANUAL)
        assert d.acao is Acao.CONFLITO
        assert d.conflitos == {"valor": ("200", "300")}

    def test_descricao_do_conflito_mostra_os_dois_valores(self):
        d = comparar({"valor": "100"}, {"valor": "200"}, {"valor": "300"}, Origem.MANUAL)
        texto = d.descrever()
        assert "200" in texto and "300" in texto


class TestRegistroNovo:
    def test_so_na_planilha_cria_no_crm(self):
        d = comparar(None, None, {"telefone": "1111", "valor": "100"})
        assert d.acao is Acao.CRIAR_NO_CRM
        assert d.campos_para_crm["telefone"] == "1111"

    def test_so_no_crm_cria_na_planilha(self):
        d = comparar(None, {"telefone": "1111"}, None)
        assert d.acao is Acao.CRIAR_NA_PLANILHA

    def test_apareceu_dos_dois_lados_com_valor_igual_nao_e_conflito(self):
        d = comparar(None, {"valor": "100"}, {"valor": "100"})
        assert d.acao is Acao.NADA

    def test_apareceu_dos_dois_lados_divergente_usa_o_vencedor(self):
        d = comparar(None, {"valor": "100"}, {"valor": "999"}, Origem.CRM)
        assert d.campos_para_planilha == {"valor": "100"}


class TestRegistroSumiu:
    def test_sumiu_do_crm_nao_apaga_da_planilha(self):
        """Apagar em cascata e destrutivo demais para um robo decidir."""
        d = comparar({"valor": "100"}, None, {"valor": "100"})
        assert d.acao is Acao.NADA

    def test_sumiu_dos_dois_nao_faz_nada(self):
        assert comparar({"valor": "100"}, None, None).acao is Acao.NADA


class TestPlanejar:
    def test_agrega_e_descarta_o_que_nao_muda(self):
        base = {"A": {"valor": "1"}, "B": {"valor": "2"}}
        crm = {"A": {"valor": "1"}, "B": {"valor": "9"}}
        planilha = {"A": {"valor": "1"}, "B": {"valor": "2"}, "C": {"valor": "3"}}

        decisoes = planejar(base, crm, planilha, ["valor"])

        chaves = {d.chave: d.acao for d in decisoes}
        assert "A" not in chaves, "sem mudanca nao entra no plano"
        assert chaves["B"] is Acao.ATUALIZAR_PLANILHA
        assert chaves["C"] is Acao.CRIAR_NO_CRM

    def test_plano_vazio_quando_tudo_esta_sincronizado(self):
        igual = {"A": {"valor": "1"}}
        assert planejar(igual, igual, igual, ["valor"]) == []


class TestNovaBase:
    def test_incorpora_o_que_foi_aplicado(self):
        base = {"A": {"valor": "1"}}
        decisoes = planejar(base, {"A": {"valor": "9"}}, {"A": {"valor": "1"}}, ["valor"])
        assert nova_base(base, decisoes, ["valor"])["A"]["valor"] == "9"

    def test_conflito_nao_entra_na_base(self):
        """Se entrasse, o conflito sumiria sem ninguem ter resolvido nada."""
        base = {"A": {"valor": "1"}}
        decisoes = planejar(
            base, {"A": {"valor": "2"}}, {"A": {"valor": "3"}}, ["valor"], Origem.MANUAL
        )
        assert nova_base(base, decisoes, ["valor"])["A"]["valor"] == "1"

    def test_registro_novo_entra_na_base(self):
        decisoes = planejar({}, {}, {"C": {"valor": "3"}}, ["valor"])
        assert nova_base({}, decisoes, ["valor"])["C"]["valor"] == "3"


class TestIdempotencia:
    def test_rodar_duas_vezes_nao_gera_acao_na_segunda(self):
        """A propriedade que separa sync bom de escrita infinita."""
        base = {"A": {"valor": "1", "telefone": "1111"}}
        crm = {"A": {"valor": "9", "telefone": "1111"}}
        planilha = {"A": {"valor": "1", "telefone": "2222"}}
        campos = ["valor", "telefone"]

        decisoes = planejar(base, crm, planilha, campos)
        assert decisoes, "primeira passada precisa fazer alguma coisa"

        # Aplica o plano nos dois lados e recalcula.
        depois = nova_base(base, decisoes, campos)
        assert planejar(depois, depois, depois, campos) == []
