"""Frozen public/fictional resume inputs for the canonical experiment only."""
from app.extraction.candidate_schema import CandidateProfile
from app.pipeline_e.resume_layout.candidates import CandidateDocument, CandidateLeaf, CandidateSection, _leaf, _unroutable

FROZEN_C1_RUNS: dict[str, str] = {
    "D_E": "c_pipeline_D_to_E_20260910T200018Z",  # owner-accepted D→E, regression re-run
    "E_F": "c_pipeline_D_to_E_20260910T195515Z",  # E→F matrix green run
    "E_D": "c1_matrix_ED_B_20260911T044203Z",  # E→D under the Option-B contract
    # C2-0d generalization audit: the frozen C1 runs for the remaining three
    # directed pairs (owner-accepted matrix finals; local artifact copies).
    "D_F": "c1_matrix_DF_B",  # D→F matrix green run
    "F_D": "c1_matrix_FD_B_20260911T061127Z",  # F→D matrix green run
    "F_E": "c1_matrix_FE2_20260910T200958Z",  # F→E matrix green run
}


C2_0B_PAIRS: dict[str, dict[str, str]] = {
    "D_E": {"candidate": "D", "target": "E", "role": "primary"},
    "E_F": {"candidate": "E", "target": "F", "role": "generalization"},
    "E_D": {"candidate": "E", "target": "D", "role": "gap_only"},
    # C2-0d: the remaining three directed pairs of the authorized D/E/F corpus.
    "D_F": {"candidate": "D", "target": "F", "role": "generalization"},
    "F_D": {"candidate": "F", "target": "D", "role": "generalization_grid_target"},
    "F_E": {"candidate": "F", "target": "E", "role": "generalization"},
}


