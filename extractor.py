import re, unicodedata
from io import BytesIO
from pypdf import PdfReader

DIAS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]
SLOTS = [
    "07:00-08:00", "08:00-09:00", "09:00-10:00", "10:10-11:10", "11:10-12:10",
    "13:00-14:00", "14:00-15:00", "15:00-16:00", "16:10-17:10", "17:10-18:10",
    "18:30-19:20", "19:20-20:10", "20:20-21:10", "21:10-22:00"
]

SIGAA_DIA_MAP = {
    '1': 'Dom',
    '2': 'Seg',
    '3': 'Ter',
    '4': 'Qua',
    '5': 'Qui',
    '6': 'Sex',
    '7': 'Sáb',
}
SIGAA_TURNO_MAP = {
    'M1': '07:00-08:00',
    'M2': '08:00-09:00',
    'M3': '09:00-10:00',
    'M4': '10:10-11:10',
    'M5': '11:10-12:10',
    'T1': '13:00-14:00',
    'T2': '14:00-15:00',
    'T3': '15:00-16:00',
    'T4': '16:10-17:10',
    'T5': '17:10-18:10',
    'N1': '18:30-19:20',
    'N2': '19:20-20:10',
    'N3': '20:20-21:10',
    'N4': '21:10-22:00',
}


def extrair_texto(path: str) -> str:
    try:
        with open(path, "rb") as f:
            reader = PdfReader(f)
            return "\n".join(
                pag.extract_text(extraction_mode="layout") or ""
                for pag in reader.pages
            )
    except Exception:
        return ""


def extrair_de_pdf_bytes(content: bytes):
    try:
        reader = PdfReader(BytesIO(content))
        texto = "\n".join(
            pag.extract_text(extraction_mode="layout") or ""
            for pag in reader.pages
        )
        if not texto.strip():
            return None
        return extrair_completo(texto)
    except Exception:
        return None


def extrair_dados_aluno(texto: str):
    nome = curso = ""
    for linha in texto.split('\n'):
        if not nome:
            m = re.search(r'(?:Discente|N\s*o\s*m\s*e)\s*:\s*(.+)', linha)
            if m:
                nome = m.group(1).strip()
        if not curso:
            m = re.search(r'C\s*u\s*r\s*s\s*o\s*:\s*(.+)', linha)
            if m:
                curso = m.group(1).strip()
    return nome, curso


def curso_curto(curso: str) -> str:
    c = curso.upper()
    if 'MECÂNICA' in c:
        return "Eng. Mecânica"
    if 'COMPUTAÇÃO' in c:
        return "Ciência da Computação"
    if 'ELÉTRICA' in c:
        return "Eng. Elétrica"
    if 'QUÍMICA' in c:
        return "Eng. Química"
    return curso[:25]


def extrair_horarios(texto: str):
    # Unificar quebras de linha no meio de intervalos de horário (ex: '08:00 -\n09:00')
    t = re.sub(r'(\d{2}:\d{2})\s*[-–]\s*\n\s*(\d{2}:\d{2})', r'\1 - \2', texto)
    linhas = [l.strip() for l in t.split('\n') if l.strip()]

    day_patterns = [
        ('Dom', r'\bDom\b'),
        ('Seg', r'\bSeg\b'),
        ('Ter', r'\bTer\b'),
        ('Qua', r'\bQua\b'),
        ('Qui', r'\bQui\b'),
        ('Sex', r'\bSex\b'),
        ('Sáb', r'\bS[aá]b\b')
    ]

    header_idx = -1
    cols = []
    for i, l in enumerate(linhas):
        matches = []
        for dname, pat in day_patterns:
            m = re.search(pat, l, re.IGNORECASE)
            if m:
                matches.append((m.start(), dname))
        if len(matches) >= 3:
            matches.sort()
            cols = [d for _, d in matches]
            header_idx = i
            break

    resultados = []
    if cols and header_idx >= 0:
        for l in linhas[header_idx + 1:]:
            time_m = re.search(r'(\d{2}:\d{2})\s*[-–]\s*(\d{2}:\d{2})', l)
            if not time_m:
                continue
            time_range = f"{time_m.group(1)}-{time_m.group(2)}"
            after_time = l[time_m.end():]
            tokens = re.findall(r'(\d{5,8}|[-–]{1,})', after_time)
            if len(tokens) == len(cols):
                for day, token in zip(cols, tokens):
                    if token.isdigit():
                        resultados.append((day, time_range, token))
            elif len(tokens) > len(cols):
                for idx, day in enumerate(cols):
                    if idx < len(tokens) and tokens[idx].isdigit():
                        resultados.append((day, time_range, tokens[idx]))

    # Fallback 1: se não encontrou cabeçalho explícito
    if not resultados:
        for l in linhas:
            time_m = re.search(r'(\d{2}:\d{2})\s*[-–]\s*(\d{2}:\d{2})', l)
            if time_m:
                time_range = f"{time_m.group(1)}-{time_m.group(2)}"
                after_time = l[time_m.end():]
                tokens = re.findall(r'(\d{5,8}|[-–]{1,})', after_time)
                if len(tokens) == 5:
                    padrao = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex']
                    for d, tok in zip(padrao, tokens):
                        if tok.isdigit():
                            resultados.append((d, time_range, tok))
                elif len(tokens) == 6:
                    padrao = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']
                    for d, tok in zip(padrao, tokens):
                        if tok.isdigit():
                            resultados.append((d, time_range, tok))
                elif len(tokens) == 7:
                    padrao = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']
                    for d, tok in zip(padrao, tokens):
                        if tok.isdigit():
                            resultados.append((d, time_range, tok))

    # Fallback 2: decodificação de códigos de turma SIGAA UFCG (ex: 2T23 4T45)
    if not resultados:
        for m in re.finditer(r'\b([1-7]+)([MTN])([1-5]+)\b', texto):
            dias_str, turno, slots_str = m.groups()
            for d in dias_str:
                dia_nome = SIGAA_DIA_MAP.get(d)
                if not dia_nome:
                    continue
                for s in slots_str:
                    slot_key = f"{turno}{s}"
                    slot_time = SIGAA_TURNO_MAP.get(slot_key)
                    if slot_time:
                        resultados.append((dia_nome, slot_time, slot_key))

    return list(dict.fromkeys(resultados))


SLOT_MAP = {s: i for i, s in enumerate(SLOTS)}


def slot_index(tr: str) -> int:
    return SLOT_MAP.get(tr, -1)


def title_case(nome: str) -> str:
    palavras = nome.split()
    excecoes = {'de', 'da', 'do', 'das', 'dos', 'e'}
    resultado = []
    for i, p in enumerate(palavras):
        if i > 0 and p.lower() in excecoes:
            resultado.append(p.lower())
        else:
            resultado.append(p.capitalize())
    return ' '.join(resultado)


def extrair_completo(texto: str):
    nome, curso = extrair_dados_aluno(texto)
    if not nome:
        return None
    nome = unicodedata.normalize('NFC', nome)
    curso = unicodedata.normalize('NFC', curso)
    horarios_raw = extrair_horarios(texto)
    busy = []
    for dia, hr, _ in horarios_raw:
        di = DIAS.index(dia)
        si = slot_index(hr)
        if si >= 0:
            busy.append([di, si])
    return {
        "nome": title_case(nome.lower()),
        "curso": curso_curto(curso) if curso else "",
        "horarios_raw": horarios_raw,
        "busy": busy,
        "total": len(busy)
    }


def extrair_de_pdf(path: str):
    texto = extrair_texto(path)
    if not texto.strip():
        return None
    return extrair_completo(texto)
