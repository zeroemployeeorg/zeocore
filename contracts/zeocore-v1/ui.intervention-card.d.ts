// Generated from ui.intervention-card.schema.json. Do not edit by hand.
// Schema: https://zeocore.zeo.ac/contracts/zeocore-v1/ui.intervention-card.schema.json
// Rules that tie fields together (for example intervention_ref equal to
// work_ref.work_id) are enforced by the validator, not by these types.

export interface Action {
  route: "intervention" | "reserved" | "task";
  action_id: string;
  type?: "locate_work" | "request_checkpoint" | "request_update" | "explain_blocker" | "request_unblock" | "verify_delivery" | "request_recovery_plan" | "escalate";
  reserved_operation?: "decision.publishing_notice" | "decision.public_release" | "decision.spend" | "decision.repo_visibility" | "decision.production_go" | "decision.gold_tag" | "decision.external_application" | "decision.outside_org_write" | "decision.brand_rule" | "review.own_content";
  task_operation?: "task.create_request" | "task.acknowledge" | "task.report_blocker" | "task.submit_evidence" | "task.record_answer" | "day.propose" | "day.accept" | "day.add_today" | "day.schedule" | "day.defer" | "day.delegate" | "day.decline" | "day.session_propose" | "day.session_accept";
  label: string;
  enabled: boolean;
  reason?: string;
}

export interface Approval {
  situation: "approval";
  approval_id: string;
  request_digest: string;
  operation_id: string;
  preview: string;
  status: "pending" | "approved" | "consumed" | "refused";
  issued_at: string;
  expires_at: string;
  decided_at?: string;
  freshness?: Freshness[];
}

export interface ArtifactRef {
  kind: "repo" | "branch" | "commit" | "pr" | "deployment" | "local_only";
  ref: string;
}

export interface ArtifactToReview {
  ref: string;
  revision: string;
}

export interface Blocker {
  subject_ref: SubjectRef;
  subject_revision: number;
  work_ref?: WorkRef;
  intervention_ref?: string;
  evidence: Evidence[];
  freshness: Freshness[];
  primary_action?: Action;
  secondary_actions?: Action[];
  situation: "blocker";
  reported_blocker: string;
  since: string;
  depends_on?: string;
  release_condition?: string;
}

export interface ChecklistStep {
  text: string;
  done: boolean;
}

export interface Checkpoint {
  subject_ref: SubjectRef;
  subject_revision: number;
  work_ref?: WorkRef;
  intervention_ref?: string;
  evidence: Evidence[];
  freshness: Freshness[];
  primary_action?: Action;
  secondary_actions?: Action[];
  situation: "checkpoint";
  requested_at: string;
  due_at?: string;
  delivery: "pending" | "delivered" | "uncertain" | "refused";
  response: "awaiting" | "acknowledged" | "evidence_returned";
}

export interface ChoiceAnswer {
  kind: "choice";
  options: Option[];
}

export interface ChoiceReply {
  reply_ref: string;
  answers_digest: string;
  closes_at?: string;
  mode: "choice";
  options: Option[];
}

export interface Decision {
  subject_ref: SubjectRef;
  subject_revision: number;
  work_ref?: WorkRef;
  intervention_ref?: string;
  evidence: Evidence[];
  freshness: Freshness[];
  primary_action?: Action;
  secondary_actions?: Action[];
  situation: "decision";
  ask_type: "decide" | "review" | "do" | "session";
  question: string;
  why_operator: "judgment" | "credentials" | "physical_presence" | "reserved_authority";
  answer?: ChoiceAnswer | IntegerAnswer;
  artifact?: ArtifactToReview;
  review_images?: [ReviewImage, ReviewImage];
  checklist?: ChecklistStep[];
  session?: Session;
  result_slots?: ResultSlot[];
  result_max_total_bytes?: number;
  target_label?: string;
  pinned_texts?: PinnedText[];
}

export interface Evidence {
  kind: "operation" | "receipt" | "document" | "source";
  ref: string;
}

export interface Flags {
  stale: "yes" | "no" | "unknown";
  blocked: "yes" | "no" | "unknown";
  reason?: string;
}

export interface Freshness {
  source: string;
  observed_at: string;
  collected_at: string;
  state: "healthy" | "degraded" | "error" | "unknown" | "stale" | "unavailable";
}

export interface IntegerAnswer {
  kind: "bounded_integer";
  unit: "minutes" | "hours" | "days" | "count";
  minimum: number;
  maximum: number;
  presets: number[];
  allow_other: boolean;
  recommended?: number;
}

export interface LatestCheckpoint {
  ref: string;
  answered_at: string;
  age_h?: number;
  run_ref?: string;
}

export interface Link {
  label: string;
  url: string;
}

export interface LocalOnly {
  flag: "yes" | "no" | "unknown";
  note?: string;
  git_unpushed_count?: number;
}