def candidate_resume_D() -> CandidateDocument:
    """Resume D verbatim (frozen C1 run ``c_pipeline_D_to_E_20260910T200018Z``)."""
    no_header_home = (
        "no measured header row in the target state carries this content; "
        "C1 rendered it in a derived extension row, which layout-state/1 has "
        "no concept for"
    )
    leaves: list[CandidateLeaf] = [
        _leaf("header.name", "header_field", slot="name", text="J. Doe"),
        _leaf("header.linkedin", "header_field", slot="linkedin", text="linkedin.com/in/USER"),
        _leaf("header.github", "header_field", slot="github", text="github.com/USER"),
        _leaf("summary.p1", "summary_paragraph", "summary",
              text="SUMMARY — This is an overly-packed and busy example showing what the template is capable of."),
    ]
    highlight_items = [
        "– Managed business for a major business, ensuring business continuity",
        "– Developed business plans that resolved 80% of business issues",
        "– Led a team of business professionals to achieve business goals",
        "– Implemented business strategies that increased revenue by 20%",
        "– Ran negotiations that saved over $500,000 in annual costs",
        "– Drove integrations with third-party platforms for ecosystem growth",
        "– Spearheaded business initiatives that improved operational efficiency",
        "– Coordinated cross-functional teams to deliver projects on time and within budget",
    ]
    leaves += [
        _leaf(f"highlights.i{i}", "additional_item", "additional_details", text=text)
        for i, text in enumerate(highlight_items, 1)
    ]
    skill_lines = [
        "Management People, Systems, Operations, Projects",
        "Business Analysis, Strategy, Development, Planning",
        "Finance Budgeting, Forecasting, Reporting",
        "Sales B2B, B2C, Lead Generation, CRM",
        "Marketing Research, Campaigns, Social Media, SEO",
        "Software OpenOffice, CRM, ERP, Data Analysis",
    ]
    leaves += [
        _leaf(f"skills.pool.{i}", "skill_group", "skills", text=text)
        for i, text in enumerate(skill_lines, 1)
    ]
    key_skill_lines = [
        "– Development: Many Deep Dives Into Business Processes and Systems",
        "– Databases: Managed Customer Relationship Management (CRM)",
        "→ Storage: Oversaw Document Management Systems for Business Records",
        "→ Communication: Led Business Meetings and Negotiations with Stakeholders",
        "⌣ Sales: Implemented Sales Strategies to Increase Revenue and Market Share",
        "⌣ Management: Directed Teams and Projects to Achieve Business Objectives",
    ]
    leaves += [
        _leaf(f"skills.key.{i}", "skill", "skills", text=text)
        for i, text in enumerate(key_skill_lines, 1)
    ]
    education_lines = [
        "Neat-O University:",
        "– Bachelor of Science in Business Management",
        "Minors: 1. Marketing, 2. Finance",
        "Certifications:",
        "– BMP Business Management Professional",
        "– CP Certified Professional",
    ]
    leaves.append(_leaf("education.e1", "education_entry", "education", text=education_lines[0]))
    leaves += [
        _leaf(f"education.e1.d{i}", "entry_detail", "education",
              parent="education.e1", text=text)
        for i, text in enumerate(education_lines[1:], 1)
    ]
    work = [
        ("Power Business Ink", "Senior Business Engineer"),
        ("Consulting Corp", "Senior Business Consultant"),
        ("HealthCo Industries", "Junior Business Manager"),
        ("Aura Systems", "Product Strategy Lead"),
        ("Fuzion Labs", "Business Operations Manager"),
    ]
    modes = ["on-site", "hybrid", "on-site", "hybrid", "on-site"]
    dates = ["| 2024 – Present", "| 2017 – 2024", "| 2005 – 2016", "| 1999 – 2005", "| 1980 – 1999"]
    for index, (company, role) in enumerate(work, 1):
        leaves.append(_leaf(f"work.e{index}", "work_entry", "work_experience", text=company))
        leaves.append(_leaf(f"work.e{index}.role", "entry_detail", "work_experience",
                            parent=f"work.e{index}", text=role))
    for index, mode in enumerate(modes, 1):  # column-blocked source order (D stores a table)
        leaves.append(_leaf(f"work.m{index}", "entry_meta", "work_experience",
                            parent=f"work.e{index}", text=mode))
    for index, date in enumerate(dates, 1):
        leaves.append(_leaf(f"work.d{index}", "entry_meta", "work_experience",
                            parent=f"work.e{index}", text=date))
    leaves += [
        _leaf("volunteer.i1", "additional_item", "additional_details",
              text="Business Mentors – Mentor"),
        _leaf("volunteer.i2", "additional_item", "additional_details",
              text="Angel Investors – Financial Advisor"),
    ]
    another_items = [
        "– More text to represent an area where text could be placed",
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed",
        "2014 – Present",
        "2005 – 2015",
    ]
    leaves += [
        _leaf(f"another.i{i}", "additional_item", "additional_details", text=text)
        for i, text in enumerate(another_items, 1)
    ]
    return CandidateDocument(
        candidate_id="resume-d",
        leaves=leaves,
        unroutable=[
            _unroutable(
                " [redacted - web copy] — # [redacted - web copy] —",
                "redacted web-copy contact line: no measured header slot carries it "
                "and the accepted C1 D→E baseline renders no value for it; "
                "EXPLICITLY OMITTED under the approved reviewed-omission "
                "disposition (C2-0b corrective pass) — not rendered, not covered",
                before="header.linkedin",
                disposition="omit",
            ),
            _unroutable(
                "Senior Business Person", no_header_home,
                before="summary.p1", slot="title",
            ),
            _unroutable(
                "Business | Hobbies | Awesomeness", no_header_home,
                before="summary.p1", slot="tagline",
            ),
        ],
        sections=[
            CandidateSection(section_id="summary", heading=None, source="summary",
                             content_kind="paragraph", leaf_ids=["summary.p1"]),
            CandidateSection(section_id="highlights", heading="HIGHLIGHTS", source="additional_details",
                             content_kind="item_list",
                             leaf_ids=[f"highlights.i{i}" for i in range(1, 9)]),
            CandidateSection(section_id="skills_pool", heading="SKILLS POOL", source="skills",
                             content_kind="item_list",
                             leaf_ids=[f"skills.pool.{i}" for i in range(1, 7)]),
            CandidateSection(section_id="key_skills", heading="KEY SKILLS", source="skills",
                             content_kind="item_list",
                             leaf_ids=[f"skills.key.{i}" for i in range(1, 7)]),
            CandidateSection(section_id="education_certs", heading="EDUCATION & CERTIFICATIONS",
                             source="education", content_kind="entries",
                             leaf_ids=["education.e1"] + [f"education.e1.d{i}" for i in range(1, 6)]),
            CandidateSection(section_id="work", heading="WORK EXPERIENCE", source="work_experience",
                             content_kind="entries",
                             leaf_ids=[leaf_id
                                       for index in range(1, 6)
                                       for leaf_id in (f"work.e{index}", f"work.e{index}.role")]
                             + [f"work.m{i}" for i in range(1, 6)]
                             + [f"work.d{i}" for i in range(1, 6)]),
            CandidateSection(section_id="volunteer", heading="VOLUNTEER EXPERIENCE",
                             source="additional_details", content_kind="item_list",
                             leaf_ids=["volunteer.i1", "volunteer.i2"]),
            CandidateSection(section_id="another", heading="ANOTHER SECTION",
                             source="additional_details", content_kind="item_list",
                             leaf_ids=[f"another.i{i}" for i in range(1, 5)]),
        ],
    )


