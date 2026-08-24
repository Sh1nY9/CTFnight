import { http } from "./client";
import type {
  AdminChallenge,
  AdminEvent,
  AdminSubmission,
  AdminTeam,
  AdminUser,
  AdminUserStatusWrite,
  Announcement,
  Challenge,
  ChallengeWrite,
  CurrentUser,
  EventSummary,
  InviteCodeResponse,
  MetaResponse,
  Paginated,
  RegistrationCode,
  RegistrationCodeCreated,
  RegistrationCodeWrite,
  Scoreboard,
  SubmissionResult,
  TeamDetails,
  TeamEnvelope,
  TeamMutationEnvelope,
} from "./types";

type ListEnvelope<T> = T[] | Paginated<T> | { items: T[] };

export function normalizeList<T>(value: ListEnvelope<T>): T[] {
  return Array.isArray(value) ? value : value.items;
}

export const api = {
  meta: () => http.get<MetaResponse>("/meta"),

  auth: {
    me: () => http.get<CurrentUser>("/auth/me"),
    login: (email: string, password: string) =>
      http.post<CurrentUser>("/auth/login", { email, password }),
    register: (email: string, username: string, password: string, access_code?: string) =>
      http.post<CurrentUser>("/auth/register", { email, username, password, access_code }),
    changePassword: (current_password: string, new_password: string) =>
      http.post<CurrentUser>("/auth/change-password", { current_password, new_password }),
    logout: () => http.post<void>("/auth/logout"),
  },

  team: {
    mine: () => http.get<TeamEnvelope>("/teams/me").then((response) => response.team),
    create: (name: string) => http.post<InviteCodeResponse>("/teams", { name }),
    join: (invite_code: string) => http.post<TeamEnvelope>("/teams/join", { invite_code }).then((response) => response.team),
    rotateInvite: () => http.post<InviteCodeResponse>("/teams/rotate-invite"),
    transferOwner: (user_id: string) =>
      http.post<TeamMutationEnvelope>("/teams/transfer-owner", { user_id })
        .then((response) => response.team),
    removeMember: (user_id: string) =>
      http.post<TeamMutationEnvelope>("/teams/remove-member", { user_id }),
    leave: () => http.post<void>("/teams/leave"),
  },

  participant: {
    event: () => http.get<EventSummary>("/events/current"),
    announcements: () => http.get<ListEnvelope<Announcement>>("/announcements").then(normalizeList),
    challenges: () => http.get<ListEnvelope<Challenge>>("/challenges").then(normalizeList),
    challenge: (id: string) => http.get<Challenge>(`/challenges/${encodeURIComponent(id)}`),
    submit: (id: string, flag: string, idempotency_key: string) =>
      http.post<SubmissionResult>(`/challenges/${encodeURIComponent(id)}/submit`, {
        flag,
        idempotency_key,
      }),
    scoreboard: () => http.get<Scoreboard>("/scoreboard"),
  },

  admin: {
    event: () => http.get<AdminEvent>("/admin/event"),
    updateEvent: (event: Partial<AdminEvent>) => http.put<AdminEvent>("/admin/event", event),
    challenges: (options?: { signal?: AbortSignal }) =>
      http.get<ListEnvelope<AdminChallenge>>("/admin/challenges", options).then(normalizeList),
    createChallenge: (challenge: ChallengeWrite) =>
      http.post<AdminChallenge>("/admin/challenges", serializeChallenge(challenge)),
    updateChallenge: (id: string, challenge: Partial<ChallengeWrite>) =>
      http.put<AdminChallenge>(`/admin/challenges/${encodeURIComponent(id)}`, serializeChallenge(challenge)),
    setChallengeVisibility: (id: string, visible: boolean) =>
      http.post<AdminChallenge>(`/admin/challenges/${encodeURIComponent(id)}/visibility`, { visible }),
    submissions: (
      query?: Record<string, string | number | boolean | undefined>,
      options?: { signal?: AbortSignal },
    ) =>
      http.get<ListEnvelope<AdminSubmissionWire>>("/admin/submissions", { ...options, query })
        .then(normalizeList)
        .then((items) => items.map(normalizeSubmission)),
    announcements: () =>
      http.get<ListEnvelope<Announcement>>("/admin/announcements").then(normalizeList),
    createAnnouncement: (announcement: Partial<Announcement>) =>
      http.post<Announcement>("/admin/announcements", announcement),
    updateAnnouncement: (id: string, announcement: Partial<Announcement>) =>
      http.put<Announcement>(`/admin/announcements/${encodeURIComponent(id)}`, announcement),
    deleteAnnouncement: (id: string) =>
      http.delete<void>(`/admin/announcements/${encodeURIComponent(id)}`),
    users: (
      query?: Record<string, string | number | boolean | undefined>,
      options?: { signal?: AbortSignal },
    ) => http.get<ListEnvelope<AdminUser>>("/admin/users", { ...options, query }).then(normalizeList),
    setUserStatus: (id: string, status: AdminUserStatusWrite) =>
      http.put<AdminUser>(`/admin/users/${encodeURIComponent(id)}/status`, status),
    registrationCodes: () =>
      http.get<ListEnvelope<RegistrationCode>>("/admin/registration-codes").then(normalizeList),
    createRegistrationCode: (code: RegistrationCodeWrite) =>
      http.post<RegistrationCodeCreated>("/admin/registration-codes", code),
    revokeRegistrationCode: (id: string) =>
      http.delete<void>(`/admin/registration-codes/${encodeURIComponent(id)}`),
    teams: (query?: Record<string, string | number | boolean | undefined>) =>
      http.get<ListEnvelope<AdminTeam>>("/admin/teams", { query }).then(normalizeList),
  },
};

function serializeChallenge(challenge: Partial<ChallengeWrite>): Record<string, unknown> {
  const { flag_type, flag, ...rest } = challenge;
  const payload: Record<string, unknown> = { ...rest };
  if (flag) payload.flag = { type: flag_type ?? "exact", value: flag };
  return payload;
}

interface AdminSubmissionWire {
  id: string;
  team: { id: string; name: string };
  user: { id: string; username: string };
  challenge: { id: string; title: string };
  correct: boolean;
  awarded_points?: number;
  submitted_fingerprint?: string;
  ip_fingerprint?: string;
  created_at: string;
}

function normalizeSubmission(item: AdminSubmissionWire): AdminSubmission {
  return {
    id: item.id,
    created_at: item.created_at,
    user_id: item.user.id,
    username: item.user.username,
    team_id: item.team.id,
    team_name: item.team.name,
    challenge_id: item.challenge.id,
    challenge_title: item.challenge.title,
    correct: item.correct,
    awarded_points: item.awarded_points,
    submitted_fingerprint: item.submitted_fingerprint,
    ip_fingerprint: item.ip_fingerprint,
  };
}
