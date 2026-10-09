import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from langchain_core.messages import HumanMessage


def _parse_json(text: str):
    clean = text.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for i, char in enumerate(clean):
        if char in ("{", "["):
            try:
                obj, _ = decoder.raw_decode(clean, i)
                return obj
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("No valid JSON found", clean, 0)


def _latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in (value or ""))


def _latex_escape_url(url: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in (url or ""))


def _latex_href(url: str, label: str) -> str:
    return rf"\href{{{_latex_escape_url(url)}}}{{{_latex_escape(label)}}}"


def _compact(items, limit):
    return [item for item in items if item][:limit]


class ResumeRenderer:
    def __init__(self, llm, template_path: Path):
        self.llm = llm
        self.template_path = template_path

    @staticmethod
    def document_to_parsed_resume(document: dict) -> dict:
        skills = []
        for group in document.get("skill_groups", []):
            skills.extend(group.get("items", []))

        education = [
            {
                "degree": edu.get("degree", ""),
                "institution": edu.get("institution", ""),
                "year": edu.get("date", ""),
            }
            for edu in document.get("education", [])
        ]

        summary = " ".join(
            item.get("text", "") for item in document.get("achievements", [])
        )

        return {
            "skills": skills,
            "experience": document.get("experience", []),
            "education": education,
            "summary": summary,
        }

    def _load_latex_template(self) -> str:
        return self.template_path.read_text(encoding="utf-8")

    def build_resume_document(
        self,
        resume_text: str,
        source_resume_text: str,
        template_kind: str,
    ) -> dict:
        word_count = len(source_resume_text.split())
        page_instruction = (
            "Optimize for a clean two-page layout."
            if word_count > 600
            else "Optimize for one page only."
        )

        prompt = f"""
Convert the resume below into structured JSON for a LaTeX resume.
Return ONLY valid JSON. Do not include markdown fences.

TEMPLATE FAMILY: {template_kind}

JSON FORMAT:
{{
  "name": "Candidate name",
  "phone": "phone or empty string",
  "email": "email or empty string",
  "linkedin": "linkedin url or empty string",
  "github": "github url or empty string",
  "education": [
    {{
      "institution": "Institute",
      "date": "Aug 2023 -- May 2027",
      "degree": "Degree",
      "detail": "CGPA or equivalent"
    }}
  ],
  "experience": [
    {{
      "title": "Job Title",
      "company": "Company Name",
      "date": "Jun 2023 -- Aug 2024",
      "bullets": ["bullet 1", "bullet 2"]
    }}
  ],
  "skill_groups": [
    {{
      "label": "AI / GenAI",
      "items": ["LLMs", "RAG", "Prompt Engineering"]
    }}
  ],
  "projects": [
    {{
      "url": "https://...",
      "title": "Project title",
      "date": "2026",
      "stack": "Python | LangChain | ...",
      "bullets": ["bullet", "bullet"]
    }}
  ],
  "achievements": [
    {{
      "text": "achievement line"
    }}
  ]
}}

RULES:
- Preserve truth from the resume. Never fabricate missing facts.
- If a field is unknown, use an empty string or empty list.
- {page_instruction}
- Include ALL work experience roles from the source resume. Do not drop any employer.
- Keep at most 3 education items, 3 experience entries with up to 4 bullets each,
  6 skill groups, 5 projects with up to 4 bullets each, and 6 achievements.
- If the source resume has NO work experience at all, return an empty "experience" array.
- Bullets must be concise, ATS-strong, and quantifiable when the source supports that.
- Reflect the target JD keywords naturally, but never invent experience.

REWRITTEN RESUME:
{resume_text}

ORIGINAL SOURCE RESUME:
{source_resume_text[:15000]}
"""

        response = self.llm.invoke([HumanMessage(content=prompt)])

        try:
            document = _parse_json(response.content)
            if isinstance(document, dict):
                document["education"] = _compact(document.get("education", []), 3)
                document["experience"] = _compact(document.get("experience", []), 3)
                for exp in document["experience"]:
                    exp["bullets"] = _compact(exp.get("bullets", []), 4)
                document["skill_groups"] = _compact(document.get("skill_groups", []), 6)
                document["projects"] = _compact(document.get("projects", []), 5)
                for project in document["projects"]:
                    project["bullets"] = _compact(project.get("bullets", []), 4)
                document["achievements"] = _compact(document.get("achievements", []), 6)
                return document
        except Exception:
            pass

        return {
            "name": "Candidate Resume",
            "phone": "",
            "email": "",
            "linkedin": "",
            "github": "",
            "education": [],
            "experience": [],
            "skill_groups": [],
            "projects": [],
            "achievements": [],
        }

    def build_resume_latex(self, document: dict) -> str:
        template = self._load_latex_template()
        name = _latex_escape(document.get("name") or "Candidate Resume")
        phone = document.get("phone") or ""
        email = document.get("email") or ""
        linkedin = document.get("linkedin") or ""
        github = document.get("github") or ""

        header_parts = []
        if phone:
            header_parts.append(_latex_href(f"tel:{phone}", phone))
        if email:
            header_parts.append(_latex_href(f"mailto:{email}", email))
        if linkedin:
            label = linkedin.replace("https://", "").replace("http://", "")
            header_parts.append(
                _latex_href(linkedin, label)
            )
        if github:
            label = github.replace("https://", "").replace("http://", "")
            header_parts.append(
                _latex_href(github, label)
            )

        header_line = r"\enspace\textbar\enspace".join(header_parts)

        education_block = ""
        if document.get("education"):
            edu = document["education"][0]
            education_block = rf"""\section{{Education}}
\resumeSubheading
  {{{_latex_escape(edu.get("institution", ""))}}}{{{_latex_escape(edu.get("date", ""))}}}
  {{{_latex_escape(edu.get("degree", ""))}}}{{{_latex_escape(edu.get("detail", ""))}}}

\vspace{{2pt}}
"""

        skill_rows = []
        for group in document.get("skill_groups", []):
            label = _latex_escape(group.get("label", "Skills"))
            items = _latex_escape(", ".join(group.get("items", [])))
            if items:
                skill_rows.append(rf"  \small\textbf{{{label}}} & \small {items} \\[2pt]")

        skills_block = ""
        if skill_rows:
            final_rows = skill_rows[:-1]
            final_rows.append(skill_rows[-1].replace(r"\\[2pt]", r"\\"))
            skills_block = "\\section{Technical Skills}\n\\begin{tabular*}{\\textwidth}{@{}l@{\\hskip 6pt}l}\n"
            skills_block += "\n".join(final_rows)
            skills_block += "\n\\end{tabular*}\n\n\\vspace{2pt}\n"

        project_chunks = []
        for project in document.get("projects", []):
            bullets = "\n".join(
                rf"  \resumeItem{{{_latex_escape(bullet)}}}"
                for bullet in project.get("bullets", [])
                if bullet
            )
            project_chunks.append(rf"""\projectHeading
  {{{_latex_escape_url(project.get("url", ""))}}}
  {{{_latex_escape(project.get("title", ""))}}}
  {{{_latex_escape(project.get("date", ""))}}}
\projectStack{{{_latex_escape(project.get("stack", ""))}}}
\begin{{itemize}}
{bullets}
\end{{itemize}}

\vspace{{2pt}}
""")

        projects_block = ""
        if project_chunks:
            projects_block = "\\section{Projects}\n\n" + "\n".join(project_chunks)

        achievement_items = "\n".join(
            rf"  \resumeItem{{{_latex_escape(item.get('text', ''))}}}"
            for item in document.get("achievements", [])
            if item.get("text")
        )
        achievements_block = ""
        if achievement_items:
            achievements_block = rf"""\section{{Achievements \& Certifications}}
\begin{{itemize}}
{achievement_items}
\end{{itemize}}
"""

        experience_chunks = []
        for exp in document.get("experience", []):
            bullets = "\n".join(
                rf"  \resumeItem{{{_latex_escape(bullet)}}}"
                for bullet in exp.get("bullets", [])
                if bullet
            )
            experience_chunks.append(rf"""\resumeSubheading
  {{{_latex_escape(exp.get("company", ""))}}}{{{_latex_escape(exp.get("date", ""))}}}
  {{{_latex_escape(exp.get("title", ""))}}}{{{{}}}}\begin{{itemize}}
{bullets}
\end{{itemize}}
""")

        experience_block = ""
        if experience_chunks:
            experience_block = "\\section{Experience}\n\n" + "\n".join(experience_chunks)

        return (
            template
            .replace("<<NAME>>", name)
            .replace("<<HEADER_LINE>>", header_line or r"\mbox{}")
            .replace("<<EDUCATION_BLOCK>>", education_block or "% No education data")
            .replace("<<EXPERIENCE_BLOCK>>", experience_block or "% No experience data")
            .replace("<<SKILLS_BLOCK>>", skills_block or "% No skills data")
            .replace("<<PROJECTS_BLOCK>>", projects_block or "% No projects data")
            .replace("<<ACHIEVEMENTS_BLOCK>>", achievements_block or "% No achievements data")
        )

    @staticmethod
    def compile_latex_to_pdf(latex_source: str) -> bytes:
        engine = next(
            (candidate for candidate in ("pdflatex", "xelatex", "lualatex", "tectonic") if shutil.which(candidate)),
            None,
        )

        if not engine:
            raise RuntimeError("No LaTeX compiler installed on the worker.")

        with tempfile.TemporaryDirectory(prefix="resume-tex-") as temp_dir:
            temp_path = Path(temp_dir)
            tex_path = temp_path / "resume.tex"
            tex_path.write_text(latex_source, encoding="utf-8")
            tex_env = os.environ.copy()
            tex_env["TEXMFVAR"] = str(temp_path / ".texmf-var")
            tex_env["TEXMFCONFIG"] = str(temp_path / ".texmf-config")
            tex_env["TEXMFHOME"] = str(temp_path / ".texmf-home")

            if engine == "tectonic":
                command = [engine, "--keep-logs", "--outdir", str(temp_path), str(tex_path)]
            else:
                command = [
                    engine,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    "-output-directory",
                    str(temp_path),
                    str(tex_path),
                ]

            result = subprocess.run(
                command,
                cwd=temp_path,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
                env=tex_env,
            )

            pdf_path = temp_path / "resume.pdf"
            if result.returncode != 0 or not pdf_path.exists():
                stderr = (result.stderr or result.stdout or "").strip()
                raise RuntimeError(stderr[-1200:] or "LaTeX compilation failed.")

            return pdf_path.read_bytes()
