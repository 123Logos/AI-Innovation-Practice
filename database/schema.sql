CREATE DATABASE IF NOT EXISTS ai_innovation_practice
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE ai_innovation_practice;

CREATE TABLE IF NOT EXISTS organizations (
  id CHAR(36) PRIMARY KEY,
  name VARCHAR(160) NOT NULL,
  code VARCHAR(64) NOT NULL UNIQUE,
  status ENUM('active','suspended','archived') NOT NULL DEFAULT 'active',
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS users (
  id CHAR(36) PRIMARY KEY,
  email VARCHAR(320) NOT NULL UNIQUE,
  phone VARCHAR(32) NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  display_name VARCHAR(120) NOT NULL,
  status ENUM('pending','active','locked','deleted') NOT NULL DEFAULT 'pending',
  last_login_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS roles (
  id CHAR(36) PRIMARY KEY,
  code VARCHAR(40) NOT NULL UNIQUE,
  name VARCHAR(80) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS memberships (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  user_id CHAR(36) NOT NULL,
  role_id CHAR(36) NOT NULL,
  status ENUM('invited','active','suspended','left') NOT NULL DEFAULT 'invited',
  joined_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_membership (organization_id, user_id),
  KEY ix_membership_user (user_id),
  CONSTRAINT fk_membership_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_membership_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_membership_role FOREIGN KEY (role_id) REFERENCES roles(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS refresh_tokens (
  id CHAR(36) PRIMARY KEY,
  user_id CHAR(36) NOT NULL,
  token_hash CHAR(64) NOT NULL UNIQUE,
  expires_at TIMESTAMP(6) NOT NULL,
  revoked_at TIMESTAMP(6) NULL,
  replaced_by_id CHAR(36) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY ix_refresh_user (user_id, revoked_at, expires_at),
  CONSTRAINT fk_refresh_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_refresh_replaced FOREIGN KEY (replaced_by_id) REFERENCES refresh_tokens(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS departments (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  name VARCHAR(160) NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_department (organization_id, name),
  CONSTRAINT fk_department_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS classes (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  department_id CHAR(36) NULL,
  name VARCHAR(120) NOT NULL,
  entry_year SMALLINT UNSIGNED NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_class (organization_id, name),
  KEY ix_class_department (department_id),
  CONSTRAINT fk_class_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_class_department FOREIGN KEY (department_id) REFERENCES departments(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS class_members (
  class_id CHAR(36) NOT NULL,
  membership_id CHAR(36) NOT NULL,
  joined_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  left_at TIMESTAMP(6) NULL,
  PRIMARY KEY (class_id, membership_id),
  CONSTRAINT fk_class_member_class FOREIGN KEY (class_id) REFERENCES classes(id),
  CONSTRAINT fk_class_member_membership FOREIGN KEY (membership_id) REFERENCES memberships(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS student_profiles (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  membership_id CHAR(36) NOT NULL UNIQUE,
  real_name VARCHAR(120) NULL,
  school VARCHAR(160) NULL,
  major VARCHAR(160) NULL,
  grade SMALLINT UNSIGNED NULL,
  city_preference JSON NULL,
  bio TEXT NULL,
  target_roles JSON NULL,
  visibility ENUM('private','organization','public') NOT NULL DEFAULT 'private',
  deleted_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  KEY ix_student_org (organization_id, deleted_at),
  CONSTRAINT fk_student_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_student_membership FOREIGN KEY (membership_id) REFERENCES memberships(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS skills (
  id CHAR(36) PRIMARY KEY,
  code VARCHAR(100) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  category ENUM('technical','soft','domain','tool','language') NOT NULL,
  parent_id CHAR(36) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_skill_parent FOREIGN KEY (parent_id) REFERENCES skills(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS evidence_items (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  student_profile_id CHAR(36) NOT NULL,
  type ENUM('project','internship','certificate','link','assessment') NOT NULL,
  title VARCHAR(200) NOT NULL,
  description TEXT NULL,
  url VARCHAR(2048) NULL,
  occurred_at DATE NULL,
  verification_status ENUM('unverified','pending','verified','rejected') NOT NULL DEFAULT 'unverified',
  visibility ENUM('private','organization','shared') NOT NULL DEFAULT 'private',
  metadata JSON NULL,
  deleted_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  KEY ix_evidence_student (student_profile_id, deleted_at),
  CONSTRAINT fk_evidence_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_evidence_student FOREIGN KEY (student_profile_id) REFERENCES student_profiles(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS student_skills (
  id CHAR(36) PRIMARY KEY,
  student_profile_id CHAR(36) NOT NULL,
  skill_id CHAR(36) NOT NULL,
  level TINYINT UNSIGNED NOT NULL,
  source ENUM('self_assessment','assessment','project','teacher_confirmed','ai_suggestion') NOT NULL,
  status ENUM('verified','pending','improving') NOT NULL DEFAULT 'pending',
  last_used_at DATE NULL,
  confidence DECIMAL(5,4) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_student_skill (student_profile_id, skill_id),
  CONSTRAINT fk_student_skill_profile FOREIGN KEY (student_profile_id) REFERENCES student_profiles(id),
  CONSTRAINT fk_student_skill_skill FOREIGN KEY (skill_id) REFERENCES skills(id),
  CONSTRAINT chk_student_skill_level CHECK (level BETWEEN 1 AND 5),
  CONSTRAINT chk_student_skill_confidence CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS evidence_skills (
  evidence_id CHAR(36) NOT NULL,
  skill_id CHAR(36) NOT NULL,
  relevance DECIMAL(5,4) NULL,
  PRIMARY KEY (evidence_id, skill_id),
  CONSTRAINT fk_evidence_skill_evidence FOREIGN KEY (evidence_id) REFERENCES evidence_items(id),
  CONSTRAINT fk_evidence_skill_skill FOREIGN KEY (skill_id) REFERENCES skills(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS companies (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  name VARCHAR(200) NOT NULL,
  description TEXT NULL,
  website VARCHAR(2048) NULL,
  created_by_membership_id CHAR(36) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  KEY ix_company_org (organization_id, name),
  CONSTRAINT fk_company_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_company_creator FOREIGN KEY (created_by_membership_id) REFERENCES memberships(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS job_posts (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  company_id CHAR(36) NOT NULL,
  created_by_membership_id CHAR(36) NOT NULL,
  title VARCHAR(200) NOT NULL,
  location VARCHAR(160) NULL,
  employment_type ENUM('internship','full_time','part_time','contract','other') NOT NULL,
  work_mode ENUM('onsite','hybrid','remote','unknown') NOT NULL DEFAULT 'unknown',
  description TEXT NOT NULL,
  salary_min DECIMAL(12,2) NULL,
  salary_max DECIMAL(12,2) NULL,
  deadline DATE NULL,
  source_type ENUM('manual','school_import','authorized_api') NOT NULL DEFAULT 'manual',
  status ENUM('draft','pending_review','approved','published','closed','expired','rejected') NOT NULL DEFAULT 'draft',
  reviewed_by_membership_id CHAR(36) NULL,
  reviewed_at TIMESTAMP(6) NULL,
  published_at TIMESTAMP(6) NULL,
  deleted_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  KEY ix_job_listing (organization_id, status, deadline, created_at),
  KEY ix_job_company (company_id, status),
  CONSTRAINT fk_job_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_job_company FOREIGN KEY (company_id) REFERENCES companies(id),
  CONSTRAINT fk_job_creator FOREIGN KEY (created_by_membership_id) REFERENCES memberships(id),
  CONSTRAINT fk_job_reviewer FOREIGN KEY (reviewed_by_membership_id) REFERENCES memberships(id),
  CONSTRAINT chk_job_salary CHECK (salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS job_skill_requirements (
  id CHAR(36) PRIMARY KEY,
  job_post_id CHAR(36) NOT NULL,
  skill_id CHAR(36) NOT NULL,
  requirement_type ENUM('hard','soft','domain') NOT NULL,
  required_level TINYINT UNSIGNED NULL,
  importance DECIMAL(5,4) NOT NULL DEFAULT 0.5000,
  source ENUM('manual','ai_suggestion','reviewer_confirmed') NOT NULL,
  confidence DECIMAL(5,4) NULL,
  evidence_text VARCHAR(1000) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_job_requirement (job_post_id, skill_id),
  CONSTRAINT fk_job_req_job FOREIGN KEY (job_post_id) REFERENCES job_posts(id),
  CONSTRAINT fk_job_req_skill FOREIGN KEY (skill_id) REFERENCES skills(id),
  CONSTRAINT chk_job_req_level CHECK (required_level IS NULL OR required_level BETWEEN 1 AND 5),
  CONSTRAINT chk_job_req_importance CHECK (importance BETWEEN 0 AND 1),
  CONSTRAINT chk_job_req_confidence CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ai_prompt_versions (
  id CHAR(36) PRIMARY KEY,
  task_type VARCHAR(60) NOT NULL,
  version VARCHAR(40) NOT NULL,
  provider VARCHAR(60) NOT NULL,
  model_name VARCHAR(120) NOT NULL,
  prompt_hash CHAR(64) NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_prompt_version (task_type, version)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ai_jobs (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  requested_by_membership_id CHAR(36) NULL,
  type VARCHAR(60) NOT NULL,
  target_type VARCHAR(60) NOT NULL,
  target_id CHAR(36) NOT NULL,
  idempotency_key VARCHAR(255) NOT NULL,
  status ENUM('queued','running','succeeded','failed','cancelled') NOT NULL DEFAULT 'queued',
  progress TINYINT UNSIGNED NOT NULL DEFAULT 0,
  retry_count TINYINT UNSIGNED NOT NULL DEFAULT 0,
  error_code VARCHAR(80) NULL,
  error_summary VARCHAR(1000) NULL,
  prompt_version_id CHAR(36) NULL,
  output_json JSON NULL,
  started_at TIMESTAMP(6) NULL,
  finished_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_ai_job_idempotency (organization_id, idempotency_key),
  KEY ix_ai_job_status (organization_id, status, created_at),
  CONSTRAINT fk_ai_job_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_ai_job_requester FOREIGN KEY (requested_by_membership_id) REFERENCES memberships(id),
  CONSTRAINT fk_ai_job_prompt FOREIGN KEY (prompt_version_id) REFERENCES ai_prompt_versions(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ai_job_events (
  id CHAR(36) PRIMARY KEY,
  ai_job_id CHAR(36) NOT NULL,
  event_id BIGINT UNSIGNED NOT NULL,
  event_type ENUM('progress','succeeded','failed') NOT NULL,
  progress TINYINT UNSIGNED NULL,
  payload JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_ai_event (ai_job_id, event_id),
  KEY ix_ai_event_job (ai_job_id, created_at),
  CONSTRAINT fk_ai_event_job FOREIGN KEY (ai_job_id) REFERENCES ai_jobs(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS assessments (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  name VARCHAR(200) NOT NULL,
  version VARCHAR(40) NOT NULL,
  status ENUM('draft','published','archived') NOT NULL DEFAULT 'draft',
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_assessment_version (organization_id, name, version),
  CONSTRAINT fk_assessment_org FOREIGN KEY (organization_id) REFERENCES organizations(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS assessment_questions (
  id CHAR(36) PRIMARY KEY,
  assessment_id CHAR(36) NOT NULL,
  skill_id CHAR(36) NULL,
  sequence_no SMALLINT UNSIGNED NOT NULL,
  question_type ENUM('single_choice','multiple_choice','text','scenario') NOT NULL,
  prompt TEXT NOT NULL,
  options JSON NULL,
  answer_schema JSON NULL,
  scoring_rule JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_assessment_question (assessment_id, sequence_no),
  CONSTRAINT fk_question_assessment FOREIGN KEY (assessment_id) REFERENCES assessments(id),
  CONSTRAINT fk_question_skill FOREIGN KEY (skill_id) REFERENCES skills(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS assessment_attempts (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  assessment_id CHAR(36) NOT NULL,
  student_profile_id CHAR(36) NOT NULL,
  status ENUM('in_progress','submitted','scored','cancelled') NOT NULL DEFAULT 'in_progress',
  score DECIMAL(8,4) NULL,
  feedback JSON NULL,
  started_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  submitted_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  KEY ix_attempt_student (student_profile_id, created_at),
  CONSTRAINT fk_attempt_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_attempt_assessment FOREIGN KEY (assessment_id) REFERENCES assessments(id),
  CONSTRAINT fk_attempt_student FOREIGN KEY (student_profile_id) REFERENCES student_profiles(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS assessment_answers (
  id CHAR(36) PRIMARY KEY,
  attempt_id CHAR(36) NOT NULL,
  question_id CHAR(36) NOT NULL,
  answer_json JSON NOT NULL,
  score DECIMAL(8,4) NULL,
  feedback TEXT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_attempt_answer (attempt_id, question_id),
  CONSTRAINT fk_answer_attempt FOREIGN KEY (attempt_id) REFERENCES assessment_attempts(id),
  CONSTRAINT fk_answer_question FOREIGN KEY (question_id) REFERENCES assessment_questions(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS match_runs (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  job_post_id CHAR(36) NOT NULL,
  requested_by_membership_id CHAR(36) NOT NULL,
  weight_version VARCHAR(40) NOT NULL,
  weights JSON NOT NULL,
  data_as_of TIMESTAMP(6) NOT NULL,
  status ENUM('queued','running','succeeded','failed') NOT NULL DEFAULT 'queued',
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_match_run_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_match_run_job FOREIGN KEY (job_post_id) REFERENCES job_posts(id),
  CONSTRAINT fk_match_run_requester FOREIGN KEY (requested_by_membership_id) REFERENCES memberships(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS match_results (
  id CHAR(36) PRIMARY KEY,
  match_run_id CHAR(36) NOT NULL,
  student_profile_id CHAR(36) NOT NULL,
  score DECIMAL(8,4) NOT NULL,
  skill_coverage DECIMAL(8,4) NULL,
  evidence_strength DECIMAL(8,4) NULL,
  experience_relevance DECIMAL(8,4) NULL,
  preference_score DECIMAL(8,4) NULL,
  freshness_score DECIMAL(8,4) NULL,
  explanation JSON NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_match_student (match_run_id, student_profile_id),
  KEY ix_match_score (match_run_id, score),
  CONSTRAINT fk_match_result_run FOREIGN KEY (match_run_id) REFERENCES match_runs(id),
  CONSTRAINT fk_match_result_student FOREIGN KEY (student_profile_id) REFERENCES student_profiles(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS match_result_items (
  id CHAR(36) PRIMARY KEY,
  match_result_id CHAR(36) NOT NULL,
  job_skill_requirement_id CHAR(36) NOT NULL,
  status ENUM('met','partial','gap') NOT NULL,
  student_level TINYINT UNSIGNED NULL,
  evidence_ids JSON NULL,
  gap_text VARCHAR(1000) NULL,
  suggested_action VARCHAR(1000) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_match_item (match_result_id, job_skill_requirement_id),
  CONSTRAINT fk_match_item_result FOREIGN KEY (match_result_id) REFERENCES match_results(id),
  CONSTRAINT fk_match_item_requirement FOREIGN KEY (job_skill_requirement_id) REFERENCES job_skill_requirements(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS growth_plans (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  student_profile_id CHAR(36) NOT NULL,
  job_post_id CHAR(36) NULL,
  source_match_run_id CHAR(36) NULL,
  title VARCHAR(200) NOT NULL,
  duration_days SMALLINT UNSIGNED NOT NULL,
  status ENUM('draft','active','completed','archived') NOT NULL DEFAULT 'draft',
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_growth_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_growth_student FOREIGN KEY (student_profile_id) REFERENCES student_profiles(id),
  CONSTRAINT fk_growth_job FOREIGN KEY (job_post_id) REFERENCES job_posts(id),
  CONSTRAINT fk_growth_run FOREIGN KEY (source_match_run_id) REFERENCES match_runs(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS growth_tasks (
  id CHAR(36) PRIMARY KEY,
  growth_plan_id CHAR(36) NOT NULL,
  title VARCHAR(200) NOT NULL,
  description TEXT NULL,
  skill_id CHAR(36) NULL,
  sequence_no SMALLINT UNSIGNED NOT NULL,
  due_date DATE NULL,
  status ENUM('todo','in_progress','done','skipped') NOT NULL DEFAULT 'todo',
  evidence_id CHAR(36) NULL,
  completed_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_growth_task_sequence (growth_plan_id, sequence_no),
  CONSTRAINT fk_growth_task_plan FOREIGN KEY (growth_plan_id) REFERENCES growth_plans(id),
  CONSTRAINT fk_growth_task_skill FOREIGN KEY (skill_id) REFERENCES skills(id),
  CONSTRAINT fk_growth_task_evidence FOREIGN KEY (evidence_id) REFERENCES evidence_items(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS resumes (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  student_profile_id CHAR(36) NOT NULL,
  job_post_id CHAR(36) NULL,
  title VARCHAR(200) NOT NULL,
  template VARCHAR(80) NOT NULL DEFAULT 'clean_white',
  status ENUM('draft','confirmed','archived') NOT NULL DEFAULT 'draft',
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  KEY ix_resume_student (student_profile_id, status),
  CONSTRAINT fk_resume_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_resume_student FOREIGN KEY (student_profile_id) REFERENCES student_profiles(id),
  CONSTRAINT fk_resume_job FOREIGN KEY (job_post_id) REFERENCES job_posts(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS resume_versions (
  id CHAR(36) PRIMARY KEY,
  resume_id CHAR(36) NOT NULL,
  version_no INT UNSIGNED NOT NULL,
  content JSON NOT NULL,
  source ENUM('manual','ai_generated','mixed') NOT NULL,
  confirmation_status ENUM('unconfirmed','confirmed','rejected') NOT NULL DEFAULT 'unconfirmed',
  fact_check_status ENUM('pending','passed','failed','needs_review') NOT NULL DEFAULT 'pending',
  created_by_user_id CHAR(36) NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_resume_version (resume_id, version_no),
  CONSTRAINT fk_resume_version_resume FOREIGN KEY (resume_id) REFERENCES resumes(id),
  CONSTRAINT fk_resume_version_user FOREIGN KEY (created_by_user_id) REFERENCES users(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS consents (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  student_profile_id CHAR(36) NOT NULL,
  purpose ENUM('enterprise_recommendation','teacher_guidance','resume_share','interview_report','analytics') NOT NULL,
  target_type VARCHAR(60) NOT NULL,
  target_id CHAR(36) NULL,
  fields JSON NOT NULL,
  expires_at TIMESTAMP(6) NULL,
  revoked_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY ix_consent_lookup (student_profile_id, purpose, target_type, target_id, revoked_at, expires_at),
  CONSTRAINT fk_consent_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_consent_student FOREIGN KEY (student_profile_id) REFERENCES student_profiles(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS resume_shares (
  id CHAR(36) PRIMARY KEY,
  resume_id CHAR(36) NOT NULL,
  resume_version_id CHAR(36) NOT NULL,
  consent_id CHAR(36) NOT NULL,
  target_type VARCHAR(60) NOT NULL,
  target_id CHAR(36) NOT NULL,
  fields JSON NOT NULL,
  expires_at TIMESTAMP(6) NULL,
  revoked_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_share_resume FOREIGN KEY (resume_id) REFERENCES resumes(id),
  CONSTRAINT fk_share_version FOREIGN KEY (resume_version_id) REFERENCES resume_versions(id),
  CONSTRAINT fk_share_consent FOREIGN KEY (consent_id) REFERENCES consents(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS interview_sessions (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  student_profile_id CHAR(36) NOT NULL,
  job_post_id CHAR(36) NULL,
  resume_version_id CHAR(36) NULL,
  mode ENUM('text','audio','video') NOT NULL,
  round ENUM('screening','technical','behavioral','final','custom') NOT NULL,
  question_count SMALLINT UNSIGNED NOT NULL,
  status ENUM('created','in_progress','finished','failed') NOT NULL DEFAULT 'created',
  started_at TIMESTAMP(6) NULL,
  finished_at TIMESTAMP(6) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  KEY ix_interview_student (student_profile_id, created_at),
  CONSTRAINT fk_interview_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_interview_student FOREIGN KEY (student_profile_id) REFERENCES student_profiles(id),
  CONSTRAINT fk_interview_job FOREIGN KEY (job_post_id) REFERENCES job_posts(id),
  CONSTRAINT fk_interview_resume_version FOREIGN KEY (resume_version_id) REFERENCES resume_versions(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS interview_turns (
  id CHAR(36) PRIMARY KEY,
  interview_session_id CHAR(36) NOT NULL,
  sequence_no SMALLINT UNSIGNED NOT NULL,
  speaker ENUM('ai','student') NOT NULL,
  content TEXT NULL,
  transcript_status ENUM('none','partial','final') NOT NULL DEFAULT 'none',
  started_at TIMESTAMP(6) NULL,
  ended_at TIMESTAMP(6) NULL,
  metadata JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_interview_turn (interview_session_id, sequence_no),
  CONSTRAINT fk_turn_session FOREIGN KEY (interview_session_id) REFERENCES interview_sessions(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS media_rooms (
  id CHAR(36) PRIMARY KEY,
  interview_session_id CHAR(36) NOT NULL,
  provider VARCHAR(80) NOT NULL,
  provider_room_ref VARCHAR(255) NOT NULL,
  status ENUM('created','active','expired','closed','failed') NOT NULL DEFAULT 'created',
  expires_at TIMESTAMP(6) NOT NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_media_provider_room (provider, provider_room_ref),
  CONSTRAINT fk_media_interview FOREIGN KEY (interview_session_id) REFERENCES interview_sessions(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS interview_reports (
  id CHAR(36) PRIMARY KEY,
  interview_session_id CHAR(36) NOT NULL UNIQUE,
  status ENUM('pending','ready','failed') NOT NULL DEFAULT 'pending',
  report_json JSON NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  CONSTRAINT fk_report_interview FOREIGN KEY (interview_session_id) REFERENCES interview_sessions(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS files (
  id CHAR(36) PRIMARY KEY,
  organization_id CHAR(36) NOT NULL,
  owner_user_id CHAR(36) NOT NULL,
  object_key VARCHAR(1024) NOT NULL,
  purpose VARCHAR(80) NOT NULL,
  content_type VARCHAR(160) NOT NULL,
  size_bytes BIGINT UNSIGNED NOT NULL,
  checksum_sha256 CHAR(64) NULL,
  scan_status ENUM('pending','clean','blocked') NOT NULL DEFAULT 'pending',
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  UNIQUE KEY uq_file_object_key (object_key(768)),
  CONSTRAINT fk_file_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_file_owner FOREIGN KEY (owner_user_id) REFERENCES users(id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS audit_logs (
  id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  organization_id CHAR(36) NULL,
  actor_user_id CHAR(36) NULL,
  action VARCHAR(100) NOT NULL,
  resource_type VARCHAR(80) NOT NULL,
  resource_id CHAR(36) NULL,
  request_id VARCHAR(100) NULL,
  purpose VARCHAR(255) NULL,
  fields_accessed JSON NULL,
  result ENUM('success','denied','failed') NOT NULL,
  ip_hash CHAR(64) NULL,
  created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  KEY ix_audit_org_time (organization_id, created_at),
  KEY ix_audit_actor_time (actor_user_id, created_at),
  CONSTRAINT fk_audit_org FOREIGN KEY (organization_id) REFERENCES organizations(id),
  CONSTRAINT fk_audit_actor FOREIGN KEY (actor_user_id) REFERENCES users(id)
) ENGINE=InnoDB;
