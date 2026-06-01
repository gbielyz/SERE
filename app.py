import csv
import io
import json
import os
import re
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from functools import wraps

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import db
from reporting import PDF_MIME, XLSX_MIME, pdf_report, xlsx_workbook
from security import (
    MIN_PASSWORD_LENGTH,
    PANEL_ACTIONS,
    clear_login_failures,
    login_is_locked,
    record_login_failure,
)
from sere.services.scoring import class_letter, concept, overall_score, overall_with_concept

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.environ.get("SERE_SECRET_KEY") or "dev-key-trocar"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("SERE_COOKIE_SECURE", "0") == "1",
)
USERS = [
    ("admin.sere", os.environ.get("SERE_ADMIN_PASSWORD", "trocar-admin-dev"), "admin", None),
    ("professor", os.environ.get("SERE_PROFESSOR_PASSWORD", "trocar-professor-dev"), "professor", None),
    ("aluno.demo", os.environ.get("SERE_ALUNO_PASSWORD", "trocar-aluno-dev"), "aluno", 3),
]

STUDENTS = [
    (1, "Lucas Andrade", "L", "2\u00baA", "2A", 88, 85, 76, 82, "Destaque acadêmico", ["Destaque acadêmico", "Top 3 da turma"], (82, 78, 72, 77)),
    (2, "Ana Beatriz", "A", "2\u00baB", "2B", 84, 82, 70, 88, "Referência de colaboração", ["Referência de colaboração", "Destaque social"], (78, 76, 68, 81)),
    (3, "Mariana Costa", "M", "2\u00baB", "2B", 80, 70, 75, 90, "Evolução consistente", ["Evolução consistente", "Top 3 da turma"], (74, 66, 70, 82)),
    (4, "Marcos Silva", "M", "2\u00baC", "2C", 78, 73, 81, 76, "Participação prática", ["Participação prática", "Destaque físico"], (72, 69, 75, 70)),
    (5, "Julia Santos", "J", "2\u00baA", "2A", 73, 78, 69, 80, "Maior evolução", ["Maior evolução", "Em evolução"], (64, 70, 63, 71)),
]

CLASS_SEEDS = [
    ("1A", "1\u00baA"), ("1B", "1\u00baB"), ("1C", "1\u00baC"),
    ("2A", "2\u00baA"), ("2B", "2\u00baB"), ("2C", "2\u00baC"), ("2D", "2\u00baD"), ("2E", "2\u00baE"),
    ("3A", "3\u00baA"), ("3B", "3\u00baB"), ("3C", "3\u00baC"), ("3D", "3\u00baD"),
]

FIRST_NAMES = [
    "Rafael", "Bianca", "Gabriel", "Larissa", "Thiago", "Marina", "Henrique", "Sofia", "Caio", "Isadora",
    "Miguel", "Laura", "Enzo", "Valentina", "Pedro", "Helena", "Davi", "Cecilia", "Arthur", "Livia",
    "Matheus", "Clara", "Gustavo", "Yasmin", "Bruno", "Manuela", "Felipe", "Ester", "Joao", "Luiza",
]
LAST_NAMES = [
    "Moura", "Almeida", "Ribeiro", "Costa", "Ferreira", "Carvalho", "Barbosa", "Araujo", "Rocha", "Mendes",
    "Teixeira", "Nogueira", "Campos", "Moreira", "Vieira", "Cardoso", "Pereira", "Lopes", "Castro", "Rezende",
]


def generated_students(total=320):
    titles = [
        "Top 3 da turma",
        "Maior evolução",
        "Em evolução",
        "Destaque físico",
        "Destaque social",
        "Autonomia em destaque",
        "Destaque acadêmico",
    ]
    out = []
    for index in range(total):
        sid = 1000 + index
        first = FIRST_NAMES[index % len(FIRST_NAMES)]
        last = LAST_NAMES[(index * 7) % len(LAST_NAMES)]
        name = f"{first} {last}"
        tid, turma = CLASS_SEEDS[index % len(CLASS_SEEDS)]
        ac = 38 + ((index * 17 + 23) % 61)
        ad = 34 + ((index * 13 + 31) % 62)
        fi = 32 + ((index * 19 + 11) % 65)
        so = 36 + ((index * 11 + 41) % 60)
        title = titles[index % len(titles)]
        unlocked = [title, titles[(index + 2) % len(titles)]]
        hist = (max(0, ac - (index % 9)), max(0, ad - ((index + 3) % 10)), max(0, fi - ((index + 5) % 8)), max(0, so - ((index + 1) % 7)))
        out.append((sid, name, first[:1], turma, tid, ac, ad, fi, so, title, unlocked, hist))
    return out

TITLES = [
    ("Destaque acadêmico", "Alto desempenho", "Acadêmico", "Desbloqueado", "Ficar em 1º lugar no atributo acadêmico."),
    ("Top 3 da turma", "Destaque", "Ranking", "Desbloqueado", "Ficar entre os 3 melhores alunos da turma."),
    ("Maior evolução", "Evolução", "Progresso", "Desbloqueado", "Apresentar grande melhora no desempenho geral."),
    ("Referência de colaboração", "Reconhecimento", "Social", "Desbloqueado", "Manter participação colaborativa consistente."),
    ("Destaque social", "Reconhecimento", "Social", "Desbloqueado", "Manter alto desempenho no atributo social."),
    ("Destaque físico", "Reconhecimento", "Físico", "Desbloqueado", "Manter alto desempenho no atributo físico."),
    ("Autonomia em destaque", "Meta", "Adaptabilidade", "Bloqueado", "Alcançar classe A em adaptabilidade."),
    ("Referência geral", "Meta", "Geral", "Bloqueado", "Alcançar o 1º lugar geral."),
    ("Em evolução", "Evolução", "Progresso", "Desbloqueado", "Melhorar o desempenho durante o ano letivo."),
]

RECS = {
    "academico": ("Acad\u00eamico", "Plano de refor\u00e7o acad\u00eamico", "Revisar um conte\u00fado curto e resolver quest\u00f5es objetivas.", ["Revisar anota\u00e7\u00f5es da aula por 20 minutos.", "Resolver 3 exerc\u00edcios do conte\u00fado estudado.", "Registrar uma d\u00favida para levar ao professor."]),
    "adaptabilidade": ("Adaptabilidade", "Plano de adapta\u00e7\u00e3o e autonomia", "Testar um novo m\u00e9todo de estudo e explicar o que funcionou.", ["Estudar usando resumo, mapa mental ou perguntas.", "Participar de uma atividade com um grupo diferente.", "Anotar uma dificuldade e uma estrat\u00e9gia usada para superar."]),
    "fisico": ("F\u00edsico", "Plano de participa\u00e7\u00e3o pr\u00e1tica", "Melhorar regularidade, participa\u00e7\u00e3o e consci\u00eancia corporal.", ["Participar de uma atividade pr\u00e1tica da aula.", "Registrar uma meta simples de regularidade.", "Identificar um cuidado importante antes ou depois do exerc\u00edcio."]),
    "social": ("Social", "Plano de colabora\u00e7\u00e3o", "Praticar participa\u00e7\u00e3o, escuta e ajuda em atividades coletivas.", ["Ajudar um colega em uma tarefa curta.", "Participar de uma discuss\u00e3o em grupo.", "Registrar como sua participa\u00e7\u00e3o ajudou o grupo."]),
}

QUIZZES = {
    "academico": [("Qual atitude mostra estudo ativo?", ["Ler sem anotar nada", "Resolver quest\u00f5es e revisar erros", "Esperar apenas a pr\u00f3xima aula"], "Resolver quest\u00f5es e revisar erros"), ("O que ajuda a encontrar dificuldades reais?", ["Fazer uma autoavalia\u00e7\u00e3o ap\u00f3s estudar", "Ignorar quest\u00f5es erradas", "Estudar s\u00f3 na v\u00e9spera"], "Fazer uma autoavalia\u00e7\u00e3o ap\u00f3s estudar"), ("Qual registro \u00e9 mais \u00fatil para o professor?", ["Uma d\u00favida espec\u00edfica", "Nenhum registro", "Apenas dizer que entendeu tudo"], "Uma d\u00favida espec\u00edfica")],
    "adaptabilidade": [("O que significa adaptabilidade no estudo?", ["Nunca mudar a forma de estudar", "Testar estrat\u00e9gias quando algo n\u00e3o funciona", "Desistir do conte\u00fado dif\u00edcil"], "Testar estrat\u00e9gias quando algo n\u00e3o funciona"), ("Qual exemplo mostra autonomia?", ["Identificar uma dificuldade e buscar solu\u00e7\u00e3o", "Esperar algu\u00e9m fazer por voc\u00ea", "Evitar tarefas novas"], "Identificar uma dificuldade e buscar solu\u00e7\u00e3o"), ("Depois de testar um novo m\u00e9todo, o ideal \u00e9:", ["Analisar se ajudou ou n\u00e3o", "Nunca repetir", "N\u00e3o registrar nada"], "Analisar se ajudou ou n\u00e3o")],
    "fisico": [("Uma boa participa\u00e7\u00e3o f\u00edsica envolve:", ["Regularidade e cuidado", "Competir sem respeitar limites", "Evitar toda atividade"], "Regularidade e cuidado"), ("Antes de uma atividade pr\u00e1tica, \u00e9 importante:", ["Preparar o corpo e seguir orienta\u00e7\u00f5es", "Come\u00e7ar sem aten\u00e7\u00e3o", "Ignorar instru\u00e7\u00f5es"], "Preparar o corpo e seguir orienta\u00e7\u00f5es"), ("Evolu\u00e7\u00e3o f\u00edsica na escola deve valorizar:", ["Progresso individual e participa\u00e7\u00e3o", "Somente vencer os outros", "Apenas for\u00e7a"], "Progresso individual e participa\u00e7\u00e3o")],
    "social": [("Qual atitude fortalece o atributo social?", ["Escutar e contribuir no grupo", "Interromper todos", "Nunca participar"], "Escutar e contribuir no grupo"), ("Ajudar um colega \u00e9 valioso quando:", ["Ajuda o outro a entender melhor", "Faz a tarefa por ele sem explicar", "Serve apenas para aparecer"], "Ajuda o outro a entender melhor"), ("Uma boa evid\u00eancia social pode ser:", ["Relato objetivo da participa\u00e7\u00e3o no grupo", "Nenhum detalhe", "Uma frase vaga"], "Relato objetivo da participa\u00e7\u00e3o no grupo")],
}

PERIODO_ATUAL = "Ciclo Avaliativo 2026.1"

LANGUAGES = {
    "pt": "Português",
    "en": "English",
    "es": "Español",
}