def candidate_resume_E() -> CandidateDocument:
    """Resume E verbatim (frozen C1 run ``c_pipeline_D_to_E_20260910T195515Z``)."""
    no_location_row = (
        "the pair's target state compiles no header location row; C1 rendered "
        "the candidate location in a derived extension row, which "
        "layout-state/1 has no concept for"
    )
    leaves: list[CandidateLeaf] = [
        _leaf("header.name", "header_field", slot="name", text="Daniel Phang"),
        _leaf("header.phone", "header_field", slot="phone", text="(000) 000-0000"),
        _leaf("header.envelope", "header_field", slot="envelope", text="example@example.com"),
        _leaf("header.github", "header_field", slot="github", text="github.com/example-profile"),
        _leaf("header.linkedin", "header_field", slot="linkedin", text="linkedin.com/in/example"),
    ]

    def entry(index: int, title: str, role: str | None, metas: list[str], bullets: list[str]) -> None:
        leaves.append(_leaf(f"work.e{index}", "work_entry", "work_experience", text=title))
        if role is not None:
            leaves.append(_leaf(f"work.e{index}.role", "entry_detail", "work_experience",
                                parent=f"work.e{index}", text=role))
        for meta_index, meta in enumerate(metas, 1):
            leaves.append(_leaf(f"work.e{index}.m{meta_index}", "entry_meta", "work_experience",
                                parent=f"work.e{index}", text=meta))
        for bullet_index, bullet in enumerate(bullets, 1):
            leaves.append(_leaf(f"work.e{index}.b{bullet_index}", "work_bullet", "work_experience",
                                parent=f"work.e{index}", text=bullet))

    entry(1, "Microsoft", "Software Engineer II", ["Redmond, WA", "April 2019 – Present"], [
        "• Analyzed performance data and optimized legacy backend code for SharePoint Classic Publishing sites, "
        "improving query caching and CPU-heavy operations such as HTML rewriting.",
        "• Designed and implemented quality-of-service dashboards and performance frameworks to dynamically "
        "detect performance issues across 400+ top companies.",
        "• Improved data processing scripts that analyze daily site performance data for 1000+ companies, "
        "performance incidents, and engineering system health.",
    ])
    entry(2, "Amazon.com", "Software Development Engineer II", ["Seattle, WA", "October 2016 – January 2019"], [
        "• Designed and implemented ordering and accounting workflows to launch Prime Wardrobe US/UK/JP, a "
        "try-before-you-buy program for clothing, jewelry, and shoes.",
        "• Reduced the US Prime Wardrobe non-payment rate significantly by implementing additional validations "
        "based on customer behavior patterns.",
        "• Optimized Prime Wardrobe’s Redshift cluster by intelligently distributing workloads, reducing peak "
        "CPU usage from 95% to 50% and peak disk usage from 90% to 60%.",
        "• Migrated Prime Wardrobe’s accounting backend to a next-generation plugin-based service, allowing for "
        "easy future integration with other retail programs.",
    ])
    entry(3, "Software Development Engineer I", None, ["June 2014 – October 2016"], [
        "• Implemented critical detail page and globalized item publishing features to help launch the Rest of "
        "World project, which enabled customers from 200+ countries to purchase digital software and video games.",
        "• Designed and implemented an automated accounting solution for the Digital Software & Video Games "
        "business, reducing the work required in monthly accounting close from 10+ hours to 2 hours.",
        "• Created an internal Django website for vendor managers to manage pricing, blacklisting, and inventory "
        "for software and video games, reducing monthly operational time spent from 20+ hours to 10 hours.",
    ])
    entry(4, "Crunchyroll", "Engineering Intern", ["San Francisco, CA", "June 2013 – August 2013"], [
        "• Developed a new version of Crunchyroll’s application for the Roku platform.",
        "• Worked with a designer to revamp the application’s user interface, improved HD video playback, and "
        "implemented a multilingual translations framework.",
    ])
    leaves += [
        _leaf("skills.languages", "skill_group", "skills",
              text="Languages: C#, HTML/CSS, Java, JavaScript, LATEX, Python, SQL"),
        _leaf("skills.software", "skill_group", "skills",
              text="Software: Atlassian (Bitbucket, Jira, Confluence), AWS (DynamoDB, EC2, Lambda, RDS, Redshift, "
                   "S3, SQS), Microsoft (Azure DevOps, Visual Studio), DigitalOcean, Django, Heroku, IntelliJ IDEA, Selenium"),
    ]
    leaves.append(_leaf("education.e1", "education_entry", "education", text="Lehigh University"))
    leaves += [
        _leaf("education.e1.d1", "entry_detail", "education", parent="education.e1",
              text="M.S. Computer Science (GPA: 3.96/4.00)"),
        _leaf("education.e1.d2", "entry_detail", "education", parent="education.e1",
              text="B.S. Computer Engineering (Minor in Economics) (GPA: 3.77/4.00)"),
        _leaf("education.e1.m1", "entry_meta", "education", parent="education.e1", text="Bethlehem, PA"),
        CandidateLeaf(leaf_id="education.e1.m2", kind="entry_meta", source="education",
                      parent_leaf_id="education.e1", paired_leaf_id="education.e1.d1",
                      text="August 2013 – May 2014"),
        CandidateLeaf(leaf_id="education.e1.m3", kind="entry_meta", source="education",
                      parent_leaf_id="education.e1", paired_leaf_id="education.e1.d2",
                      text="August 2009 – May 2013"),
    ]
    return CandidateDocument(
        candidate_id="resume-e",
        profile=CandidateProfile(full_name="Daniel Phang", given_name="Daniel", family_name="Phang"),
        leaves=leaves,
        unroutable=[
            _unroutable(
                "Seattle, Washington", no_location_row,
                before="header.envelope", slot="location",
            )
        ],
        sections=[
            CandidateSection(section_id="experience", heading="Experience", source="work_experience",
                             content_kind="entries",
                             leaf_ids=[leaf.leaf_id for leaf in leaves if leaf.source == "work_experience"]),
            CandidateSection(section_id="skills", heading="Skills", source="skills",
                             content_kind="item_list", leaf_ids=["skills.languages", "skills.software"]),
            CandidateSection(section_id="education", heading="Education", source="education",
                             content_kind="entries",
                             leaf_ids=["education.e1", "education.e1.d1", "education.e1.d2",
                                       "education.e1.m1", "education.e1.m2", "education.e1.m3"]),
        ],
    )


