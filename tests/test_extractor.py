import pytest
from extractor import (
    extrair_dados_aluno, curso_curto, extrair_horarios,
    extrair_completo, slot_index, title_case,
    extrair_de_pdf_bytes, extrair_texto,
)

SIGAA_TEXT = """\
Universidade Federal do Ceará
Sistema Integrado de Gestão de Atividades Acadêmicas

Discente: João Silva de Oliveira
Curso: CIÊNCIA DA COMPUTAÇÃO - Bacharelado

Matriz Curricular: 2009.1

    Horário                     Seg            Ter            Qua            Qui            Sex
    07:00-08:00               ---            ---            1234567        ---            ---
    08:00-09:00               9876543        ---            ---            3456789        ---
    09:00-10:00               ---            1234567        ---            ---            9876543
    10:10-11:10               3456789        ---            9876543        ---            ---

Observações: Período 2024.2
"""

SIGAA_TEXT_FALLBACK = """\
Universidade Federal do Ceará
Discente: Maria Santos
Curso: ENGENHARIA MECÂNICA

07:00-08:00               ---            1111111        ---            ---            ---
08:00-09:00               ---            ---            2222222        ---            ---
09:00-10:00               3333333        ---            ---            4444444        ---
10:10-11:10               ---            3333333        ---            ---            2222222
"""

TEXT_SEM_HORARIO = """\
Discente: João
Curso: TESTE
Isso não tem tabela de horários.
"""


class TestExtrairDadosAluno:
    def test_extrair_nome_e_curso(self):
        nome, curso = extrair_dados_aluno(SIGAA_TEXT)
        assert nome == "João Silva de Oliveira"
        assert curso == "CIÊNCIA DA COMPUTAÇÃO - Bacharelado"

    def test_extrair_nome_com_discente(self):
        txt = "Discente: Maria\nCurso: Física"
        assert extrair_dados_aluno(txt) == ("Maria", "Física")

    def test_extrair_nome_com_nome(self):
        txt = "Nome: José\nCurso: Matemática"
        assert extrair_dados_aluno(txt) == ("José", "Matemática")

    def test_retorna_vazio_quando_sem_dados(self):
        nome, curso = extrair_dados_aluno("texto qualquer")
        assert nome == ""
        assert curso == ""


class TestCursoCurto:
    def test_mecanica(self):
        assert curso_curto("Engenharia Mecânica") == "Eng. Mecânica"

    def test_ciencia_da_computacao(self):
        assert curso_curto("CIÊNCIA DA COMPUTAÇÃO") == "Ciência da Computação"

    def test_eletrica(self):
        assert curso_curto("Engenharia Elétrica") == "Eng. Elétrica"

    def test_quimica(self):
        assert curso_curto("Engenharia Química") == "Eng. Química"

    def test_outro_curso(self):
        assert curso_curto("Física") == "Física"


class TestExtrairHorarios:
    def test_extrai_horarios_com_header(self):
        result = extrair_horarios(SIGAA_TEXT)
        assert len(result) > 0
        dias = {r[0] for r in result}
        assert "Seg" in dias
        assert "Ter" in dias
        assert "Qua" in dias
        assert "Qui" in dias

    def test_extrai_horarios_sem_header_explicito(self):
        result = extrair_horarios(SIGAA_TEXT_FALLBACK)
        assert len(result) > 0

    def test_retorna_vazio_sem_horarios(self):
        assert extrair_horarios(TEXT_SEM_HORARIO) == []


class TestExtrairCompleto:
    def test_extrai_completo(self):
        result = extrair_completo(SIGAA_TEXT)
        assert result is not None
        assert result["nome"] == "João Silva de Oliveira"
        assert result["curso"] == "Ciência da Computação"
        assert len(result["horarios_raw"]) > 0
        assert len(result["busy"]) > 0

    def test_retorna_none_sem_nome(self):
        assert extrair_completo("texto sem dados") is None


class TestSlotIndex:
    def test_mapa_correto(self):
        assert slot_index("07:00-08:00") == 0
        assert slot_index("08:00-09:00") == 1
        assert slot_index("21:10-22:00") == 13

    def test_slot_invalido(self):
        assert slot_index("invalido") == -1


class TestTitleCase:
    def test_title_case_normal(self):
        assert title_case("joão silva") == "João Silva"

    def test_title_case_com_excecoes(self):
        assert title_case("maria da silva") == "Maria da Silva"
        assert title_case("josé dos santos") == "José dos Santos"
        assert title_case("luís e carlos") == "Luís e Carlos"


class TestExtrairDePdfBytes:
    def test_returns_none_for_invalid_bytes(self):
        assert extrair_de_pdf_bytes(b"not a pdf") is None

    def test_returns_none_for_empty(self):
        assert extrair_de_pdf_bytes(b"") is None
