import type { ProcessResponse } from "../types"

// Placeholder data matching the design mockup, used until the Convert button
// is wired to POST /api/process and render a real response.
export const sampleReview: ProcessResponse = {
  original_text: [
    "Dohyun Kim / Seoul / 010-1234-5678",
    "8-yr backend engineer. Large-scale commerce traffic, team lead.",
    "[Experience] Mar 2021–now Marketflow / Backend Lead",
    "- Payments redesign, latency -40% · Order API MSA, 3M tx/day",
    "Skills: Java, Spring, Kotlin, Redis, Kafka, AWS",
    "Military: completed / Hobbies: climbing, photography / Portfolio",
  ].join("\n"),
  profile: {
    full_name: "Dohyun Kim",
    email: null,
    phone: "010-1234-5678",
    location: "Seoul",
    linkedin_url: null,
    portfolio_url: "https://dohyunkim.dev",
    professional_summary: "8-yr backend engineer. Large-scale commerce traffic, team lead.",
    skills: ["Java", "Spring", "Kotlin", "Redis", "Kafka", "AWS"],
    languages: [],
    work_experience: [
      {
        company: "Marketflow",
        title: "Backend Lead",
        location: null,
        start_date: "2021-03",
        end_date: "Present",
        description: ["Payments redesign, latency -40%", "Order API MSA, 3M tx/day"],
      },
    ],
    education: [],
    certifications: [],
    salary_expectation: null,
    notice_period: null,
    work_authorization: "Completed military service",
    interview_availability: null,
    missing_fields: [
      { field_name: "salary_expectation", label: "Salary expectation", reason: "Not clearly present in the source resume." },
      { field_name: "notice_period", label: "Notice period", reason: "Not clearly present in the source resume." },
    ],
    client_display_rules: {
      salary_expectation: "pending_confirmation",
      notice_period: "pending_confirmation",
    },
  },
  // Matches the mockup's exact numbers (14 / 12 / 2) for visual comparison.
  // The real endpoint always returns extracted === placed (see app/main.py::_build_ledger) —
  // this static placeholder is the only place those will differ.
  ledger: {
    extracted: 14,
    placed: 12,
    needs_review: 2,
  },
}