TRANSLATIONS = {
    "pt": {
        "nav.home": "Início",
        "nav.ranking": "Ranking",
        "nav.routine": "Rotina",
        "nav.goals": "Metas",
        "nav.recommendations": "Recomendações",
        "nav.recognitions": "Reconhecimentos",
        "nav.events": "Eventos",
        "nav.notices": "Avisos",
        "nav.history": "Histórico",
        "nav.teacher": "Professor",
        "nav.reports": "Relatórios",
        "nav.interventions": "Intervenções",
        "nav.management": "Gestão",
        "nav.settings": "Configurações",
        "nav.logout": "Sair",
        "ui.menu": "Menu",
        "settings.eyebrow": "Preferências do sistema",
        "settings.title": "Configurações",
        "settings.subtitle": "Ajuste aparência, idioma, privacidade e foco de estudo.",
        "settings.language": "Idioma",
        "settings.language_help": "Altera os textos principais da interface.",
        "settings.theme_button": "Salvar preferências",
        "settings.saved": "Preferências salvas.",
        "focus.inactive": "Modo foco inativo",
        "focus.title": "Modo foco",
        "focus.description": "Ative um ciclo de estudo. Durante o tempo escolhido, apps marcados como distração recebem bloqueio ou aviso motivacional dentro do SERE.",
        "focus.duration": "Duração",
        "focus.behavior": "Comportamento",
        "focus.apps": "Apps que distraem",
        "focus.start": "Ativar modo foco",
        "focus.stop": "Desativar",
        "display.title": "Preferências de exibição",
        "landing.cta": "Entrar",
        "login.user": "Usuário",
        "login.password": "Senha",
        "login.submit": "Entrar",
        "login.back": "Voltar para a apresentação",
    },
    "en": {
        "nav.home": "Home",
        "nav.ranking": "Ranking",
        "nav.routine": "Routine",
        "nav.goals": "Goals",
        "nav.recommendations": "Recommendations",
        "nav.recognitions": "Recognitions",
        "nav.events": "Events",
        "nav.notices": "Notices",
        "nav.history": "History",
        "nav.teacher": "Teacher",
        "nav.reports": "Reports",
        "nav.interventions": "Interventions",
        "nav.management": "Management",
        "nav.settings": "Settings",
        "nav.logout": "Sign out",
        "ui.menu": "Menu",
        "settings.eyebrow": "System preferences",
        "settings.title": "Settings",
        "settings.subtitle": "Adjust appearance, language, privacy and study focus.",
        "settings.language": "Language",
        "settings.language_help": "Changes the main interface labels.",
        "settings.theme_button": "Save preferences",
        "settings.saved": "Preferences saved.",
        "focus.inactive": "Focus mode inactive",
        "focus.title": "Focus mode",
        "focus.description": "Start a study cycle. During the selected time, distracting apps receive a block or motivational warning inside SERE.",
        "focus.duration": "Duration",
        "focus.behavior": "Behavior",
        "focus.apps": "Distracting apps",
        "focus.start": "Start focus mode",
        "focus.stop": "Stop",
        "display.title": "Display preferences",
        "landing.cta": "Sign in",
        "login.user": "User",
        "login.password": "Password",
        "login.submit": "Sign in",
        "login.back": "Back to overview",
    },
    "es": {
        "nav.home": "Inicio",
        "nav.ranking": "Ranking",
        "nav.routine": "Rutina",
        "nav.goals": "Metas",
        "nav.recommendations": "Recomendaciones",
        "nav.recognitions": "Reconocimientos",
        "nav.events": "Eventos",
        "nav.notices": "Avisos",
        "nav.history": "Historial",
        "nav.teacher": "Profesor",
        "nav.reports": "Informes",
        "nav.interventions": "Intervenciones",
        "nav.management": "Gestión",
        "nav.settings": "Configuración",
        "nav.logout": "Salir",
        "ui.menu": "Menú",
        "settings.eyebrow": "Preferencias del sistema",
        "settings.title": "Configuración",
        "settings.subtitle": "Ajusta apariencia, idioma, privacidad y foco de estudio.",
        "settings.language": "Idioma",
        "settings.language_help": "Cambia los textos principales de la interfaz.",
        "settings.theme_button": "Guardar preferencias",
        "settings.saved": "Preferencias guardadas.",
        "focus.inactive": "Modo foco inactivo",
        "focus.title": "Modo foco",
        "focus.description": "Activa un ciclo de estudio. Durante el tiempo elegido, las apps marcadas como distracción reciben bloqueo o aviso motivacional dentro de SERE.",
        "focus.duration": "Duración",
        "focus.behavior": "Comportamiento",
        "focus.apps": "Apps que distraen",
        "focus.start": "Activar modo foco",
        "focus.stop": "Desactivar",
        "display.title": "Preferencias de visualización",
        "landing.cta": "Entrar",
        "login.user": "Usuario",
        "login.password": "Contraseña",
        "login.submit": "Entrar",
        "login.back": "Volver a la presentación",
    },
}

EVENTS = [
    {
        "id": "matematica",
        "nome": "Semana da Matem\u00e1tica",
        "missao": "Resolver desafios objetivos e comprovar estudo por mini prova.",
        "recompensa": "+3 Acad\u00eamico para participantes aprovados",
        "area": "Acad\u00eamico",
        "status": "Ativo",
        "turmas": ["2\u00baA", "2\u00baB", "2\u00baC"],
    },
    {
        "id": "colaboracao",
        "nome": "Desafio de Colabora\u00e7\u00e3o",
        "missao": "Ajudar colegas, participar de debates e registrar evid\u00eancias.",
        "recompensa": "+2 Social para destaques da semana",
        "area": "Social",
        "status": "Em breve",
        "turmas": ["2\u00baA", "2\u00baB"],
    },
]

CHALLENGES = [
    {"nome": "Desempenho Acadêmico", "turma_a": "2\u00baA", "turma_b": "2\u00baB", "area": "Acad\u00eamico", "status": "Em andamento"},
    {"nome": "Conex\u00e3o Social", "turma_a": "2\u00baB", "turma_b": "2\u00baC", "area": "Social", "status": "Em andamento"},
    {"nome": "Regularidade F\u00edsica", "turma_a": "2\u00baA", "turma_b": "2\u00baC", "area": "F\u00edsico", "status": "Finalizado"},
]

def cls(p):
    return class_letter(p)


def conceito(media):
    return concept(media)


def calcular_media(aluno):
    return overall_with_concept(aluno)


def geral(a):
    return overall_score(a)


def risco_pedagogico(aluno):
    area = priority_area(aluno)
    if aluno["geral"] < 50:
        return "Alto", f"Indice SERE abaixo de 50; prioridade em {RECS[area][0]}."
    if aluno["geral"] < 65 or aluno[area] < 45:
        return "Moderado", f"Acompanhar evolucao em {RECS[area][0]}."
    if aluno["geral"] < 75:
        return "Leve", "Manter metas curtas e acompanhamento preventivo."
    return "Estavel", "Evolucao dentro da faixa esperada."


def safe_int(value, default=60, minimum=0, maximum=100):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def clean_name(value):
    normalized = unicodedata.normalize("NFKC", value or "")
    allowed_marks = {" ", ".", "'", "-"}
    cleaned = "".join(
        ch for ch in normalized
        if ch in allowed_marks or ch.isalpha() or ch.isdigit()
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:80]


