import argparse
from pathlib import Path

from app.extraction.candidate_schema import CandidateProfile, Education, Language, WorkExperience
from app.generation.docx_renderer import render_docx
from app.generation.pdf_exporter import export_docx_to_pdf
from app.generation.target_format_blueprint import get_target_format_blueprint
from app.generation.template_mapper import build_client_render_context


DEFAULT_OUTPUT_DIR = Path("data/generated_outputs/backend_mvp_template_smoke")
TEMPLATE_ID = "client_10554236_v1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render short, typical, and long synthetic profiles for backend template QA."
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    blueprint = get_target_format_blueprint(TEMPLATE_ID)

    for fixture_name, profile in _synthetic_profiles().items():
        fixture_dir = output_dir / fixture_name
        fixture_dir.mkdir(parents=True, exist_ok=True)
        context = build_client_render_context(profile, template_name=TEMPLATE_ID)
        docx_path = render_docx(
            context,
            fixture_dir / "candidate_profile.docx",
            blueprint=blueprint,
        )
        pdf_path = export_docx_to_pdf(docx_path, output_dir=fixture_dir)
        print(f"{fixture_name}: {docx_path} -> {pdf_path}")


def _synthetic_profiles() -> dict[str, CandidateProfile]:
    short = CandidateProfile(
        full_name="Synthetic Candidate Short",
        professional_summary="Accountant experienced in monthly reporting.",
        skills=["Financial reporting", "Reconciliations"],
        work_experience=[
            WorkExperience(
                company="Example Company",
                title="Accountant",
                start_date="2022",
                end_date="Present",
                description=["Prepared monthly management reports."],
            )
        ],
    )

    typical = CandidateProfile(
        full_name="Synthetic Candidate Typical",
        location="Example City",
        professional_summary=(
            "Financial accountant with experience in reporting, balance-sheet reconciliations, "
            "audit support, and process improvement across regional operations."
        ),
        skills=[
            "Financial reporting",
            "Account reconciliations",
            "Variance analysis",
            "Audit support",
            "ERP systems",
            "Advanced Excel",
        ],
        languages=[Language(name="English", proficiency="Professional")],
        work_experience=[
            WorkExperience(
                company="Example Manufacturing Group",
                title="Senior Financial Accountant",
                location="Example City",
                start_date="January 2021",
                end_date="Present",
                description=[
                    "Prepared monthly financial statements and management reporting packs.",
                    "Reviewed balance-sheet reconciliations and resolved ledger differences.",
                    "Supported annual audits and improved month-end close controls.",
                ],
            ),
            WorkExperience(
                company="Example Services Limited",
                title="Financial Accountant",
                start_date="July 2017",
                end_date="December 2020",
                description=[
                    "Maintained general-ledger schedules and analyzed operating variances.",
                    "Partnered with operations teams to improve cost-center reporting accuracy.",
                ],
            ),
        ],
        education=[
            Education(
                institution="Example University",
                degree="Bachelor of Commerce",
                field_of_study="Accounting",
                end_date="2017",
            )
        ],
    )

    long_entries = [
        WorkExperience(
            company=f"Synthetic Regional Organization {index}",
            title=(
                "Senior Financial Reporting and Business Process Improvement Specialist"
                if index == 1
                else "Financial Accounting Manager"
            ),
            location="Example Metropolitan Region",
            start_date=f"January {2010 + index * 2}",
            end_date="Present" if index == 5 else f"December {2011 + index * 2}",
            description=[
                "Led the preparation and review of monthly reporting packages for multiple operating entities.",
                "Investigated complex reconciliation differences and coordinated corrective actions with stakeholders.",
                "Documented controls, supported external audit requests, and trained colleagues on standardized processes.",
                "Developed detailed variance commentary for leadership while maintaining accurate supporting schedules.",
            ],
        )
        for index in range(1, 6)
    ]
    long = CandidateProfile(
        full_name="Synthetic Candidate Long",
        location="Example Metropolitan Region",
        professional_summary=(
            "Experienced accounting leader with a record of managing complex close cycles, statutory reporting, "
            "audit preparation, financial controls, systems improvements, and cross-functional initiatives across "
            "multiple entities and jurisdictions."
        ),
        skills=[
            "Statutory reporting",
            "Management reporting",
            "Consolidations",
            "Balance-sheet governance",
            "Process improvement",
            "Audit coordination",
            "Financial controls",
            "ERP implementation",
            "Stakeholder management",
            "Advanced financial modeling",
        ],
        languages=[
            Language(name="English", proficiency="Professional"),
            Language(name="Spanish", proficiency="Intermediate"),
        ],
        work_experience=long_entries,
        education=[
            Education(
                institution="Example International University",
                degree="Master of Professional Accounting",
                field_of_study="Accounting and Finance",
                end_date="2011",
            ),
            Education(
                institution="Example State University",
                degree="Bachelor of Business",
                field_of_study="Accounting",
                end_date="2009",
            ),
        ],
    )

    return {"short": short, "typical": typical, "long": long}


if __name__ == "__main__":
    main()
