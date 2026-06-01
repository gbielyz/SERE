import os
import io
import tempfile
import unittest
import zipfile

os.environ["SERE_DB_PATH"] = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name

from app import app, init_db  # noqa: E402


class SecurityPermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)
        init_db()

    def client_as(self, role, student_id=None):
        client = app.test_client()
        user_ids = {"admin": 1, "professor": 2, "aluno": 3}
        with client.session_transaction() as session:
            session["usuario_id"] = user_ids[role]
            session["username"] = role
            session["role"] = role
            session["student_id"] = student_id
            session["theme"] = "tema-padrao"
            session["csrf_token"] = "test-token"
        return client

    def test_student_can_open_public_student_profiles(self):
        client = self.client_as("aluno", student_id=3)

        own_profile = client.get("/perfil/3")
        other_profile = client.get("/perfil/1")

        self.assertEqual(own_profile.status_code, 200)
        self.assertEqual(other_profile.status_code, 200)
        self.assertIn(b"Bio publica", other_profile.data)
        self.assertNotIn("Observa&ccedil;&otilde;es pedag&oacute;gicas".encode("utf-8"), other_profile.data)
        self.assertIn("Observa&ccedil;&otilde;es pedag&oacute;gicas".encode("utf-8"), own_profile.data)

    def test_ranking_student_name_links_to_public_profile(self):
        client = self.client_as("aluno", student_id=3)

        page = client.get("/ranking")

        self.assertEqual(page.status_code, 200)
        self.assertIn(b'class="student-name-link" href="/perfil/', page.data)

    def test_student_cannot_open_management_pages(self):
        client = self.client_as("aluno", student_id=3)

        for path in ["/professor", "/painel", "/importar-csv", "/relatorios", "/intervencoes"]:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 403)

    def test_professor_cannot_import_csv(self):
        client = self.client_as("professor")

        self.assertEqual(client.get("/professor").status_code, 200)
        self.assertEqual(client.get("/painel").status_code, 200)
        self.assertEqual(client.get("/importar-csv").status_code, 403)
        self.assertEqual(client.get("/relatorios").status_code, 200)
        self.assertEqual(client.get("/intervencoes").status_code, 200)

    def test_admin_can_open_management_pages(self):
        client = self.client_as("admin")

        for path in ["/dashboard", "/perfil/1", "/professor", "/painel", "/importar-csv", "/relatorios", "/intervencoes"]:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

    def test_admin_primary_pages_render_without_errors(self):
        client = self.client_as("admin")
        paths = [
            "/dashboard", "/ranking", "/rotina", "/turmas", "/turmas/2B",
            "/titulos", "/metas", "/eventos", "/conquistas", "/avisos", "/buscar",
            "/historico", "/recomendacoes", "/professor", "/relatorios",
            "/intervencoes", "/painel", "/configuracoes",
        ]

        for path in paths:
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"Erro interno", response.data)

    def test_theme_preference_is_saved_and_applied(self):
        client = self.client_as("admin")

        response = client.post(
            "/configuracoes",
            data={"csrf_token": "test-token", "theme": "tema-verde"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as session:
            self.assertEqual(session["theme"], "tema-verde")
        self.assertIn(b"app-shell tema-verde", client.get("/dashboard").data)

    def test_reports_download_for_manager_only(self):
        student = self.client_as("aluno", student_id=3)
        professor = self.client_as("professor")

        self.assertEqual(student.get("/relatorios/alunos.xlsx").status_code, 403)
        self.assertEqual(student.get("/relatorios/alunos.pdf").status_code, 403)

        xlsx = professor.get("/relatorios/alunos.xlsx")
        pdf = professor.get("/relatorios/alunos.pdf")
        individual = professor.get("/relatorios/aluno/3.pdf")

        self.assertEqual(xlsx.status_code, 200)
        self.assertIn("spreadsheetml.sheet", xlsx.content_type)
        self.assertTrue(xlsx.data.startswith(b"PK"))
        with zipfile.ZipFile(io.BytesIO(xlsx.data)) as workbook:
            workbook_xml = workbook.read("xl/workbook.xml").decode("utf-8")
            self.assertIn("Resumo Institucional", workbook_xml)
            self.assertIn("Ranking", workbook_xml)
            self.assertIn("Turmas", workbook_xml)
            self.assertIn("Metas", workbook_xml)
            self.assertIn("Intervencoes", workbook_xml)
            sheet1 = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("autoFilter", sheet1)
            self.assertIn("state=\"frozen\"", sheet1)
            self.assertIn("xl/styles.xml", workbook.namelist())
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.content_type, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertEqual(individual.status_code, 200)
        self.assertEqual(individual.content_type, "application/pdf")
        self.assertTrue(individual.data.startswith(b"%PDF"))

    def test_manager_can_create_and_update_intervention(self):
        professor = self.client_as("professor")

        response = professor.post(
            "/intervencoes",
            data={
                "csrf_token": "test-token",
                "student_id": "3",
                "motivo": "Queda de desempenho",
                "acao": "Revisar conteudo da semana e registrar evidencias.",
                "responsavel": "Professor",
                "prazo": "2026-06-15",
            },
        )

        self.assertEqual(response.status_code, 302)
        page = professor.get("/intervencoes")
        self.assertIn(b"Queda de desempenho", page.data)
        self.assertIn(b"Revisar conteudo", page.data)

        import re

        match = re.search(rb"/intervencoes/(\d+)/concluir", page.data)
        self.assertIsNotNone(match)
        intervention_id = match.group(1).decode()
        updated = professor.post(f"/intervencoes/{intervention_id}/concluir", data={"csrf_token": "test-token"})

        self.assertEqual(updated.status_code, 302)
        self.assertIn(b"Concluida", professor.get("/intervencoes").data)

    def test_student_cannot_create_intervention(self):
        student = self.client_as("aluno", student_id=3)

        response = student.post(
            "/intervencoes",
            data={
                "csrf_token": "test-token",
                "student_id": "3",
                "motivo": "Teste",
                "acao": "Nao deve criar",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_student_can_generate_own_study_routine(self):
        student = self.client_as("aluno", student_id=3)

        page = student.get("/rotina")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Rotina de Estudos SERE".encode("utf-8"), page.data)

        response = student.post(
            "/rotina",
            data={
                "csrf_token": "test-token",
                "student_id": "3",
                "minutes_per_day": "60",
                "preferred_time": "Noite",
                "focus_note": "Tenho mais energia depois da aula.",
                "generate": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        updated = student.get("/rotina")
        self.assertIn(b"60 min", updated.data)
        self.assertIn("Tenho mais energia".encode("utf-8"), updated.data)

    def test_student_routine_ignores_other_student_query(self):
        student = self.client_as("aluno", student_id=3)

        self.assertEqual(student.get("/rotina?student_id=1").status_code, 200)

    def test_manager_can_generate_student_routine(self):
        professor = self.client_as("professor")

        response = professor.post(
            "/rotina",
            data={
                "csrf_token": "test-token",
                "student_id": "3",
                "minutes_per_day": "50",
                "preferred_time": "Tarde",
                "focus_note": "Plano definido pelo professor.",
                "generate": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        page = professor.get("/rotina?student_id=3")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Plano definido pelo professor".encode("utf-8"), page.data)

    def test_csrf_is_required_for_post(self):
        client = self.client_as("aluno", student_id=3)

        response = client.post("/rotina", data={"student_id": "3", "minutes_per_day": "60"})

        self.assertEqual(response.status_code, 403)

    def test_challenge_area_is_removed_from_product(self):
        client = self.client_as("aluno", student_id=3)

        for path in ["/x1", "/x1/history", "/x1/ia", "/x1/arena/1"]:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 404)

        page = client.get("/dashboard")
        self.assertNotIn(b"/x1", page.data)
        self.assertNotIn("Desafios".encode("utf-8"), page.data)

if __name__ == "__main__":
    unittest.main()


