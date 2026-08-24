const DISALLOWED_TEAM_NAME_CHARACTERS = /[\p{Cc}\p{Cf}\p{Cs}]/u;

/** UX guard only: the backend remains authoritative for persisted team identities. */
export function hasDisallowedTeamNameCharacters(value: string): boolean {
  return DISALLOWED_TEAM_NAME_CHARACTERS.test(value);
}
