export type EventState =
  | "draft"
  | "registration"
  | "live"
  | "frozen"
  | "ended"
  | "archived";

export type UserRole = "participant" | "admin";
export type TeamRole = "owner" | "member";

export interface TeamSummary {
  id: string;
  name: string;
  role: TeamRole;
}

export interface CurrentUser {
  id: string;
  email: string;
  username: string;
  role: UserRole;
  team: TeamSummary | null;
  password_change_required?: boolean;
}

export interface MetaResponse {
  name?: string;
  version?: string;
  registration_enabled?: boolean;
  limits?: {
    max_flag_length?: number;
  };
}

export interface EventSummary {
  id: string;
  name: string;
  slug: string;
  state: EventState;
  description_md?: string | null;
  registration_at?: string | null;
  start_at?: string | null;
  end_at?: string | null;
  freeze_at?: string | null;
  team_mode?: "team" | "individual";
  registration_access_mode?: "open" | "code";
}

export interface Announcement {
  id: string;
  title: string;
  body?: string;
  body_md?: string;
  published_at?: string | null;
  publish_at?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface Challenge {
  id: string;
  slug: string;
  title: string;
  category: string;
  description_md: string;
  connection_info: string | null;
  scoring_type: "fixed" | "dynamic";
  current_points: number;
  solve_count: number;
  solved: boolean;
  max_attempts: number;
  attempts: number;
  visible_at: string | null;
  prerequisite_ids: string[];
}

export interface SubmissionResult {
  correct: boolean;
  message: string;
  awarded_points: number;
  solved_at: string | null;
}

export interface ScoreboardEntry {
  rank: number;
  team_id: string;
  team_name: string;
  score: number;
  solves: number;
  last_solve_at: string | null;
}

export interface Scoreboard {
  event: Pick<EventSummary, "id" | "name" | "state">;
  frozen: boolean;
  generated_at: string;
  total_entries: number;
  truncated: boolean;
  entries: ScoreboardEntry[];
}

export interface TeamDetails {
  id: string;
  name: string;
  role: TeamRole;
  members?: Array<{
    id: string;
    username: string;
    role: TeamRole;
  }>;
}

export interface TeamEnvelope {
  team: TeamDetails | null;
}

export interface TeamMutationEnvelope {
  team: TeamDetails;
  invite_code?: string;
}

export interface InviteCodeResponse {
  invite_code: string;
  team?: TeamDetails;
}

export interface AdminEvent extends EventSummary {
  scoreboard_visible?: boolean;
}

export type FlagType = "exact" | "regex";

export interface AdminChallenge extends Omit<Challenge, "solved" | "attempts"> {
  flag_type: FlagType;
  has_flag: boolean;
  visible: boolean;
  initial_points?: number;
  minimum_points?: number;
  decay?: number;
  created_at?: string;
  updated_at?: string;
}

export interface ChallengeWrite {
  slug: string;
  title: string;
  category: string;
  description_md: string;
  connection_info: string | null;
  scoring_type: "fixed" | "dynamic";
  initial_points: number;
  minimum_points: number;
  decay: number;
  max_attempts: number;
  visible_at: string | null;
  prerequisite_ids: string[];
  flag_type: FlagType;
  flag?: string;
  visible: boolean;
}

export interface AdminSubmission {
  id: string;
  created_at: string;
  user_id?: string;
  username: string;
  team_id?: string | null;
  team_name?: string | null;
  challenge_id: string;
  challenge_title: string;
  correct: boolean;
  awarded_points?: number;
  submitted_fingerprint?: string | null;
  ip_fingerprint?: string | null;
}

export interface AdminUser {
  id: string;
  username: string;
  email: string;
  role: UserRole;
  active: boolean;
  team_name?: string | null;
  team?: { id: string; name: string } | null;
  created_at?: string;
}

export interface AdminUserStatusWrite {
  active: boolean;
  reason: string;
}

export interface RegistrationCode {
  id: string;
  event_id: string;
  label: string;
  max_uses: number | null;
  use_count: number;
  expires_at: string | null;
  active: boolean;
  created_by: string;
  created_at: string;
  revoked_at: string | null;
}

export interface RegistrationCodeWrite {
  label: string;
  max_uses: number | null;
  expires_at: string | null;
}

export interface RegistrationCodeCreated extends RegistrationCode {
  access_code: string;
}

export interface AdminTeam {
  id: string;
  name: string;
  member_count?: number;
  score?: number;
  created_at?: string;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ApiErrorPayload {
  error: {
    code: string;
    message: string;
    request_id?: string;
  };
}