export interface Message {
  situation: "message";
  message_id: string;
  source_digest: string;
  sent_at: string;
  from: string;
  subject: string;
  body: string;
  project?: string;
  in_reply_to?: string;
  priority?: "p0" | "p1" | "p2";
  expires_at?: string;
  links?: Link[];
  freshness?: Freshness[];
  reply?: TextReply | ChoiceReply;
}

export interface Milestone {
  what?: string;
  when?: string;
  evidence_ref?: string;
  stage: "implemented" | "qualified" | "reviewed" | "merged" | "delivered" | "installed" | "consumer_accepted" | "unknown";
}

export interface NextAct {
  action: string;
  owner: string;
  time?: string;
  time_quality: "due" | "target" | "unknown";
}

export interface Option {
  id: string;
  label: string;
}

export interface Outcome {
  subject_ref: SubjectRef;
  subject_revision: number;
  work_ref?: WorkRef;
  intervention_ref?: string;
  evidence: Evidence[];
  freshness: Freshness[];
  primary_action?: Action;
  secondary_actions?: Action[];
  situation: "outcome";
  outcome: "verified" | "failed" | "cancelled";
  completion_evidence?: Evidence[];
  verification_ref?: string;
  reason?: string;
  closed_at: string;
}

export interface PinnedText {
  label: string;
  language: "sql" | "text";
  text: string;
  sha256: string;
}

export interface ProjectCheckpoint {
  situation: "project_checkpoint";
  run_ref: string;
  index_revision?: string;
  project_ref: string;
  subject_revision: number;
  seats: SeatAnswer[];
  last_product?: Milestone;
  last_administrative?: Milestone;
  wait?: Wait;
  local_only?: LocalOnly;
  project_checkpoint_age_h?: number;
  sources: SourceStatus[];
  flags: Flags;
  next_act?: NextAct;
  primary_action?: Action;
  secondary_actions?: Action[];
}

export interface RequestStatus {
  subject_ref: SubjectRef;
  subject_revision: number;
  work_ref?: WorkRef;
  intervention_ref?: string;
  evidence: Evidence[];
  freshness: Freshness[];
  primary_action?: Action;
  secondary_actions?: Action[];
  situation: "request_status";
  delivery: "pending" | "delivered" | "uncertain" | "refused";
  response: "awaiting" | "acknowledged" | "evidence_returned";
  outcome: "open" | "verifying" | "verified" | "failed" | "cancelled";
  deadline?: string;
  overdue?: boolean;
  summary: string;
}

export interface ResultSlot {
  slot_id: string;
  label: string;
  value_type: "text" | "timestamp" | "confirm";
  max_utf8_bytes?: number;
  max_data_rows?: number;
  required?: boolean;
}

export interface ReviewImage {
  media_ref: string;
  label: string;
  alt: string;
  sha256: string;
  byte_length: number;
  content_type: "image/png" | "image/jpeg" | "image/webp";
  width: number;
  height: number;
}

export interface SeatAnswer {
  seat: string;
  answer_state: "answered" | "not_answered" | "unknown";
  answer_at?: string;
  answer_age_h?: number;
  answer_ref?: string;
  latest_checkpoint?: LatestCheckpoint;
}

export interface Session {
  start: string;
  duration_minutes: number;
}

export interface SourceItem {
  situation: "source_item";
  source_id: string;
  source_digest: string;
  observed_at: string;
  project: string;
  label: string;
  title: string;
  state: string;
  next_action?: string;
  copy_line?: string;
  owner?: string;
  deadline?: string;
  source_url?: string;
  freshness?: Freshness[];
}

export interface SourceStatus {
  source: "seat_answers" | "git_org_main" | "git_branches" | "github_prs" | "seat_state_local" | "org_chart" | "operator_tasks";
  observed_at?: string;
  collected_at?: string;
  state: "ok" | "partial" | "unavailable" | "stale" | "unknown";
  reason?: string;
}

export interface SubjectRef {
  project_id: string;
  seat_id: string;
  assignment_id: string;
  actor_id?: string;
}

export interface TextReply {
  reply_ref: string;
  answers_digest: string;
  closes_at?: string;
  mode: "text";
  max_length: number;
}

export interface Wait {
  owner?: string;
  since?: string;
  text?: string;
  source: "seat_answer" | "sow_your_next" | "unknown";
}

export interface WorkLocation {
  subject_ref: SubjectRef;
  subject_revision: number;
  work_ref?: WorkRef;
  intervention_ref?: string;
  evidence: Evidence[];
  freshness: Freshness[];
  primary_action?: Action;
  secondary_actions?: Action[];
  situation: "work_location";
  claimed_work: string;
  artifact_refs: ArtifactRef[];
  location_state: "published" | "local_only" | "unknown";
}

export interface WorkRef {
  work_id: string;
  source_ref: string;
  revision: string;
}

export type InterventionCard = WorkLocation | Checkpoint | Blocker | Decision | RequestStatus | Outcome | ProjectCheckpoint | SourceItem | Message | Approval;
