"""Regressão do template da escala (templates/escala.html).

O painel de co-donos ficava sempre vazio porque `renderAll()` não chamava
`renderAdmins()`; com isso o dropdown de candidatos (`#adminPick`) nunca era
populado e o usuário não via quem poderia promover. Não há testes de JS neste
projeto, então verificamos a fiação diretamente no template.
"""
import os
import re

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "..", "templates", "escala.html")


def _template() -> str:
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def test_render_all_chama_render_admins():
    html = _template()
    m = re.search(r"function renderAll\(\)\s*\{([^}]*)\}", html)
    assert m, "renderAll() não encontrada em escala.html"
    assert "renderAdmins()" in m.group(1), (
        "renderAll() deve chamar renderAdmins(); caso contrário o painel de "
        "co-donos nunca é renderizado na carga da página."
    )


def test_painel_de_admins_e_dropdown_existem():
    html = _template()
    for elem in ('id="adminsBox"', 'id="adminsAdd"', 'id="adminPick"', 'id="btnAddAdmin"'):
        assert elem in html, f"elemento ausente no template: {elem}"
