import unittest
from unittest.mock import patch

import main


class JobMatchingTests(unittest.TestCase):
    def test_experience_phrases(self):
        cases = [
            ("1.5-2 years", (1.5, 2)),
            ("2 years of experience", (2, 12)),
            ("1-3 years", (1, 3)),
            ("minimum 1 year", (1, 11)),
            ("1.4+ yrs", (1.4, 11.4)),
            ("0 to 2 years", (0, 2)),
            ("No experience stated", None),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(main.extract_experience_range(text), expected)

    def test_experience_filter(self):
        jobs = [{"description": text} for text in [
            "1-3 years", "2 years", "1.5-2 years", "No experience stated",
        ]]
        self.assertEqual(main.filter_jobs_by_experience(jobs, 1.4), [jobs[0], jobs[3]])
        self.assertEqual(main.filter_jobs_by_experience(jobs, 1.4, False), [jobs[0]])

    def test_skill_aliases(self):
        for variant in ["dotnet", "ASP.NET Core", ".NET", "Dot Net"]:
            with self.subTest(variant=variant):
                self.assertTrue(main.skill_found_in_text(".NET", variant))
        self.assertTrue(main.skill_found_in_text("Entity Framework", "EF Core"))
        self.assertFalse(main.skill_found_in_text(".NET", "networking"))
        self.assertFalse(main.skill_found_in_text("SQL", "NoSQL"))

    def test_only_resume_skills_drive_matching(self):
        config = {"required_skills": [".NET", "C#", "Angular"], "use_resume_skills": True}
        self.assertEqual(main.get_matching_skills(config, "dotnet C#"), [".NET", "C#"])
        for text in ["", "Unrecognized skills"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                main.get_matching_skills(config, text)
        config["use_resume_skills"] = False
        self.assertEqual(main.get_matching_skills(config, ""), config["required_skills"])

    def test_missing_skills_comparison(self):
        job = {"description": "dotnet C# Angular EF Core"}
        self.assertEqual(main.compute_changes_needed(
            job, ".NET C# Entity Framework", [".NET", "C#", "Angular", "Entity Framework"],
        ), "Angular")

    def test_main_filters_before_reports_and_seen_tracking(self):
        config = {
            "required_skills": [".NET", "C#", "SQL"], "core_skills": [".NET"],
            "use_resume_skills": True, "match_mode": "min_count", "min_skill_matches": 2,
            "experience_filter": {"years": 1.4, "include_unspecified": True},
            "resume_path": "resume/resume.pdf", "output_filename": "test.xlsx",
            "job_focus": ".NET Developer",
        }
        jobs = [
            {"title": title, "description": description, "company": "Example",
             "location": "Bangalore", "url": title}
            for title, description in [
                ("junior", "dotnet C# SQL; 1-2 years"),
                ("senior", ".NET SQL; 3+ years"),
                ("unknown", "ASP.NET Core SQL Server"),
                ("other", "Python SQL; 1 year"),
            ]
        ]
        env = {name: "test" for name in [
            "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "EMAIL_ADDRESS", "EMAIL_APP_PASSWORD", "EMAIL_TO",
        ]}
        with (
            patch.dict(main.os.environ, env),
            patch.object(main, "load_yaml", side_effect=[config, {"companies": []}]),
            patch.object(main, "extract_resume_text", return_value=".NET C# SQL"),
            patch.object(main, "collect_adzuna_jobs", return_value=jobs),
            patch.object(main, "fetch_all_companies", return_value=([], [])),
            patch.object(main, "load_seen_jobs", return_value={"unknown": "2026-10-01"}),
            patch.object(main, "save_seen_jobs") as save_seen,
            patch.object(main, "save_excel") as save_excel,
            patch.object(main, "send_email") as send_email,
            patch("builtins.print"),
        ):
            main.main()
            sheets = save_excel.call_args.args[0]
            self.assertEqual(list(sheets["All Jobs"]["Job Title"]), ["junior", "unknown"])
            self.assertEqual(list(sheets["New Jobs"]["Job Title"]), ["junior"])
            self.assertEqual(list(sheets["Old Jobs (Repeated)"]["Job Title"]), ["unknown"])
            self.assertEqual(list(sheets["1.4 Years Experience"]["Job Title"]), ["junior"])
            self.assertEqual(set(save_seen.call_args.args[0]), {"junior", "unknown"})
            self.assertEqual(send_email.call_args.args[-2:], (".NET Developer", "1.4 Years Experience"))


if __name__ == "__main__":
    unittest.main()