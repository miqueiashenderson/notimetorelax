import re, subprocess, unicodedata

DIAS = ["Seg", "Ter", "Qua", "Qui", "Sex"]
SLOTS = [
    "07:00-08:00", "08:00-09:00", "09:00-10:00", "10:10-11:10", "11:10-12:10",
    "13:00-14:00", "14:00-15:00", "15:00-16:00", "16:10-17:10", "17:10-18:10",
    "18:30-19:20", "19:20-20:10", "20:20-21:10", "21:10-22:00"
]


def extrair_texto(path: str) -> str:
    try:
        r = subprocess.run(
            ['pdftotext', '-layout', path, '-'],
            capture_output=True, text=True, timeout=10
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def extrair_dados_aluno(texto: str):
    nome = curso = ""
    for linha in texto.split('\n'):
        if not nome:
            m = re.search(r'(?:Discente|Nome):\s*(.+)', linha)
            if m:
                nome = m.group(1).strip()
        if not curso:
            m = re.search(r'Curso:\s*(.+)', linha)
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


def parse_tabela(texto: str):
    linhas = texto.split('\n')

    inicio = -1
    for i, linha in enumerate(linhas):
        dias_presentes = sum(1 for d in DIAS if d in linha)
        if dias_presentes >= 3 and 'Horário' in linha:
            inicio = i + 1
            break

    if inicio == -1:
        for i, linha in enumerate(linhas):
            if re.search(r'\d{2}:\d{2}\s*[-–]\s*\d{2}:\d{2}', linha):
                count = 0
                for j in range(i, min(i + 25, len(linhas))):
                    l = linhas[j]
                    if re.search(r'\d{2}:\d{2}\s*[-–]', l) or '---' in l or re.search(r'\b\d{7}\b', l):
                        count += 1
                    elif l.strip() == '':
                        continue
                    else:
                        if count >= 4:
                            break
                        count = 0
                if count >= 4:
                    inicio = i
                    break

    if inicio == -1:
        return []

    header = linhas[inicio - 1] if inicio > 0 else ""

    day_pos = {}
    for d in DIAS:
        idx = header.find(d)
        if idx >= 0:
            day_pos[d] = idx

    if not day_pos:
        if 'Dom' in header:
            day_pos = {"Seg": 26, "Ter": 38, "Qua": 50, "Qui": 62, "Sex": 74}
        else:
            day_pos = {"Seg": 20, "Ter": 32, "Qua": 44, "Qui": 56, "Sex": 68}

    raw_rows = []
    for i in range(inicio, min(inicio + 40, len(linhas))):
        l = linhas[i]
        has_time = bool(re.search(r'\d{2}:\d{2}', l))
        has_code_or_dash = bool(re.search(r'\b\d{7}\b', l)) or '---' in l
        is_empty = l.strip() == ''
        if has_time or has_code_or_dash:
            raw_rows.append(l)
        elif is_empty and raw_rows:
            raw_rows.append('')
        elif raw_rows and not is_empty:
            break

    compressed = []
    for l in raw_rows:
        if l.strip() == '' and compressed and compressed[-1].strip() == '':
            continue
        compressed.append(l)
    raw_rows = compressed

    grupos = []
    atual = []
    for l in raw_rows:
        if l.strip() == '':
            if atual:
                grupos.append(atual)
                atual = []
            continue
        starts_new = bool(re.search(r'\d{2}:\d{2}\s*[-–]', l))
        if starts_new:
            if atual:
                grupos.append(atual)
            atual = [l]
        else:
            atual.append(l)
    if atual:
        grupos.append(atual)

    resultados = []
    for grupo in grupos:
        if not grupo:
            continue
        combined = ' '.join(grupo)
        times = re.findall(r'(\d{2}:\d{2})', combined)
        if len(times) < 2:
            continue
        time_range = f"{times[0]}-{times[1]}"

        sorted_days = sorted(day_pos.items(), key=lambda x: x[1])

        if len(grupo) == 1:
            rest = re.sub(r'^\s*\d{2}:\d{2}\s*[-–]\s*\d{2}:\d{2}\s*', '', grupo[0])
            parts = re.split(r'\s{3,}', rest.strip())
            has_dom = 'Dom' in (linhas[inicio - 1] if inicio > 0 else '')
            offset = 1 if has_dom else 0
            for idx, part in enumerate(parts):
                part = part.strip()
                if part and part != '---':
                    m = re.search(r'(\d{7})', part)
                    if m:
                        day_idx = idx - offset
                        if 0 <= day_idx < len(DIAS):
                            resultados.append((DIAS[day_idx], time_range, m.group(1)))
            continue

        encontrou = False
        for i, (dia, pos) in enumerate(sorted_days):
            end_pos = sorted_days[i + 1][1] if i + 1 < len(sorted_days) else len(combined)
            col_text = combined[pos:end_pos].strip()
            m = re.search(r'(\d{7})', col_text)
            if m:
                resultados.append((dia, time_range, m.group(1)))
                encontrou = True
        if encontrou:
            continue

        codes = list(re.finditer(r'(\d{7})', combined))
        for m in codes:
            code_pos = m.start()
            code = m.group(1)
            for i, (dia, pos) in enumerate(sorted_days):
                end_pos = sorted_days[i + 1][1] if i + 1 < len(sorted_days) else len(combined)
                if pos <= code_pos < end_pos:
                    resultados.append((dia, time_range, code))
                    break

    vistos = set()
    unicos = []
    for r in resultados:
        if r not in vistos:
            vistos.add(r)
            unicos.append(r)
    return unicos


def slot_index(tr: str) -> int:
    for i, s in enumerate(SLOTS):
        if s == tr:
            return i
    return -1


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
    horarios_raw = parse_tabela(texto)
    busy = []
    for dia, hr, _ in horarios_raw:
        di = DIAS.index(dia)
        si = slot_index(hr)
        if si >= 0:
            busy.append([di, si])
    unique_busy = []
    seen = set()
    for item in busy:
        key = tuple(item)
        if key not in seen:
            seen.add(key)
            unique_busy.append(item)
    return {
        "nome": title_case(nome.lower()),
        "curso": curso_curto(curso) if curso else "",
        "horarios_raw": horarios_raw,
        "busy": unique_busy,
        "total": len(unique_busy)
    }


def extrair_de_pdf(path: str):
    texto = extrair_texto(path)
    if not texto.strip():
        return None
    return extrair_completo(texto)