def clean_username(value):
    return re.sub(r"[^a-zA-Z0-9_.-]", "", value or "").strip()[:40]


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password_hash TEXT, role TEXT, student_id INTEGER);
        CREATE TABLE IF NOT EXISTS students(id INTEGER PRIMARY KEY, nome TEXT, inicial TEXT, turma TEXT, turma_id TEXT, academico INTEGER, adaptabilidade INTEGER, fisico INTEGER, social INTEGER, geral INTEGER, titulo TEXT, observacoes TEXT, perfil_bio TEXT);
        CREATE TABLE IF NOT EXISTS student_titles(student_id INTEGER, title TEXT, PRIMARY KEY(student_id,title));
        CREATE TABLE IF NOT EXISTS available_titles(id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT, tipo TEXT, categoria TEXT, status TEXT, requisito TEXT);
        CREATE TABLE IF NOT EXISTS history_initial(student_id INTEGER PRIMARY KEY, academico INTEGER, adaptabilidade INTEGER, fisico INTEGER, social INTEGER, geral INTEGER);
        CREATE TABLE IF NOT EXISTS recommendation_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, area TEXT, score INTEGER, total INTEGER, approved INTEGER, reflection TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS goals(id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, titulo TEXT, area TEXT, alvo INTEGER, progresso INTEGER, status TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS classes(id TEXT PRIMARY KEY, nome TEXT UNIQUE, descricao TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT, missao TEXT, recompensa TEXT, area TEXT, status TEXT, turmas TEXT, duracao TEXT);
        CREATE TABLE IF NOT EXISTS missions(id INTEGER PRIMARY KEY AUTOINCREMENT, titulo TEXT, tipo TEXT, objetivo TEXT, recompensa TEXT, status TEXT);
        CREATE TABLE IF NOT EXISTS approval_requests(id INTEGER PRIMARY KEY AUTOINCREMENT, requester_id INTEGER, requester_name TEXT, action TEXT, payload TEXT, status TEXT DEFAULT 'Pendente', reviewer_id INTEGER, review_note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, reviewed_at TEXT);
        CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, tipo TEXT, titulo TEXT, texto TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS interventions(id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, motivo TEXT, acao TEXT, responsavel TEXT, prazo TEXT, status TEXT DEFAULT 'Aberta', created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS study_profiles(student_id INTEGER PRIMARY KEY, minutes_per_day INTEGER DEFAULT 45, preferred_time TEXT DEFAULT 'Noite', focus_note TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS study_plans(id INTEGER PRIMARY KEY AUTOINCREMENT, student_id INTEGER, title TEXT, plan_json TEXT, created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        cols = [r["name"] for r in con.execute("PRAGMA table_info(users)")]
        if "student_id" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN student_id INTEGER")
        if "theme" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'tema-padrao'")
        if "language" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'pt'")
        student_cols = [r["name"] for r in con.execute("PRAGMA table_info(students)")]
        if "observacoes" not in student_cols:
            con.execute("ALTER TABLE students ADD COLUMN observacoes TEXT")
        if "perfil_bio" not in student_cols:
            con.execute("ALTER TABLE students ADD COLUMN perfil_bio TEXT")
        if con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            for u, pw, role, sid in USERS:
                con.execute("INSERT INTO users(username,password_hash,role,student_id) VALUES(?,?,?,?)", (u, generate_password_hash(pw), role, sid))
        for u, _, role, sid in USERS:
            con.execute("INSERT OR IGNORE INTO users(username,password_hash,role,student_id) VALUES(?,?,?,?)", (u, generate_password_hash(os.environ.get("SERE_ALUNO_PASSWORD", "trocar-aluno-dev") if role == "aluno" else "trocar-" + role + "-dev"), role, sid))
            con.execute("UPDATE users SET role=?, student_id=?, theme=COALESCE(theme,'tema-padrao') WHERE username=?", (role, sid, u))
        con.execute("DELETE FROM users WHERE username='kiyo'")
        if con.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0:
            for s in STUDENTS:
                sid, nome, ini, turma, tid, ac, ad, fi, so, titulo, titles, hist = s
                con.execute("INSERT INTO students(id,nome,inicial,turma,turma_id,academico,adaptabilidade,fisico,social,geral,titulo,observacoes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (sid, nome, ini, turma, tid, ac, ad, fi, so, geral({"academico": ac, "adaptabilidade": ad, "fisico": fi, "social": so}), titulo, "Perfil inicial importado para o SERE."))
                for t in titles:
                    con.execute("INSERT INTO student_titles VALUES(?,?)", (sid, t))
                con.execute("INSERT INTO history_initial VALUES(?,?,?,?,?,?)", (sid, *hist, geral({"academico": hist[0], "adaptabilidade": hist[1], "fisico": hist[2], "social": hist[3]})))
        if con.execute("SELECT COUNT(*) FROM available_titles").fetchone()[0] == 0:
            for t in TITLES:
                con.execute("INSERT INTO available_titles(nome,tipo,categoria,status,requisito) VALUES(?,?,?,?,?)", t)
        con.execute("DELETE FROM available_titles WHERE nome LIKE '%🏆%' OR nome LIKE '%🎯%' OR nome LIKE '%📘%' OR nome LIKE '%⭐%' OR nome LIKE '%⚡%' OR nome LIKE '%💪%' OR nome LIKE '%🔥%' OR nome LIKE '%Campeão%' OR nome LIKE '%MVP%' OR nome LIKE '%Atleta%'")
        for t in TITLES:
            if con.execute("SELECT 1 FROM available_titles WHERE nome=? LIMIT 1", (t[0],)).fetchone() is None:
                con.execute("INSERT INTO available_titles(nome,tipo,categoria,status,requisito) VALUES(?,?,?,?,?)", t)
        if con.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 0:
            for s in STUDENTS:
                sid, nome, _ini, _turma, _tid, ac, ad, fi, so, *_ = s
                values = {"academico": ac, "adaptabilidade": ad, "fisico": fi, "social": so}
                area = min(values, key=values.get)
                area_nome = RECS[area][0]
                con.execute("INSERT INTO goals(student_id,titulo,area,alvo,progresso,status) VALUES(?,?,?,?,?,?)", (sid, f"Elevar {area_nome} em 5 pontos", area, min(100, values[area] + 5), values[area], "Em andamento"))
                con.execute("INSERT INTO goals(student_id,titulo,area,alvo,progresso,status) VALUES(?,?,?,?,?,?)", (sid, "Concluir 2 atividades validadas", "atividade", 2, 0, "Em andamento"))
                media, _conceito = calcular_media(values)
                con.execute("INSERT INTO goals(student_id,titulo,area,alvo,progresso,status) VALUES(?,?,?,?,?,?)", (sid, "Manter evolu\u00e7\u00e3o geral positiva", "geral", 1, 1 if media >= 75 else 0, "Conclu\u00edda" if media >= 75 else "Em andamento"))
        for tid, name in CLASS_SEEDS:
            con.execute("INSERT OR IGNORE INTO classes(id,nome,descricao) VALUES(?,?,?)", (tid, name, "Turma cadastrada no SERE."))
        if con.execute("SELECT COUNT(*) FROM students").fetchone()[0] < 300:
            for s in generated_students():
                sid, nome, ini, turma, tid, ac, ad, fi, so, titulo, titles, hist = s
                avg = geral({"academico": ac, "adaptabilidade": ad, "fisico": fi, "social": so})
                con.execute(
                    "INSERT OR IGNORE INTO students(id,nome,inicial,turma,turma_id,academico,adaptabilidade,fisico,social,geral,titulo,observacoes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sid, nome, ini, turma, tid, ac, ad, fi, so, avg, titulo, "Aluno ficticio gerado para simular o painel SERE."),
                )
                for t in titles:
                    con.execute("INSERT OR IGNORE INTO student_titles VALUES(?,?)", (sid, t))
                con.execute("INSERT OR IGNORE INTO history_initial VALUES(?,?,?,?,?,?)", (sid, *hist, geral({"academico": hist[0], "adaptabilidade": hist[1], "fisico": hist[2], "social": hist[3]})))
                if con.execute("SELECT 1 FROM goals WHERE student_id=? LIMIT 1", (sid,)).fetchone() is None:
                    values = {"academico": ac, "adaptabilidade": ad, "fisico": fi, "social": so}
                    area = min(values, key=values.get)
                    con.execute("INSERT INTO goals(student_id,titulo,area,alvo,progresso,status) VALUES(?,?,?,?,?,?)", (sid, f"Meta de recuperacao em {RECS[area][0]}", area, min(100, values[area] + 8), values[area], "Em andamento"))
                    con.execute("INSERT INTO goals(student_id,titulo,area,alvo,progresso,status) VALUES(?,?,?,?,?,?)", (sid, "Concluir comprovação de estudo", "atividade", 2, 0, "Em andamento"))
        if con.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0:
            for event in EVENTS:
                con.execute("INSERT INTO events(nome,missao,recompensa,area,status,turmas,duracao) VALUES(?,?,?,?,?,?,?)", (event["nome"], event["missao"], event["recompensa"], event["area"], event["status"], ",".join(event["turmas"]), "1 semana"))
        if con.execute("SELECT COUNT(*) FROM missions").fetchone()[0] == 0:
            seeds = [
                ("Entregar atividade", "Di\u00e1ria", "Concluir uma atividade proposta pelo professor.", "+1 ponto no atributo relacionado", "Ativa"),
                ("Participar da aula", "Di\u00e1ria", "Registrar participa\u00e7\u00e3o em debate, exerc\u00edcio ou grupo.", "+1 Social", "Ativa"),
                ("Resumo semanal", "Semanal", "Produzir um resumo curto do conte\u00fado da semana.", "+2 Acad\u00eamico", "Ativa"),
                ("Pesquisa guiada", "Semanal", "Pesquisar um tema e apresentar evid\u00eancias.", "+2 Adaptabilidade", "Ativa"),
            ]
            for mission in seeds:
                con.execute("INSERT INTO missions(titulo,tipo,objetivo,recompensa,status) VALUES(?,?,?,?,?)", mission)
        for s in STUDENTS:
            sid, nome, ini, turma, tid, *_rest = s
            titulo, titles = s[9], s[10]
            con.execute("UPDATE students SET nome=?, inicial=?, turma=?, turma_id=?, titulo=? WHERE id=?", (nome, ini, turma, tid, titulo, sid))
            con.execute("DELETE FROM student_titles WHERE student_id=? AND (title LIKE '%🏆%' OR title LIKE '%🎯%' OR title LIKE '%📘%' OR title LIKE '%⭐%' OR title LIKE '%⚡%' OR title LIKE '%💪%' OR title LIKE '%🔥%' OR title LIKE '%Campeão%' OR title LIKE '%MVP%' OR title LIKE '%Atleta%')", (sid,))
            for t in titles:
                con.execute("INSERT OR IGNORE INTO student_titles VALUES(?,?)", (sid, t))


def is_admin():
    return session.get("role") == "admin"


def is_prof():
    return session.get("role") == "professor"


def is_student():
    return session.get("role") == "aluno"


def is_logged_in():
    return "usuario_id" in session


def current_language():
    lang = session.get("language", "pt")
    return lang if lang in LANGUAGES else "pt"


def translate(key):
    lang = current_language()
    return TRANSLATIONS.get(lang, TRANSLATIONS["pt"]).get(key, TRANSLATIONS["pt"].get(key, key))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def manager_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        if not (is_admin() or is_prof()):
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        if not is_admin():
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def can_access_student(sid):
    if not is_logged_in() or get_student(sid) is None:
        return False
    if is_admin() or is_prof():
        return True
    return is_student() and session.get("student_id") == sid


def require_manager():
    if "usuario_id" not in session:
        return False
    return is_admin() or is_prof()


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


@app.context_processor
def ctx():
    return {
        "classe": cls,
        "csrf_token": csrf_token(),
        "is_admin": is_admin(),
        "is_professor": is_prof(),
        "is_aluno": is_student(),
        "usuario_atual": session.get("username"),
        "periodo_atual": PERIODO_ATUAL,
        "tema_atual": session.get("theme", "tema-padrao"),
        "idioma_atual": current_language(),
        "idiomas": LANGUAGES,
        "t": translate,
    }


def all_students():
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM students")]
        ts = con.execute("SELECT * FROM student_titles").fetchall()
    by = {}
    for t in ts:
        by.setdefault(t["student_id"], []).append(t["title"])
    for r in rows:
        r["titulos"] = by.get(r["id"], [])
        r["geral"], r["conceito"] = calcular_media(r)
    return rows


def all_classes():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM classes ORDER BY nome")]


def all_events():
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM events ORDER BY id DESC")]
    for r in rows:
        r["turmas"] = [t.strip() for t in (r.get("turmas") or "").split(",") if t.strip()]
    return rows


def all_missions():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM missions ORDER BY tipo,id DESC")]


def log_event(student_id, tipo, titulo, texto):
    with db() as con:
        con.execute("INSERT INTO audit_log(student_id,tipo,titulo,texto) VALUES(?,?,?,?)", (student_id, tipo, titulo, texto))


def all_interventions():
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM interventions ORDER BY CASE status WHEN 'Aberta' THEN 0 WHEN 'Em acompanhamento' THEN 1 WHEN 'Concluida' THEN 2 ELSE 3 END, prazo IS NULL, prazo, id DESC")]
    students = {a["id"]: a for a in prepare_students()}
    out = []
    for row in rows:
        aluno = students.get(row["student_id"])
        if not aluno:
            continue
        row["aluno"] = aluno
        out.append(row)
    return out


def interventions_for_students(students):
    ids = {a["id"] for a in students}
    return [item for item in all_interventions() if item["student_id"] in ids]


def intervention_summary(students):
    items = interventions_for_students(students)
    abertas = [item for item in items if item["status"] in {"Aberta", "Em acompanhamento"}]
    concluidas = [item for item in items if item["status"] == "Concluida"]
    return {"total": len(items), "abertas": len(abertas), "concluidas": len(concluidas), "items": items}


def study_profile_for(sid):
    with db() as con:
        row = con.execute("SELECT * FROM study_profiles WHERE student_id=?", (sid,)).fetchone()
    if row:
        return dict(row)
    return {"student_id": sid, "minutes_per_day": 45, "preferred_time": "Noite", "focus_note": ""}


def save_study_profile(sid, form):
    if not can_access_student(sid):
        abort(403)
    minutes = safe_int(form.get("minutes_per_day"), default=45, minimum=15, maximum=240)
    preferred = (form.get("preferred_time") or "Noite").strip()[:40]
    note = (form.get("focus_note") or "").strip()[:300]
    with db() as con:
        con.execute(
            """
            INSERT INTO study_profiles(student_id,minutes_per_day,preferred_time,focus_note,updated_at)
            VALUES(?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(student_id) DO UPDATE SET
              minutes_per_day=excluded.minutes_per_day,
              preferred_time=excluded.preferred_time,
              focus_note=excluded.focus_note,
              updated_at=CURRENT_TIMESTAMP
            """,
            (sid, minutes, preferred, note),
        )


def latest_study_plan(sid):
    with db() as con:
        row = con.execute("SELECT * FROM study_plans WHERE student_id=? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["plan"] = json.loads(item["plan_json"])
    return item


def generate_study_plan_for(aluno, profile):
    area = priority_area(aluno)
    area_nome, titulo, meta, actions = RECS[area]
    minutes = int(profile["minutes_per_day"])
    review = max(10, round(minutes * 0.4))
    practice = max(10, round(minutes * 0.4))
    reflection = max(5, minutes - review - practice)
    days = ["Segunda", "Terca", "Quarta", "Quinta", "Sexta", "Sabado", "Domingo"]
    focus_rotation = [
        (area_nome, actions[0]),
        ("Revisao ativa", actions[1]),
        ("Evidencia", actions[2]),
        ("Indice SERE", "Revisar uma meta ativa e registrar uma dificuldade."),
        ("Simulado curto", "Resolver questoes curtas e revisar erros."),
        ("Acompanhamento individual", "Revisar metas, evidencias e proximos passos com orientacao pedagogica."),
        ("Recuperacao leve", "Organizar material e planejar a proxima semana."),
    ]
    agenda = []
    for day, (focus, action) in zip(days, focus_rotation):
        agenda.append({
            "dia": day,
            "horario": profile["preferred_time"],
            "foco": focus,
            "duracao": minutes,
            "blocos": [
                f"{review} min - revisar conteudo e anotar pontos fracos",
                f"{practice} min - praticar com exercicios ou tarefa objetiva",
                f"{reflection} min - registrar evidencia do estudo",
            ],
            "acao": action,
        })
    return {
        "titulo": f"Rotina SERE - {area_nome}",
        "prioridade": area_nome,
        "meta": meta,
        "observacao": profile.get("focus_note") or "Plano gerado com base no Indice SERE, metas e area prioritaria.",
        "agenda": agenda,
    }


def save_generated_study_plan(sid, created_by=None):
    aluno = get_student(sid)
    if not aluno:
        abort(404)
    profile = study_profile_for(sid)
    plan = generate_study_plan_for(aluno, profile)
    with db() as con:
        con.execute(
            "INSERT INTO study_plans(student_id,title,plan_json,created_by) VALUES(?,?,?,?)",
            (sid, plan["titulo"], json.dumps(plan, ensure_ascii=False), created_by or session.get("usuario_id")),
        )
    log_event(sid, "Rotina", "Rotina de estudos gerada", f"Plano semanal criado para prioridade em {plan['prioridade']}.")
    return plan


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.before_request
def csrf_protect():
    if request.method != "POST":
        return None
    token = session.get("csrf_token")
    submitted = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    if not token or not submitted or not secrets.compare_digest(token, submitted):
        abort(403)
    return None


def prepare_students():
    ranking = sorted(all_students(), key=lambda x: x["geral"], reverse=True)
    for i, a in enumerate(ranking, 1):
        a.update(rank_geral=i, classe=cls(a["geral"]), media=a["geral"], academico_classe=cls(a["academico"]), adaptabilidade_classe=cls(a["adaptabilidade"]), fisico_classe=cls(a["fisico"]), social_classe=cls(a["social"]))
        a["indice_sere"] = a["geral"]
        a["nivel"] = a["classe"]
        a["risco"], a["risco_motivo"] = risco_pedagogico(a)
        if not a.get("observacoes"):
            a["observacoes"] = f"Aluno com destaque em {max({'Acad\u00eamico': a['academico'], 'Adaptabilidade': a['adaptabilidade'], 'F\u00edsico': a['fisico'], 'Social': a['social']}, key={'Acad\u00eamico': a['academico'], 'Adaptabilidade': a['adaptabilidade'], 'F\u00edsico': a['fisico'], 'Social': a['social']}.get)} e desempenho geral classe {a['classe']}."
        a["evolucao"] = "Evolu\u00e7\u00e3o positiva e desempenho consistente no ano letivo." if a["geral"] >= 80 else "Boa evolu\u00e7\u00e3o geral, com potencial claro para subir de classe."
    for turma in sorted(set(a["turma"] for a in ranking)):
        for i, a in enumerate(sorted([x for x in ranking if x["turma"] == turma], key=lambda x: x["geral"], reverse=True), 1):
            a["rank_turma"] = i
    return ranking


def visible_students():
    students = prepare_students()
    if is_student():
        return [a for a in students if a["id"] == session.get("student_id")]
    return students


def current_student():
    if not is_student():
        return None
    return get_student(session.get("student_id"))


def current_student_peers():
    aluno = current_student()
    if not aluno:
        return []
    return [a for a in prepare_students() if a["turma_id"] == aluno["turma_id"]]


def priority_area(a):
    return min({"academico": a["academico"], "adaptabilidade": a["adaptabilidade"], "fisico": a["fisico"], "social": a["social"]}, key={"academico": a["academico"], "adaptabilidade": a["adaptabilidade"], "fisico": a["fisico"], "social": a["social"]}.get)


def attempts():
    with db() as con:
        rows = con.execute("SELECT * FROM recommendation_attempts ORDER BY created_at DESC,id DESC").fetchall()
    last, ok = {}, set()
    for r in rows:
        key = (r["student_id"], r["area"])
        last.setdefault(key, dict(r))
        if r["approved"]:
            ok.add(key)
    return last, ok


def recommendations(students):
    last, ok = attempts()
    out = []
    for a in students:
        area = priority_area(a)
        nome, title, meta, actions = RECS[area]
        key = (a["id"], area)
        out.append({"aluno": a, "area": area, "area_nome": nome, "titulo": title, "meta": meta, "acoes": actions, "status": "Comprovado" if key in ok else ("Revisar" if key in last else "Pendente"), "tentativa": last.get(key)})
    return out


def goals_for(students):
    ids = [a["id"] for a in students]
    if not ids:
        return []
    rows = []
    with db() as con:
        for sid in ids:
            rows.extend(dict(r) for r in con.execute("SELECT * FROM goals WHERE student_id=? ORDER BY status DESC,id", (sid,)))
    by_student = {a["id"]: a for a in students}
    for g in rows:
        g["aluno"] = by_student.get(g["student_id"])
        g["percentual"] = 100 if g["alvo"] <= 0 else min(100, round((g["progresso"] / g["alvo"]) * 100))
        g["area_nome"] = RECS[g["area"]][0] if g["area"] in RECS else ("Atividade" if g["area"] == "atividade" else "Geral")
    return rows


def quests_for(students):
    quests = []
    for aluno in students:
        area = priority_area(aluno)
        area_nome, _titulo, _meta, actions = RECS[area]
        quests.extend([
            {
                "aluno": aluno,
                "tipo": "Rank",
                "titulo": f"Subir 3 posicoes no ranking de {area_nome}",
                "descricao": f"Pontue em {area_nome} ate ultrapassar concorrentes diretos da sua faixa.",
                "recompensa": "+2 no atributo prioritario",
                "dificuldade": "Alta" if aluno["geral"] >= 78 else "Media",
                "percentual": min(96, max(18, aluno[area])),
            },
            {
                "aluno": aluno,
                "tipo": "Prova",
                "titulo": f"Validar merito em {area_nome}",
                "descricao": actions[0],
                "recompensa": "Titulo temporario de desempenho",
                "dificuldade": "Media",
                "percentual": 35 if aluno[area] < 60 else 62,
            },
            {
                "aluno": aluno,
                "tipo": "Conduta",
                "titulo": "Manter contribuicao social sem penalidade",
                "descricao": "Fechar a semana sem faltas, atrasos ou registros negativos.",
                "recompensa": "+1 Social e estabilidade no indice",
                "dificuldade": "Baixa" if aluno["social"] >= 70 else "Media",
                "percentual": aluno["social"],
            },
        ])
    return quests


def sync_goals(sid):
    aluno = get_student(sid)
    if not aluno:
        return
    with db() as con:
        for area in ["academico", "adaptabilidade", "fisico", "social"]:
            con.execute("UPDATE goals SET progresso=?, status=CASE WHEN ? >= alvo THEN 'Conclu\u00edda' ELSE status END WHERE student_id=? AND area=?", (aluno[area], aluno[area], sid, area))
        done = con.execute("SELECT COUNT(*) FROM recommendation_attempts WHERE student_id=? AND approved=1", (sid,)).fetchone()[0]
        con.execute("UPDATE goals SET progresso=?, status=CASE WHEN ? >= alvo THEN 'Conclu\u00edda' ELSE status END WHERE student_id=? AND area='atividade'", (done, done, sid))


def timeline(students):
    hist = history(students)
    goals = goals_for(students)
    with db() as con:
        attempts_rows = [dict(r) for r in con.execute("SELECT * FROM recommendation_attempts ORDER BY created_at DESC,id DESC")]
        logs = [dict(r) for r in con.execute("SELECT * FROM audit_log ORDER BY created_at DESC,id DESC")]
    names = {a["id"]: a for a in students}
    visible_ids = set(names)
    items = []
    for item in hist:
        a = item["aluno"]
        items.append({"tipo": "Evolu\u00e7\u00e3o", "titulo": f"{a['nome']} evoluiu de {item['classe_inicial']} para {item['classe_atual']}", "texto": f"Maior avan\u00e7o: {item['maior_avanco'].capitalize()} +{item['maior_delta']}.", "data": "Ano letivo atual", "aluno": a})
        for key, ev in item["evolucoes"].items():
            if key != "geral" and ev["delta"] != 0:
                items.append({"tipo": "Atributo", "titulo": f"{key.capitalize()} mudou de {ev['inicio']} para {ev['atual']}", "texto": f"{a['nome']} teve varia\u00e7\u00e3o de {ev['delta']:+d} ponto(s).", "data": PERIODO_ATUAL, "aluno": a})
        for title in a.get("titulos", []):
            items.append({"tipo": "Reconhecimento", "titulo": title, "texto": f"{a['nome']} possui este reconhecimento registrado no perfil.", "data": PERIODO_ATUAL, "aluno": a})
    for g in goals:
        if g["status"] == "Conclu\u00edda":
            items.append({"tipo": "Meta", "titulo": g["titulo"], "texto": f"{g['aluno']['nome']} concluiu uma meta de {g['area_nome']}.", "data": "Meta ativa", "aluno": g["aluno"]})
    for r in attempts_rows:
        if r["student_id"] not in visible_ids:
            continue
        a = names[r["student_id"]]
        area_nome = RECS[r["area"]][0]
        status = "aprovada" if r["approved"] else "em revis\u00e3o"
        items.append({"tipo": "Mini prova", "titulo": f"Atividade de {area_nome} {status}", "texto": f"{a['nome']} fez {r['score']}/{r['total']} na comprova\u00e7\u00e3o.", "data": r["created_at"], "aluno": a})
    for log in logs:
        if log["student_id"] and log["student_id"] not in visible_ids:
            continue
        a = names.get(log["student_id"])
        items.append({"tipo": log["tipo"], "titulo": log["titulo"], "texto": log["texto"], "data": log["created_at"], "aluno": a})
    return items[:30]


def get_student(sid):
    return next((a for a in prepare_students() if a["id"] == sid), None)


def class_groups(students):
    groups = []
    for name in sorted(set(a["turma"] for a in students)):
        members = [a for a in students if a["turma"] == name]
        avg = round(sum(a["geral"] for a in members) / len(members))
        medias = {"Acad\u00eamico": round(sum(a["academico"] for a in members) / len(members)), "Adaptabilidade": round(sum(a["adaptabilidade"] for a in members) / len(members)), "F\u00edsico": round(sum(a["fisico"] for a in members) / len(members)), "Social": round(sum(a["social"] for a in members) / len(members))}
        best = max(medias, key=medias.get)
        hist_items = history(members)
        evo_media = round(sum(item["evolucoes"]["geral"]["delta"] for item in hist_items) / len(hist_items)) if hist_items else 0
        groups.append({"id": members[0]["turma_id"], "nome": name, "rank": 0, "titulo": "", "classe_media": cls(avg), "media_geral": avg, "melhor_area": best, "membros": len(members), "alunos": members, "medias": medias, "evolucao_media": evo_media, "historico": hist_items})
    groups.sort(key=lambda g: g["media_geral"], reverse=True)
    for i, g in enumerate(groups, 1):
        g["rank"] = i
        g["titulo"] = "Turma de maior desempenho" if i == 1 else f"Refer\u00eancia em {g['melhor_area']}"
    return groups


def challenges(students=None):
    groups = {g["nome"]: g for g in class_groups(students or visible_students())}
    area_keys = {"Acad\u00eamico": "academico", "Adaptabilidade": "adaptabilidade", "F\u00edsico": "fisico", "Social": "social"}
    out = []
    for ch in CHALLENGES:
        a = groups.get(ch["turma_a"])
        b = groups.get(ch["turma_b"])
        key = area_keys[ch["area"]]
        score_a = round(sum(s[key] for s in a["alunos"]) / len(a["alunos"])) if a else 0
        score_b = round(sum(s[key] for s in b["alunos"]) / len(b["alunos"])) if b else 0
        winner = ch["turma_a"] if score_a > score_b else ch["turma_b"] if score_b > score_a else "Empate"
        item = dict(ch)
        item.update(score_a=score_a, score_b=score_b, vencedor=winner)
        out.append(item)
    return out


def stored_challenges(students=None):
    scoped_students = students or visible_students()
    out = challenges(scoped_students)
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT nome,missao,recompensa,area,status,turmas,duracao FROM events WHERE nome LIKE 'Desafio:%' ORDER BY id DESC")]
    groups = {g["nome"]: g for g in class_groups(scoped_students)}
    area_keys = {"Acad\u00eamico": "academico", "Adaptabilidade": "adaptabilidade", "F\u00edsico": "fisico", "Social": "social"}
    for row in rows:
        turmas = [t.strip() for t in (row["turmas"] or "").split(",") if t.strip()]
        if len(turmas) < 2:
            continue
        key = area_keys.get(row["area"], "geral")
        a = groups.get(turmas[0])
        b = groups.get(turmas[1])
        score_a = round(sum(s[key] for s in a["alunos"]) / len(a["alunos"])) if a else 0
        score_b = round(sum(s[key] for s in b["alunos"]) / len(b["alunos"])) if b else 0
        winner = turmas[0] if score_a > score_b else turmas[1] if score_b > score_a else "Empate"
        out.append({"nome": row["nome"].replace("Desafio:", "").strip(), "turma_a": turmas[0], "turma_b": turmas[1], "area": row["area"], "status": row["status"], "score_a": score_a, "score_b": score_b, "vencedor": winner})
    return out


def achievements(students):
    out = []
    for a in students:
        checks = [
            ("Primeiro A", a["classe"].startswith("A"), "Alcan\u00e7ar classe A em qualquer momento."),
            ("Top 10 Geral", a["rank_geral"] <= 10, "Entrar entre os melhores do ranking geral."),
            ("100 pontos sociais", a["social"] >= 100, "Chegar ao limite do atributo social."),
            ("Classe B+ ou superior", a["geral"] >= 76, "Manter desempenho geral acima de B."),
        ]
        for name, unlocked, desc in checks:
            out.append({"aluno": a, "nome": name, "status": "Desbloqueada" if unlocked else "Bloqueada", "descricao": desc})
    return out


def history(students):
    with db() as con:
        rows = {r["student_id"]: dict(r) for r in con.execute("SELECT * FROM history_initial")}
    out = []
    for a in students:
        base = rows.get(a["id"], {})
        ev = {}
        for k in ["academico", "adaptabilidade", "fisico", "social", "geral"]:
            old = base.get(k, a[k])
            ev[k] = {"inicio": old, "atual": a[k], "delta": a[k] - old}
        best = max(ev, key=lambda k: ev[k]["delta"])
        out.append({"aluno": a, "evolucoes": ev, "maior_avanco": best, "maior_delta": ev[best]["delta"], "classe_inicial": cls(ev["geral"]["inicio"]), "classe_atual": cls(ev["geral"]["atual"])})
    return out


def dashboard_data():
    students = prepare_students()
    groups = class_groups(students)
    visible = visible_students()
    current = [a for a in students if a["id"] == session.get("student_id")]
    recs = recommendations(current if is_student() else visible)
    hist = history(current if is_student() else visible)

    if is_student():
        if not current:
            abort(403)
        aluno = current[0]
        rec = recs[0] if recs else None
        hist_item = hist[0] if hist else None
        metas = goals_for(visible)
        turma = next((g for g in groups if g["nome"] == aluno["turma"]), None)
        comparativo = {
            "media_turma": turma["media_geral"] if turma else aluno["geral"],
            "classe_turma": turma["classe_media"] if turma else aluno["classe"],
            "diferenca": round(aluno["geral"] - (turma["media_geral"] if turma else aluno["geral"]), 1),
            "posicao_turma": aluno["rank_turma"],
            "total_turma": turma["membros"] if turma else 1,
        }
        return {"modo": "aluno", "aluno": aluno, "recomendacao": rec, "historico": hist_item, "metas": metas[:3], "comparativo": comparativo}

    with db() as con:
        attempts = con.execute("SELECT approved FROM recommendation_attempts").fetchall()

    comprovadas = sum(1 for a in attempts if a["approved"])
    pendentes = sum(1 for r in recs if r["status"] != "Comprovado")
    media_geral = round(sum(a["geral"] for a in students) / len(students)) if students else 0
    top_evolucao = max(history(students), key=lambda item: item["maior_delta"]) if students else None
    atencao = sorted(students, key=lambda a: (a["geral"], a["adaptabilidade"]))[:3]

    return {
        "modo": "geral",
        "total_alunos": len(students),
        "total_turmas": len(groups),
        "media_geral": media_geral,
        "classe_media": cls(media_geral),
        "recomendacoes_pendentes": pendentes,
        "mini_provas_comprovadas": comprovadas,
        "top_alunos": students[:3],
        "top_turma": groups[0] if groups else None,
        "top_evolucao": top_evolucao,
        "alunos_atencao": atencao,
    }


def report_sheets():
    students = prepare_students()
    groups = class_groups(students)
    atencao = sorted(students, key=lambda a: (a["geral"], a["adaptabilidade"]))[:10]
    interventions = interventions_for_students(students)
    goals = goals_for(students)
    summary = intervention_summary(students)
    media_geral = round(sum(a["geral"] for a in students) / len(students), 1) if students else 0

    resumo = [
        ["Indicador", "Valor"],
        ["Periodo", PERIODO_ATUAL],
        ["Total de alunos", len(students)],
        ["Total de turmas", len(groups)],
        ["Indice SERE medio", media_geral],
        ["Intervencoes abertas", summary["abertas"]],
        ["Intervencoes concluidas", summary["concluidas"]],
        ["Alunos em atencao", len(atencao)],
    ]

    ranking = [[
        "Rank", "Aluno", "Turma", "Indice SERE", "Conceito", "Nivel",
        "Academico", "Adaptabilidade", "Fisico", "Social", "Risco", "Reconhecimento",
    ]]
    for a in students:
        ranking.append([
            a["rank_geral"], a["nome"], a["turma"], a["geral"], a["conceito"], a["classe"],
            a["academico"], a["adaptabilidade"], a["fisico"], a["social"], a["risco"], a["titulo"],
        ])

    turmas = [["Rank", "Turma", "Alunos", "Media geral", "Classe media", "Melhor atributo", "Evolucao media", "Melhor aluno", "Maior evolucao"]]
    for g in groups:
        melhor_aluno = g["alunos"][0]["nome"] if g["alunos"] else ""
        maior_evolucao = max(g["historico"], key=lambda item: item["maior_delta"])["aluno"]["nome"] if g["historico"] else ""
        turmas.append([g["rank"], g["nome"], g["membros"], g["media_geral"], g["classe_media"], g["melhor_area"], g["evolucao_media"], melhor_aluno, maior_evolucao])

    alunos = [[
        "ID", "Aluno", "Turma", "Indice SERE", "Nivel", "Risco", "Prioridade pedagogica",
        "Observacoes", "Reconhecimento",
    ]]
    for a in students:
        area = priority_area(a)
        alunos.append([a["id"], a["nome"], a["turma"], a["geral"], a["classe"], a["risco"], RECS[area][0], a["observacoes"], a["titulo"]])

    metas = [["Aluno", "Turma", "Meta", "Area", "Alvo", "Progresso", "Percentual", "Status"]]
    for goal in goals:
        aluno = goal["aluno"]
        if aluno:
            metas.append([aluno["nome"], aluno["turma"], goal["titulo"], goal["area_nome"], goal["alvo"], goal["progresso"], goal["percentual"], goal["status"]])

    planos = [["Aluno", "Turma", "Motivo", "Acao", "Responsavel", "Prazo", "Status", "Criado em"]]
    for item in interventions:
        a = item["aluno"]
        planos.append([a["nome"], a["turma"], item["motivo"], item["acao"], item["responsavel"], item["prazo"], item["status"], item["created_at"]])

    alertas = [["Aluno", "Turma", "Indice SERE", "Adaptabilidade", "Prioridade", "Recomendacao"]]
    for a in atencao:
        area = priority_area(a)
        alertas.append([a["nome"], a["turma"], a["geral"], a["adaptabilidade"], RECS[area][0], RECS[area][1]])

    return [
        ("Resumo Institucional", resumo),
        ("Ranking", ranking),
        ("Turmas", turmas),
        ("Alunos", alunos),
        ("Metas", metas),
        ("Intervencoes", planos),
        ("Atencao", alertas),
    ]


def _legacy_report_sheets():
    students = prepare_students()
    groups = class_groups(students)
    atencao = sorted(students, key=lambda a: (a["geral"], a["adaptabilidade"]))[:10]
    interventions = interventions_for_students(students)
    alunos = [[
        "Rank", "Aluno", "Turma", "Indice SERE", "Conceito", "Nivel",
        "Academico", "Autonomia e adaptacao", "Participacao pratica", "Convivencia", "Risco", "Reconhecimento",
    ]]
    for a in students:
        alunos.append([
            a["rank_geral"], a["nome"], a["turma"], a["geral"], a["conceito"], a["classe"],
            a["academico"], a["adaptabilidade"], a["fisico"], a["social"], a["risco"], a["titulo"],
        ])

    turmas = [["Rank", "Turma", "Alunos", "Media geral", "Classe media", "Melhor area", "Evolucao media"]]
    for g in groups:
        turmas.append([g["rank"], g["nome"], g["membros"], g["media_geral"], g["classe_media"], g["melhor_area"], g["evolucao_media"]])

    alertas = [["Aluno", "Turma", "Geral", "Adaptabilidade", "Prioridade", "Recomendacao"]]
    for a in atencao:
        area = priority_area(a)
        alertas.append([a["nome"], a["turma"], a["geral"], a["adaptabilidade"], RECS[area][0], RECS[area][1]])

    planos = [["Aluno", "Turma", "Motivo", "Acao", "Responsavel", "Prazo", "Status", "Criado em"]]
    for item in interventions:
        a = item["aluno"]
        planos.append([a["nome"], a["turma"], item["motivo"], item["acao"], item["responsavel"], item["prazo"], item["status"], item["created_at"]])

    return [("Alunos", alunos), ("Turmas", turmas), ("Atencao", alertas), ("Intervencoes", planos)]


def report_pdf_sections():
    students = prepare_students()
    groups = class_groups(students)
    atencao = sorted(students, key=lambda a: (a["geral"], a["adaptabilidade"]))[:10]
    interventions = interventions_for_students(students)
    intervention_stats = intervention_summary(students)
    resumo = [
        f"Total de alunos: {len(students)}",
        f"Total de turmas: {len(groups)}",
        f"Media geral: {round(sum(a['geral'] for a in students) / len(students), 1) if students else 0}",
        f"Intervencoes abertas: {intervention_stats['abertas']}",
    ]
    top_alunos = [
        f"#{a['rank_geral']} {a['nome']} - {a['turma']} - geral {a['geral']} - classe {a['classe']}"
        for a in students[:10]
    ]
    turma_rows = [
        f"#{g['rank']} {g['nome']} - {g['membros']} alunos - media {g['media_geral']} - classe {g['classe_media']} - destaque em {g['melhor_area']}"
        for g in groups
    ]
    alerta_rows = []
    for a in atencao:
        area = priority_area(a)
        alerta_rows.append(f"{a['nome']} - {a['turma']} - geral {a['geral']} - prioridade: {RECS[area][0]} - {RECS[area][1]}")
    intervention_rows = [
        f"{item['aluno']['nome']} - {item['motivo']} - {item['status']} - responsavel: {item['responsavel']} - prazo: {item['prazo'] or 'sem prazo'}"
        for item in interventions[:20]
    ] or ["Nenhuma intervencao registrada."]
    return [
        ("Resumo institucional", resumo),
        ("Top alunos", top_alunos),
        ("Turmas", turma_rows),
        ("Alunos em atencao", alerta_rows),
        ("Intervencoes pedagogicas", intervention_rows),
    ]


def student_pdf_sections(aluno):
    metas = goals_for([aluno])
    intervencoes = interventions_for_students([aluno])
    atributos = [
        f"Indice SERE: {aluno['indice_sere']} - Nivel {aluno['nivel']} - Conceito {aluno['conceito']}",
        f"Risco pedagogico: {aluno['risco']} - {aluno['risco_motivo']}",
        f"Academico: {aluno['academico']} ({aluno['academico_classe']})",
        f"Autonomia e adaptacao: {aluno['adaptabilidade']} ({aluno['adaptabilidade_classe']})",
        f"Participacao pratica: {aluno['fisico']} ({aluno['fisico_classe']})",
        f"Convivencia e participacao: {aluno['social']} ({aluno['social_classe']})",
    ]
    meta_rows = [f"{m['titulo']} - {m['status']} - {m['percentual']}%" for m in metas] or ["Nenhuma meta registrada."]
    intervention_rows = [
        f"{i['motivo']} - {i['status']} - responsavel: {i['responsavel'] or 'Nao informado'} - prazo: {i['prazo'] or 'sem prazo'}"
        for i in intervencoes
    ] or ["Nenhuma intervencao registrada."]
    return [
        ("Resumo do aluno", [f"{aluno['nome']} - {aluno['turma']}", f"Reconhecimento: {aluno['titulo']}"]),
        ("Indicadores", atributos),
        ("Metas", meta_rows),
        ("Intervencoes", intervention_rows),
        ("Observacoes", [aluno.get("observacoes") or "Sem observacoes."]),
    ]


def alerts_for_current_user():
    alerts = []
    active_events = [e for e in all_events() if e["status"] == "Ativo"]
    for event in active_events[:3]:
        alerts.append({"tipo": "Evento", "titulo": event["nome"], "texto": event["missao"], "link": url_for("pagina_eventos")})
    active_missions = [m for m in all_missions() if m["status"] == "Ativa"]
    for mission in active_missions[:3]:
        alerts.append({"tipo": mission["tipo"], "titulo": mission["titulo"], "texto": mission["objetivo"], "link": url_for("pagina_eventos")})
    for rec in recommendations(visible_students()):
        if rec["status"] != "Comprovado":
            alerts.append({"tipo": "Evolu\u00e7\u00e3o", "titulo": rec["titulo"], "texto": rec["meta"], "link": url_for("mini_prova", sid=rec["aluno"]["id"], area=rec["area"])})
    if is_student():
        sid = session.get("student_id")
        for intervention in interventions_for_students(visible_students()):
            if intervention["status"] in {"Aberta", "Em acompanhamento"}:
                alerts.append({"tipo": "Plano", "titulo": intervention["motivo"], "texto": intervention["acao"], "link": url_for("perfil", sid=intervention["student_id"])})
        if not latest_study_plan(sid):
            alerts.append({"tipo": "Rotina", "titulo": "Monte sua rotina semanal", "texto": "Informe seu tempo livre diario para receber um plano de estudo.", "link": url_for("rotina")})
    elif is_admin() or is_prof():
        summary = intervention_summary(visible_students())
        if summary["abertas"]:
            alerts.append({"tipo": "Intervencao", "titulo": f"{summary['abertas']} plano(s) em aberto", "texto": "Acompanhe prazos e status dos planos pedagogicos.", "link": url_for("intervencoes")})
    return alerts[:8]


def search_results(query):
    q = query.strip().lower()
    if not q:
        return []
    results = []
    for aluno in visible_students():
        if any(q in str(value).lower() for value in [aluno["nome"], aluno["turma"], aluno["classe"], aluno["titulo"]]):
            results.append({"tipo": "Aluno", "titulo": aluno["nome"], "texto": f"{aluno['turma']} - Classe {aluno['classe']} - Rank #{aluno['rank_geral']}", "link": url_for("perfil", sid=aluno["id"])})
    for turma in class_groups(current_student_peers() if is_student() else visible_students()):
        if q in turma["nome"].lower() or q in turma["melhor_area"].lower():
            results.append({"tipo": "Turma", "titulo": turma["nome"], "texto": f"Classe m\u00e9dia {turma['classe_media']} - Rank #{turma['rank']}", "link": url_for("perfil_turma", turma_id=turma["id"])})
    for event in all_events():
        if q in event["nome"].lower() or q in event["missao"].lower() or q in event["area"].lower():
            results.append({"tipo": "Evento", "titulo": event["nome"], "texto": event["missao"], "link": url_for("pagina_eventos")})
    for mission in all_missions():
        if q in mission["titulo"].lower() or q in mission["objetivo"].lower() or q in mission["tipo"].lower():
            results.append({"tipo": "Atividade", "titulo": mission["titulo"], "texto": mission["objetivo"], "link": url_for("pagina_eventos")})
    return results[:20]


def save_profile_customization(form):
    if not is_student():
        abort(403)
    sid = session.get("student_id")
    bio = (form.get("perfil_bio") or "").strip()[:420]
    title = (form.get("titulo") or "").strip()
    if title:
        with db() as con:
            unlocked = con.execute("SELECT 1 FROM student_titles WHERE student_id=? AND title=?", (sid, title)).fetchone()
            if not unlocked:
                abort(403)
            con.execute("UPDATE students SET titulo=?, perfil_bio=? WHERE id=?", (title, bio, sid))
    else:
        with db() as con:
            con.execute("UPDATE students SET perfil_bio=? WHERE id=?", (bio, sid))


def save_panel(form):
    updated_ids = []
    with db() as con:
        for a in prepare_students():
            sid = str(a["id"])
            vals = {k: safe_int(form.get(f"{sid}_{k}", a[k]), default=a[k]) for k in ["academico", "adaptabilidade", "fisico", "social"]}
            title = form.get(f"{sid}_titulo", "").strip() or a["titulo"]
            obs = form.get(f"{sid}_observacoes", "").strip() or a["observacoes"]
            con.execute("UPDATE students SET academico=?,adaptabilidade=?,fisico=?,social=?,geral=?,titulo=?,observacoes=? WHERE id=?", (vals["academico"], vals["adaptabilidade"], vals["fisico"], vals["social"], geral(vals), title, obs, a["id"]))
            con.execute("INSERT OR IGNORE INTO student_titles VALUES(?,?)", (a["id"], title))
            extra = form.get(f"{sid}_novo_titulo", "").strip()
            if extra:
                con.execute("INSERT OR IGNORE INTO student_titles VALUES(?,?)", (a["id"], extra))
                exists = con.execute("SELECT 1 FROM available_titles WHERE nome=?", (extra,)).fetchone()
                if not exists:
                    con.execute("INSERT INTO available_titles(nome,tipo,categoria,status,requisito) VALUES(?,?,?,?,?)", (extra, "Professor", "Personalizado", "Desbloqueado", "T\u00edtulo concedido manualmente pelo professor."))
            updated_ids.append(a["id"])
    for sid in updated_ids:
        sync_goals(sid)


def submit_approval_request(action, form):
    if action not in PANEL_ACTIONS:
        abort(400)
    payload = {key: value for key, value in form.items() if key != "csrf_token"}
    payload["action"] = action
    with db() as con:
        con.execute(
            "INSERT INTO approval_requests(requester_id,requester_name,action,payload,status) VALUES(?,?,?,?,?)",
            (session.get("usuario_id"), session.get("username"), action, json.dumps(payload, ensure_ascii=False), "Pendente"),
        )


def pending_approvals():
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM approval_requests WHERE status='Pendente' ORDER BY id DESC")]


def apply_approval_request(request_id, approve=True, note=""):
    if not is_admin():
        abort(403)
    with db() as con:
        row = con.execute("SELECT * FROM approval_requests WHERE id=?", (request_id,)).fetchone()
    if not row:
        abort(404)
    row = dict(row)
    if row["status"] != "Pendente":
        return
    if approve:
        payload = json.loads(row["payload"])
        action = payload.get("action", "update_students")
        if action not in PANEL_ACTIONS:
            abort(400)
        if action == "create_class":
            create_class(payload)
        elif action == "create_student":
            create_student(payload)
        elif action == "create_event":
            create_event(payload)
        elif action == "create_mission":
            create_mission(payload)
        else:
            save_panel(payload)
        status = "Aprovada"
    else:
        status = "Recusada"
    with db() as con:
        con.execute(
            "UPDATE approval_requests SET status=?, reviewer_id=?, review_note=?, reviewed_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, session.get("usuario_id"), note, request_id),
        )


def create_class(form):
    tid = form.get("turma_id", "").strip().upper().replace(" ", "")
    nome = form.get("turma_nome", "").strip()
    descricao = form.get("turma_descricao", "").strip() or "Turma cadastrada no SERE."
    if not tid or not nome:
        return
    with db() as con:
        con.execute("INSERT OR REPLACE INTO classes(id,nome,descricao) VALUES(?,?,?)", (tid, nome, descricao))


def create_student(form):
    nome = clean_name(form.get("aluno_nome", ""))
    turma_id = form.get("aluno_turma_id", "").strip()
    username = clean_username(form.get("aluno_usuario", ""))
    password = form.get("aluno_senha", "").strip()
    if not nome or not turma_id or not username:
        return
    if len(password) < MIN_PASSWORD_LENGTH:
        return
    with db() as con:
        turma = con.execute("SELECT * FROM classes WHERE id=?", (turma_id,)).fetchone()
        if not turma:
            return
        if con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            return
        next_id = (con.execute("SELECT COALESCE(MAX(id),0)+1 FROM students").fetchone()[0])
        vals = {k: safe_int(form.get(f"aluno_{k}", 60)) for k in ["academico", "adaptabilidade", "fisico", "social"]}
        avg = geral(vals)
        titulo = form.get("aluno_titulo", "").strip() or "\U0001f525 Em Ascens\u00e3o"
        con.execute("INSERT INTO students(id,nome,inicial,turma,turma_id,academico,adaptabilidade,fisico,social,geral,titulo,observacoes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (next_id, nome, nome[:1].upper(), turma["nome"], turma_id, vals["academico"], vals["adaptabilidade"], vals["fisico"], vals["social"], avg, titulo, "Aluno cadastrado pelo painel."))
        con.execute("INSERT OR IGNORE INTO student_titles VALUES(?,?)", (next_id, titulo))
        con.execute("INSERT INTO history_initial VALUES(?,?,?,?,?,?)", (next_id, vals["academico"], vals["adaptabilidade"], vals["fisico"], vals["social"], avg))
        con.execute("INSERT INTO users(username,password_hash,role,student_id) VALUES(?,?,?,?)", (username, generate_password_hash(password), "aluno", next_id))
    log_event(next_id, "Cadastro", "Aluno cadastrado", f"{nome} entrou no SERE na turma {turma['nome']}.")


def import_students_csv(file_storage):
    if not file_storage or not file_storage.filename:
        return 0
    try:
        content = file_storage.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return 0
    reader = csv.DictReader(io.StringIO(content))
    created = 0
    with db() as con:
        for row in reader:
            nome = clean_name(row.get("nome", ""))
            turma_id = (row.get("turma_id") or row.get("turma") or "").strip().upper().replace(" ", "")
            username = clean_username(row.get("usuario") or row.get("username") or "")
            password = (row.get("senha") or "").strip()
            if not nome or not turma_id or not username:
                continue
            if len(password) < MIN_PASSWORD_LENGTH:
                continue
            turma = con.execute("SELECT * FROM classes WHERE id=?", (turma_id,)).fetchone()
            if not turma:
                continue
            if con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                continue
            next_id = con.execute("SELECT COALESCE(MAX(id),0)+1 FROM students").fetchone()[0]
            vals = {k: safe_int(row.get(k, 60)) for k in ["academico", "adaptabilidade", "fisico", "social"]}
            avg = geral(vals)
            titulo = (row.get("titulo") or "\U0001f525 Em Ascens\u00e3o").strip()
            con.execute("INSERT INTO students(id,nome,inicial,turma,turma_id,academico,adaptabilidade,fisico,social,geral,titulo,observacoes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (next_id, nome, nome[:1].upper(), turma["nome"], turma_id, vals["academico"], vals["adaptabilidade"], vals["fisico"], vals["social"], avg, titulo, "Aluno importado por CSV."))
            con.execute("INSERT OR IGNORE INTO student_titles VALUES(?,?)", (next_id, titulo))
            con.execute("INSERT INTO history_initial VALUES(?,?,?,?,?,?)", (next_id, vals["academico"], vals["adaptabilidade"], vals["fisico"], vals["social"], avg))
            con.execute("INSERT INTO users(username,password_hash,role,student_id) VALUES(?,?,?,?)", (username, generate_password_hash(password), "aluno", next_id))
            created += 1
    return created


def create_intervention(form):
    sid = safe_int(form.get("student_id"), default=0, minimum=0, maximum=10**9)
    motivo = (form.get("motivo") or "").strip()[:120]
    acao = (form.get("acao") or "").strip()[:500]
    responsavel = (form.get("responsavel") or session.get("username") or "").strip()[:80]
    prazo = (form.get("prazo") or "").strip()[:20]
    status = form.get("status", "Aberta").strip()
    if status not in {"Aberta", "Em acompanhamento", "Concluida", "Cancelada"}:
        status = "Aberta"
    aluno = get_student(sid)
    if not aluno or not motivo or not acao:
        return None
    with db() as con:
        cur = con.execute(
            "INSERT INTO interventions(student_id,motivo,acao,responsavel,prazo,status,created_by) VALUES(?,?,?,?,?,?,?)",
            (sid, motivo, acao, responsavel, prazo, status, session.get("usuario_id")),
        )
        intervention_id = cur.lastrowid
    log_event(sid, "Intervencao", "Plano pedagogico criado", f"{motivo}: {acao}")
    return intervention_id


def update_intervention_status(intervention_id, action):
    statuses = {
        "acompanhar": "Em acompanhamento",
        "concluir": "Concluida",
        "cancelar": "Cancelada",
        "reabrir": "Aberta",
    }
    if action not in statuses:
        abort(404)
    with db() as con:
        row = con.execute("SELECT * FROM interventions WHERE id=?", (intervention_id,)).fetchone()
        if not row:
            abort(404)
        con.execute("UPDATE interventions SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (statuses[action], intervention_id))
    log_event(row["student_id"], "Intervencao", f"Plano {statuses[action].lower()}", f"Status alterado para {statuses[action]}.")


def create_event(form):
    nome = form.get("evento_nome", "").strip()
    missao = form.get("evento_missao", "").strip()
    recompensa = form.get("evento_recompensa", "").strip()
    area = form.get("evento_area", "Acad\u00eamico").strip()
    status = form.get("evento_status", "Ativo").strip()
    turmas = form.get("evento_turmas", "").strip()
    duracao = form.get("evento_duracao", "").strip() or "1 semana"
    if not nome or not missao:
        return
    with db() as con:
        con.execute("INSERT INTO events(nome,missao,recompensa,area,status,turmas,duracao) VALUES(?,?,?,?,?,?,?)", (nome, missao, recompensa, area, status, turmas, duracao))
        con.execute("INSERT INTO audit_log(student_id,tipo,titulo,texto) VALUES(NULL,?,?,?)", ("Evento", nome, f"{missao} Recompensa: {recompensa}"))


def create_mission(form):
    titulo = form.get("missao_titulo", "").strip()
    objetivo = form.get("missao_objetivo", "").strip()
    tipo = form.get("missao_tipo", "Semanal").strip()
    recompensa = form.get("missao_recompensa", "").strip()
    status = form.get("missao_status", "Ativa").strip()
    if not titulo or not objetivo:
        return
    with db() as con:
        con.execute("INSERT INTO missions(titulo,tipo,objetivo,recompensa,status) VALUES(?,?,?,?,?)", (titulo, tipo, objetivo, recompensa, status))
        con.execute("INSERT INTO audit_log(student_id,tipo,titulo,texto) VALUES(NULL,?,?,?)", ("Miss\u00e3o", titulo, objetivo))


def approved_before(sid, area):
    with db() as con:
        return con.execute("SELECT 1 FROM recommendation_attempts WHERE student_id=? AND area=? AND approved=1", (sid, area)).fetchone() is not None


def save_quiz(sid, area, form):
    qs = QUIZZES[area]
    score = sum(1 for i, q in enumerate(qs) if form.get(f"q{i}") == q[2])
    passed = score >= 2
    already = approved_before(sid, area)
    with db() as con:
        con.execute("INSERT INTO recommendation_attempts(student_id,area,score,total,approved,reflection) VALUES(?,?,?,?,?,?)", (sid, area, score, len(qs), 1 if passed else 0, form.get("reflexao", "").strip()))
        if passed and not already:
            row = con.execute("SELECT academico,adaptabilidade,fisico,social FROM students WHERE id=?", (sid,)).fetchone()
            vals = dict(row)
            vals[area] = min(100, vals[area] + 2)
            update_sql = {
                "academico": "UPDATE students SET academico=?, geral=? WHERE id=?",
                "adaptabilidade": "UPDATE students SET adaptabilidade=?, geral=? WHERE id=?",
                "fisico": "UPDATE students SET fisico=?, geral=? WHERE id=?",
                "social": "UPDATE students SET social=?, geral=? WHERE id=?",
            }[area]
            con.execute(update_sql, (vals[area], geral(vals), sid))
    sync_goals(sid)
    return {"score": score, "total": len(qs), "aprovado": passed, "bonus_aplicado": passed and not already}


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("usuario", "").strip()
        remote_addr = request.remote_addr or "local"
        if login_is_locked(remote_addr, username):
            return render_template("login.html", erro="Muitas tentativas. Aguarde alguns minutos antes de tentar novamente."), 429
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("senha", "")):
            clear_login_failures(remote_addr, username)
            session.clear()
            session.update(usuario_id=user["id"], username=user["username"], role=user["role"], student_id=user["student_id"], theme=user["theme"] or "tema-padrao", language=user["language"] or "pt")
            return redirect(url_for("dashboard"))
        record_login_failure(remote_addr, username)
        error = "Usu\u00e1rio ou senha inv\u00e1lidos."
    return render_template("login.html", erro=error)


@app.route("/")
def landing():
    if is_logged_in():
        return redirect(url_for("dashboard"))
    return render_template("landing.html")


@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", dados=dashboard_data())


@app.route("/perfil/<int:sid>")
@app.route("/aluno/<int:sid>")
@login_required
def perfil(sid):
    aluno = get_student(sid)
    if aluno is None:
        abort(403)
    can_view_private_profile = is_admin() or is_prof() or session.get("student_id") == sid
    intervencoes = interventions_for_students([aluno]) if can_view_private_profile else []
    return render_template(
        "perfil.html",
        aluno=aluno,
        intervencoes=intervencoes,
        can_view_private_profile=can_view_private_profile,
    )


@app.route("/ranking")
@login_required
def ranking():
    st = prepare_students()
    return render_template("ranking.html", ranking_alunos=st, top3=st[:3])


@app.route("/turmas")
@login_required
def pagina_turmas():
    students = current_student_peers() if is_student() else visible_students()
    return render_template("turmas.html", turmas=class_groups(students))


@app.route("/turmas/<turma_id>")
@login_required
def perfil_turma(turma_id):
    students = current_student_peers() if is_student() else visible_students()
    turma = next((g for g in class_groups(students) if g["id"] == turma_id), None)
    return redirect(url_for("pagina_turmas")) if turma is None else render_template("perfil_turma.html", turma=turma)


@app.route("/titulos", methods=["GET", "POST"])
@login_required
def pagina_titulos():
    if request.method == "POST":
        save_profile_customization(request.form)
        return redirect(url_for("pagina_titulos", salvo="1"))
    with db() as con:
        titles = [dict(r) for r in con.execute("SELECT nome,tipo,categoria,status,requisito FROM available_titles ORDER BY id")]
    aluno = get_student(session.get("student_id")) if is_student() else None
    unlocked = set(aluno["titulos"]) if aluno else set()
    return render_template("titulos.html", titulos=titles, aluno=aluno, desbloqueados=unlocked, mensagem="Perfil atualizado." if request.args.get("salvo") else None)


@app.route("/titulos/equipar", methods=["POST"])
@login_required
def equipar_titulo():
    if not is_student():
        abort(403)
    title = request.form.get("titulo", "").strip()
    sid = session.get("student_id")
    with db() as con:
        unlocked = con.execute("SELECT 1 FROM student_titles WHERE student_id=? AND title=?", (sid, title)).fetchone()
        if not unlocked:
            abort(403)
        con.execute("UPDATE students SET titulo=? WHERE id=?", (title, sid))
    return redirect(url_for("pagina_titulos"))


@app.route("/historico")
@login_required
def pagina_historico():
    students = visible_students()
    return render_template("historico.html", historico=history(students), linha_do_tempo=timeline(students))


@app.route("/metas")
@login_required
def pagina_metas():
    return render_template("metas.html", metas=goals_for(visible_students()), quests=quests_for(visible_students()))


@app.route("/professor")
@manager_required
def painel_professor_util():
    students = prepare_students()
    dados = dashboard_data()
    return render_template("professor.html", dados=dados, metas=goals_for(students), recomendacoes=recommendations(students))


@app.route("/relatorios")
@manager_required
def relatorios():
    dados = dashboard_data()
    students = prepare_students()
    return render_template("relatorios.html", dados=dados, turmas=class_groups(students), intervencoes=intervention_summary(students), alunos=students)


@app.route("/intervencoes", methods=["GET", "POST"])
@manager_required
def intervencoes():
    if request.method == "POST":
        created = create_intervention(request.form)
        return redirect(url_for("intervencoes", salvo="1" if created else "0"))
    students = prepare_students()
    return render_template(
        "intervencoes.html",
        alunos=students,
        intervencoes=interventions_for_students(students),
        mensagem="Plano criado." if request.args.get("salvo") == "1" else ("Nao foi possivel criar o plano." if request.args.get("salvo") == "0" else None),
    )


@app.route("/intervencoes/<int:intervention_id>/<action>", methods=["POST"])
@manager_required
def atualizar_intervencao(intervention_id, action):
    update_intervention_status(intervention_id, action)
    return redirect(request.referrer or url_for("intervencoes"))


@app.route("/relatorios/alunos.xlsx")
@manager_required
def relatorio_alunos_xlsx():
    workbook = xlsx_workbook(report_sheets())
    return send_file(workbook, mimetype=XLSX_MIME, as_attachment=True, download_name="Relatorio SERE.xlsx")


@app.route("/relatorios/alunos.pdf")
@manager_required
def relatorio_alunos_pdf():
    pdf = pdf_report(f"SERE - Relatorio institucional - {PERIODO_ATUAL}", report_pdf_sections())
    return send_file(pdf, mimetype=PDF_MIME, as_attachment=True, download_name="sere-relatorio-institucional.pdf")


@app.route("/relatorios/aluno/<int:sid>.pdf")
@manager_required
def relatorio_aluno_pdf(sid):
    aluno = get_student(sid)
    if aluno is None:
        abort(404)
    pdf = pdf_report(f"SERE - Relatorio individual - {aluno['nome']} - {PERIODO_ATUAL}", student_pdf_sections(aluno))
    filename = f"sere-relatorio-{clean_username(aluno['nome']).lower() or sid}.pdf"
    return send_file(pdf, mimetype=PDF_MIME, as_attachment=True, download_name=filename)


@app.route("/configuracoes", methods=["GET", "POST"])
@login_required
def configuracoes():
    temas = [
        {"id": "tema-padrao", "nome": "SERE noturno", "descricao": "Azul e roxo, competitivo e padrao do aplicativo."},
        {"id": "tema-claro", "nome": "Claro institucional", "descricao": "Branco, discreto e com destaque verde-azulado."},
        {"id": "tema-rosa", "nome": "Branco e rosa", "descricao": "Claro, limpo e com acento rosa para leitura prolongada."},
        {"id": "tema-alto-contraste", "nome": "Alto contraste", "descricao": "Escuro, forte e mais leg\u00edvel."},
        {"id": "tema-verde", "nome": "Minimal verde", "descricao": "Visual calmo para rotina de estudo."},
    ]
    if request.method == "POST":
        theme = request.form.get("theme", "tema-padrao")
        language = request.form.get("language", current_language())
        allowed = {t["id"] for t in temas}
        if theme not in allowed:
            theme = "tema-padrao"
        if language not in LANGUAGES:
            language = "pt"
        with db() as con:
            con.execute("UPDATE users SET theme=?, language=? WHERE id=?", (theme, language, session["usuario_id"]))
        session["theme"] = theme
        session["language"] = language
        return redirect(url_for("configuracoes", salvo="1"))
    aluno = get_student(session.get("student_id")) if is_student() else None
    perfil_estudo = study_profile_for(aluno["id"]) if aluno else None
    return render_template("configuracoes.html", temas=temas, aluno=aluno, perfil_estudo=perfil_estudo, mensagem=translate("settings.saved") if request.args.get("salvo") else None)


@app.route("/importar-csv", methods=["GET", "POST"])
@admin_required
def importar_csv():
    mensagem = None
    if request.method == "POST":
        total = import_students_csv(request.files.get("arquivo"))
        mensagem = f"{total} aluno(s) importado(s)."
    return render_template("importar_csv.html", mensagem=mensagem)


@app.route("/buscar")
@login_required
def buscar():
    query = request.args.get("q", "")
    return render_template("buscar.html", consulta=query, resultados=search_results(query))


@app.route("/avisos")
@login_required
def avisos():
    return render_template("avisos.html", avisos=alerts_for_current_user())


@app.route("/eventos")
@login_required
def pagina_eventos():
    students = current_student_peers() if is_student() else visible_students()
    return render_template("eventos.html", eventos=all_events(), desafios=stored_challenges(students), missoes=all_missions())


@app.route("/x1", methods=["GET", "POST"])
@login_required
def pagina_x1():
    abort(404)


@app.route("/x1/<int:challenge_id>/<action>", methods=["POST"])
@login_required
def atualizar_x1(challenge_id, action):
    abort(404)


@app.route("/x1/arena/<int:challenge_id>", methods=["GET", "POST"])
@login_required
def x1_arena(challenge_id):
    abort(404)


@app.route("/x1/history")
@login_required
def x1_history():
    abort(404)


@app.route("/x1/ia", methods=["GET", "POST"])
@login_required
def x1_ia():
    abort(404)


@app.route("/x1/ia/chess/state")
@login_required
def x1_ia_chess_state():
    abort(404)


@app.route("/x1/ia/chess/move", methods=["POST"])
@login_required
def x1_ia_chess_move():
    abort(404)


@app.route("/aprovacoes/<int:request_id>/<action>", methods=["POST"])
@admin_required
def revisar_aprovacao(request_id, action):
    if action not in ["aprovar", "recusar"]:
        abort(404)
    apply_approval_request(request_id, approve=action == "aprovar", note=request.form.get("nota", ""))
    return redirect(url_for("painel_professor", salvo="1"))


@app.route("/conquistas")
@login_required
def pagina_conquistas():
    return render_template("conquistas.html", conquistas=achievements(visible_students()))


@app.route("/recomendacoes")
@login_required
def pagina_recomendacoes():
    return render_template("recomendacoes.html", recomendacoes=recommendations(visible_students()))


@app.route("/recomendacoes/<int:sid>/<area>/prova", methods=["GET", "POST"])
@login_required
def mini_prova(sid, area):
    if area not in QUIZZES:
        abort(404)
    if not can_access_student(sid):
        abort(403)
    if is_student() and session.get("student_id") != sid:
        abort(403)
    aluno = get_student(sid)
    if aluno is None:
        abort(404)
    if is_student() and area != priority_area(aluno):
        abort(403)
    result = None
    if request.method == "POST":
        if session.get("student_id") != sid:
            abort(403)
        result = save_quiz(sid, area, request.form)
        aluno = get_student(sid)
    qs = [{"pergunta": q[0], "opcoes": q[1], "resposta": q[2]} for q in QUIZZES[area]]
    nome, titulo, *_ = RECS[area]
    return render_template("mini_prova.html", aluno=aluno, area=area, dados_area={"nome": nome, "titulo": titulo}, perguntas=qs, resultado=result, pode_responder=session.get("student_id") == sid)


@app.route("/rotina", methods=["GET", "POST"])
@login_required
def rotina():
    sid = session.get("student_id") if is_student() else safe_int(request.values.get("student_id"), default=0, minimum=0, maximum=10**9)
    if not sid:
        sid = prepare_students()[0]["id"] if prepare_students() else 0
    if not can_access_student(sid):
        abort(403)
    if request.method == "POST":
        save_study_profile(sid, request.form)
        if request.form.get("generate") == "1":
            save_generated_study_plan(sid)
        return redirect(url_for("rotina", student_id=sid, salvo="1"))
    aluno = get_student(sid)
    students = visible_students()
    profile = study_profile_for(sid)
    plan = latest_study_plan(sid)
    if not plan:
        plan = {"plan": generate_study_plan_for(aluno, profile), "created_at": "Previa"}
    return render_template(
        "rotina.html",
        aluno=aluno,
        alunos=students,
        perfil_estudo=profile,
        plano=plan,
        mensagem="Rotina atualizada." if request.args.get("salvo") else None,
    )


@app.route("/painel", methods=["GET", "POST"])
@manager_required
def painel_professor():
    if request.method == "POST":
        action = request.form.get("action", "update_students")
        if action not in PANEL_ACTIONS:
            abort(400)
        if is_prof():
            submit_approval_request(action, request.form)
            return redirect(url_for("painel_professor", solicitado="1"))
        if action == "create_class":
            create_class(request.form)
        elif action == "create_student":
            create_student(request.form)
        elif action == "create_event":
            create_event(request.form)
        elif action == "create_mission":
            create_mission(request.form)
        else:
            save_panel(request.form)
        return redirect(url_for("painel_professor", salvo="1"))
    mensagem = None
    if request.args.get("salvo"):
        mensagem = "Dados atualizados com sucesso."
    if request.args.get("solicitado"):
        mensagem = "Solicita\u00e7\u00e3o enviada para aprova\u00e7\u00e3o da dire\u00e7\u00e3o."
    return render_template("painel.html", ranking_alunos=prepare_students(), turmas=all_classes(), eventos=all_events(), missoes=all_missions(), aprovacoes=pending_approvals() if is_admin() else [], mensagem=mensagem)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/health")
def health():
    return {"status": "ok", "app": "SERE"}


@app.errorhandler(403)
def forbidden(_error):
    return render_template("erro.html", codigo=403, titulo="Acesso bloqueado", mensagem="Sua conta n\u00e3o tem permiss\u00e3o para acessar esta a\u00e7\u00e3o."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("erro.html", codigo=404, titulo="P\u00e1gina n\u00e3o encontrada", mensagem="Essa tela n\u00e3o existe ou foi movida no SERE."), 404


@app.errorhandler(500)
def server_error(_error):
    return render_template("erro.html", codigo=500, titulo="Erro interno", mensagem="Algo saiu do fluxo esperado. Tente novamente em instantes."), 500


init_db()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
