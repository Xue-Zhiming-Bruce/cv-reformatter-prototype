export interface Language {
  name: string
  proficiency: string | null
}

export interface WorkExperience {
  company: string | null
  title: string | null
  location: string | null
  start_date: string | null
  end_date: string | null
  description: string[]
}

export interface Education {
  institution: string | null
  degree: string | null
  field_of_study: string | null
  start_date: string | null
  end_date: string | null
}

export interface Certification {
  name: string
  issuer: string | null
  date: string | null
}

export interface MissingField {
  field_name: string
  label: string
  reason: string
}

export type DisplayRule = "show" | "hide" | "pending_confirmation" | "available_upon_request"

export interface CandidateProfile {
  full_name: string | null
  email: string | null
  phone: string | null
  location: string | null
  linkedin_url: string | null
  portfolio_url: string | null
  professional_summary: string | null
  skills: string[]
  languages: Language[]
  work_experience: WorkExperience[]
  education: Education[]
  certifications: Certification[]
  salary_expectation: string | null
  notice_period: string | null
  work_authorization: string | null
  interview_availability: string | null
  missing_fields: MissingField[]
  client_display_rules: Record<string, DisplayRule>
}

export interface LedgerSummary {
  extracted: number
  placed: number
  needs_review: number
}

export interface ProcessResponse {
  artifact_id: string
  profile: CandidateProfile
  ledger: LedgerSummary
  original_text: string
  original_pdf_preview_url: string | null
  original_preview_error: string | null
  debug_artifacts: Record<string, string>
  artifact_metadata_url: string
}