def candidate_resume_F() -> CandidateDocument:
    """Resume F verbatim (frozen C1 run ``c1_matrix_FE2_20260910T200958Z``).

    Author-assigned segmentation (same authorship rule as D/E — NOT an LLM
    extraction claim; C2-0d generalization audit input). Authoring note: the
    candidate's contact values are ONE verbatim source line, authored as a
    single header leaf on the ``phone`` slot — the coverage gate consumes one
    source line with one authored text (the documented 2-3 partition rule
    does not reach four items on one line), and layout-state/1 measures
    header rows, not per-field geometry (C2-0c limitation). The PROJECTS
    block is authored as ordered ``additional_item`` lines: the frozen role
    vocabulary has no projects role and the audit must not expand it.
    """
    leaves: list[CandidateLeaf] = [
        _leaf("header.name", "header_field", slot="name", text="Alex Webb"),
        _leaf(
            "header.contact", "header_field", slot="phone",
            text="555-123-4567 | alex@email.com | linkedin.com/in/alexwebbx | github.com/alexwebbx",
        ),
        _leaf(
            "summary.p1", "summary_paragraph", "summary",
            text="Passionate AI/ML engineer with a strong background in deep learning, computer vision, and natural language processing. "
                 "Skilled in Python, TensorFlow, PyTorch, and various ML libraries. Excellent problem-solving, research, and collaboration "
                 "abilities. Seeking a challenging role to develop cutting-edge AI solutions.",
        ),
    ]
    skills = [
        "Programming Languages: Python, C++, SQL, MATLAB",
        "Deep Learning Frameworks: TensorFlow, PyTorch, Keras, Caffe",
        "Libraries & Tools: NumPy, Pandas, Scikit-learn, OpenCV, NLTK, Git, Docker",
    ]
    leaves += [
        _leaf(f"skills.g{i}", "skill_group", "skills", text=text)
        for i, text in enumerate(skills, 1)
    ]
    project_lines = [
        "Image Captioning System",
        "Deep Learning Project",
        "Jan 2023 – Present",
        "Python, TensorFlow, OpenCV",
        "• Developed an end-to-end system for generating descriptive captions for images",
        "• Utilized CNN and LSTM models for image feature extraction and caption generation",
        "• Achieved state-of-the-art performance on the COCO dataset",
        "Sentiment Analysis API",
        "Natural Language Processing",
        "Aug 2022 – Dec 2022",
        "Python, Flask, NLTK, Hugging Face",
        "• Built a RESTful API for sentiment analysis of text data",
        "• Implemented pre-trained transformer models using Hugging Face",
        "• Deployed the API on a cloud platform for easy integration",
    ]
    leaves += [
        _leaf(f"projects.i{i}", "additional_item", "additional_details", text=text)
        for i, text in enumerate(project_lines, 1)
    ]

    def entry(index: int, title: str, detail: str, metas: list[str], bullets: list[str]) -> None:
        leaves.append(_leaf(f"work.e{index}", "work_entry", "work_experience", text=title))
        leaves.append(_leaf(f"work.e{index}.d1", "entry_detail", "work_experience",
                            parent=f"work.e{index}", text=detail))
        for meta_index, meta in enumerate(metas, 1):
            leaves.append(_leaf(f"work.e{index}.m{meta_index}", "entry_meta", "work_experience",
                                parent=f"work.e{index}", text=meta))
        for bullet_index, bullet in enumerate(bullets, 1):
            leaves.append(_leaf(f"work.e{index}.b{bullet_index}", "work_bullet", "work_experience",
                                parent=f"work.e{index}", text=bullet))

    entry(1, "AI Research Intern", "DeepMind", ["June 2022 – Aug 2022", "London, UK"], [
        "• Conducted research on reinforcement learning algorithms for robotics",
        "• Implemented and evaluated deep RL models using PyTorch and RLlib",
        "• Presented findings at weekly research meetings",
    ])
    entry(2, "Machine Learning Engineer", "Acme AI Solutions", ["Jan 2021 – May 2022", "San Francisco, CA"], [
        "• Developed and deployed machine learning models for various industries",
        "• Optimized model performance and ensured data quality",
        "• Collaborated with cross-functional teams to deliver AI solutions",
    ])
    education_entries = [
        ("Stanford University", "M.S. in Computer Science, Artificial Intelligence",
         ["Stanford, CA", "Aug 2019 – May 2021"]),
        ("University of California, Berkeley", "B.S. in Electrical Engineering and Computer Science",
         ["Berkeley, CA", "Aug 2015 – May 2019"]),
    ]
    for index, (school, degree, metas) in enumerate(education_entries, 1):
        leaves.append(_leaf(f"education.e{index}", "education_entry", "education", text=school))
        leaves.append(_leaf(f"education.e{index}.d1", "entry_detail", "education",
                            parent=f"education.e{index}", text=degree))
        for meta_index, meta in enumerate(metas, 1):
            leaves.append(_leaf(f"education.e{index}.m{meta_index}", "entry_meta", "education",
                                parent=f"education.e{index}", text=meta))
    certifications = [
        "• AWS Certified Machine Learning - Specialty",
        "• TensorFlow Developer Certificate",
    ]
    leaves += [
        _leaf(f"certifications.i{i}", "certification_item", "certifications", text=text)
        for i, text in enumerate(certifications, 1)
    ]
    work_ids = [leaf.leaf_id for leaf in leaves if leaf.source == "work_experience"]
    education_ids = [leaf.leaf_id for leaf in leaves if leaf.source == "education"]
    return CandidateDocument(
        candidate_id="resume-f",
        leaves=leaves,
        unroutable=[],
        sections=[
            CandidateSection(section_id="summary", heading="SUMMARY", source="summary",
                             content_kind="paragraph", leaf_ids=["summary.p1"]),
            CandidateSection(section_id="skills", heading="TECHNICAL SKILLS", source="skills",
                             content_kind="item_list",
                             leaf_ids=[f"skills.g{i}" for i in range(1, 4)]),
            CandidateSection(section_id="projects", heading="PROJECTS", source="additional_details",
                             content_kind="item_list",
                             leaf_ids=[f"projects.i{i}" for i in range(1, 15)]),
            CandidateSection(section_id="experience", heading="EXPERIENCE", source="work_experience",
                             content_kind="entries", leaf_ids=work_ids),
            CandidateSection(section_id="education", heading="EDUCATION", source="education",
                             content_kind="entries", leaf_ids=education_ids),
            CandidateSection(section_id="certifications", heading="CERTIFICATIONS", source="certifications",
                             content_kind="item_list",
                             leaf_ids=["certifications.i1", "certifications.i2"]),
        ],
    )


def candidate_document_for_pair(pair: str) -> CandidateDocument:
    """The frozen C1 candidate render context for one C2-0b evaluation pair."""
    if pair not in C2_0B_PAIRS:
        raise KeyError(f"unknown pair {pair!r}; known: {sorted(C2_0B_PAIRS)}")
    candidate_letter = C2_0B_PAIRS[pair]["candidate"]
    if candidate_letter == "D":
        return candidate_resume_D()
    if candidate_letter == "F":
        return candidate_resume_F()
    return candidate_resume_E()

