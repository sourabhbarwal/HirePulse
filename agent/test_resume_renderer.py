import unittest
from pathlib import Path

from services.resume_renderer import ResumeRenderer, _latex_escape_url


class ResumeRendererTests(unittest.TestCase):
    def setUp(self):
        self.renderer = ResumeRenderer(
            llm=None,
            template_path=Path(__file__).resolve().parent / "templates" / "shivansh_one_page_resume.tex",
        )

    def test_latex_url_escape_handles_common_query_chars(self):
        url = "https://linkedin.com/in/dev_name?trk=public_profile&locale=en_US#about"

        escaped = _latex_escape_url(url)

        self.assertIn(r"dev\_name", escaped)
        self.assertIn(r"?trk=public\_profile\&locale=en\_US", escaped)
        self.assertIn(r"\#about", escaped)

    def test_header_hrefs_escape_url_and_display_text_separately(self):
        latex = self.renderer.build_resume_latex(
            {
                "name": "Dev User",
                "phone": "+1 555_0100",
                "email": "dev+test@example.com",
                "linkedin": "https://linkedin.com/in/dev_name?trk=public_profile&locale=en_US#about",
                "github": "https://github.com/dev_user/repo?tab=readme&sort=stars",
                "education": [],
                "experience": [],
                "skill_groups": [],
                "projects": [],
                "achievements": [],
            }
        )

        self.assertIn(r"\href{tel:+1 555\_0100}{+1 555\_0100}", latex)
        self.assertIn(r"\href{mailto:dev+test@example.com}{dev+test@example.com}", latex)
        self.assertIn(
            r"\href{https://linkedin.com/in/dev\_name?trk=public\_profile\&locale=en\_US\#about}",
            latex,
        )
        self.assertIn(r"linkedin.com/in/dev\_name?trk=public\_profile\&locale=en\_US\#about", latex)
        self.assertIn(
            r"\href{https://github.com/dev\_user/repo?tab=readme\&sort=stars}",
            latex,
        )


if __name__ == "__main__":
    unittest.main()
