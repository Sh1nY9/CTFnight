import { api } from "@/api/endpoints";
import { ApiError } from "@/api/client";
import type { EventSummary, TeamDetails } from "@/api/types";
import { useAuth } from "@/auth/AuthContext";
import { ErrorState, LoadingState } from "@/components/States";
import { PageHeader, Panel } from "@/components/Page";
import { useToast } from "@/components/Toast";
import { getErrorMessage } from "@/lib/errors";
import { teamChangesAreOpen } from "@/lib/eventGates";
import { hasDisallowedTeamNameCharacters } from "@/lib/teamName";
import {
  ArrowRightLeft,
  Clipboard,
  Crown,
  LockKeyhole,
  LogOut,
  RefreshCw,
  ShieldCheck,
  UserMinus,
  UserPlus,
  UsersRound,
} from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";

export function TeamPage() {
  const { user, refresh } = useAuth();
  const { push } = useToast();
  const [team, setTeam] = useState<TeamDetails | null>(null);
  const [currentEvent, setCurrentEvent] = useState<EventSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"create" | "join">("create");
  const [name, setName] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [newInviteCode, setNewInviteCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [memberAction, setMemberAction] = useState<{ kind: "transfer" | "remove"; userId: string } | null>(null);
  const teamChangesOpen = teamChangesAreOpen(currentEvent);
  const busy = submitting || memberAction !== null;

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const event = await api.participant.event();
      setCurrentEvent(event);
      if (event.team_mode === "individual") {
        setTeam(null);
        setLoading(false);
        return;
      }
    } catch (requestError) {
      setError(getErrorMessage(requestError));
      setLoading(false);
      return;
    }
    if (!user?.team) {
      setTeam(null);
      setLoading(false);
      return;
    }
    try {
      setTeam(await api.team.mine());
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 404) setTeam(null);
      else setError(getErrorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }, [user?.team]);

  useEffect(() => { void load(); }, [load]);

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault();
    if (!teamChangesOpen) return;
    if (hasDisallowedTeamNameCharacters(name)) {
      setError("팀 이름에는 제어 문자나 보이지 않는 방향 전환 문자를 사용할 수 없습니다.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const response = await api.team.create(name.trim());
      setNewInviteCode(response.invite_code);
      await refresh();
      setTeam(response.team ?? await api.team.mine());
      push("팀을 만들었습니다.", "success");
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally { setSubmitting(false); }
  };

  const handleJoin = async (event: FormEvent) => {
    event.preventDefault();
    if (!teamChangesOpen) return;
    setSubmitting(true);
    setError("");
    try {
      const joined = await api.team.join(inviteCode.trim());
      if (!joined) throw new Error("팀 정보를 받지 못했습니다.");
      setTeam(joined);
      await refresh();
      push("팀에 합류했습니다.", "success");
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally { setSubmitting(false); }
  };

  const rotateInvite = async () => {
    if (!teamChangesOpen) return;
    setSubmitting(true);
    try {
      const response = await api.team.rotateInvite();
      setNewInviteCode(response.invite_code);
      push("기존 초대 코드를 폐기했습니다.", "success");
    } catch (requestError) { push(getErrorMessage(requestError), "error"); }
    finally { setSubmitting(false); }
  };

  const leave = async () => {
    if (!teamChangesOpen) return;
    if (!window.confirm(team?.role === "owner" ? "팀 소유자가 나가면 팀 운영에 영향을 줄 수 있습니다. 정말 나갈까요?" : "정말 이 팀에서 나갈까요?")) return;
    setSubmitting(true);
    try {
      await api.team.leave();
      setTeam(null);
      setNewInviteCode("");
      await refresh();
      push("팀에서 나왔습니다.", "success");
    } catch (requestError) { push(getErrorMessage(requestError), "error"); }
    finally { setSubmitting(false); }
  };

  const manageMember = async (
    member: NonNullable<TeamDetails["members"]>[number],
    kind: "transfer" | "remove",
  ) => {
    if (
      busy
      || !teamChangesOpen
      || currentEvent?.team_mode === "individual"
      || team?.role !== "owner"
      || member.role !== "member"
      || member.id === user?.id
    ) return;
    const confirmed = kind === "transfer"
      ? window.confirm(`${member.username}님에게 팀 소유권을 이전할까요? 이전 후에는 내가 일반 멤버가 됩니다.`)
      : window.confirm(`${member.username}님을 팀에서 제외할까요? 이 작업은 즉시 적용됩니다.`);
    if (!confirmed) return;

    setMemberAction({ kind, userId: member.id });
    setError("");
    try {
      const removal = kind === "remove" ? await api.team.removeMember(member.id) : null;
      const updated = kind === "transfer"
        ? await api.team.transferOwner(member.id)
        : removal!.team;
      setTeam(updated);
      if (kind === "transfer") setNewInviteCode("");
      else setNewInviteCode(removal?.invite_code ?? "");
      await refresh();
      push(
        kind === "transfer"
          ? `${member.username}님에게 팀 소유권을 이전했습니다.`
          : `${member.username}님을 팀에서 제외했습니다.`,
        "success",
      );
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setMemberAction(null);
    }
  };

  const copyInvite = async () => {
    try {
      await navigator.clipboard.writeText(newInviteCode);
      push("초대 코드를 복사했습니다.", "success");
    } catch { push("초대 코드를 복사하지 못했습니다.", "error"); }
  };

  if (loading) return <div className="page-container"><LoadingState label="팀 정보를 불러오는 중" /></div>;
  if (error && !currentEvent) return <div className="page-container"><PageHeader eyebrow="SQUAD OPERATIONS" title="팀" description="팀 정보를 확인할 수 없습니다." /><ErrorState message={error} onRetry={load} /></div>;

  return (
    <div className="page-container team-page">
      <PageHeader eyebrow="SQUAD OPERATIONS" title="팀" description="팀을 구성하고 한 점수판에서 함께 경쟁하세요." />
      {error && <ErrorState message={error} onRetry={load} />}
      {currentEvent?.team_mode !== "individual" && !teamChangesOpen && (
        <div className="warning-box team-readonly-notice" role="status"><LockKeyhole /><p>팀 구성 변경은 등록 기간에만 가능합니다. 현재 팀 정보는 읽기 전용입니다.</p></div>
      )}

      {currentEvent?.team_mode === "individual" ? (
        <section className="individual-entry">
          <span className="individual-entry__avatar">{user?.username.slice(0, 2).toUpperCase()}</span>
          <div><p className="eyebrow">INDIVIDUAL DIVISION</p><h2>{user?.username}</h2><p>이 이벤트는 개인전입니다. 별도의 팀 생성이나 초대 없이 내 계정으로 점수가 집계됩니다.</p></div>
          <ShieldCheck size={28} />
        </section>
      ) : !team ? (
        <div className="team-onboarding">
          <div className="team-onboarding__intro">
            <span><UsersRound /></span>
            <p className="eyebrow">NO TEAM DETECTED</p>
            <h2>혼자 시작해도,<br />함께 완주하세요.</h2>
            <p>새 팀을 만들거나 전달받은 초대 코드로 기존 팀에 합류할 수 있습니다.</p>
          </div>
          <section className="team-form-card">
            <div className="segmented-control" role="tablist" aria-label="팀 구성 방법">
              <button aria-selected={mode === "create"} className={mode === "create" ? "is-active" : ""} disabled={!teamChangesOpen} onClick={() => setMode("create")} role="tab" type="button">새 팀 만들기</button>
              <button aria-selected={mode === "join"} className={mode === "join" ? "is-active" : ""} disabled={!teamChangesOpen} onClick={() => setMode("join")} role="tab" type="button">초대 코드 입력</button>
            </div>
            {mode === "create" ? (
              <form onSubmit={handleCreate}>
                <label className="field"><span>팀 이름</span><input disabled={!teamChangesOpen} maxLength={80} minLength={2} onChange={(event) => setName(event.target.value)} placeholder="0xCTFnight" required value={name} /><small>2–80자, 점수판에 공개됩니다.</small></label>
                <button className="button button--primary button--full" disabled={submitting || !teamChangesOpen} type="submit"><UsersRound size={17} /> {submitting ? "생성 중…" : "팀 만들기"}</button>
              </form>
            ) : (
              <form onSubmit={handleJoin}>
                <label className="field"><span>초대 코드</span><input autoCapitalize="characters" className="mono-input" disabled={!teamChangesOpen} maxLength={128} minLength={16} onChange={(event) => setInviteCode(event.target.value)} placeholder="CTFnight-XXXX-XXXX" required value={inviteCode} /></label>
                <button className="button button--primary button--full" disabled={submitting || !teamChangesOpen} type="submit"><UserPlus size={17} /> {submitting ? "합류 중…" : "팀 합류"}</button>
              </form>
            )}
          </section>
        </div>
      ) : (
        <>
          <section className="team-identity">
            <div className="team-avatar">{team.name.slice(0, 2).toUpperCase()}</div>
            <div><p className="eyebrow">ACTIVE SQUAD</p><h2>{team.name}</h2><p>{team.members?.length ?? 1}명 참가 중</p></div>
            <span className="role-badge">{team.role === "owner" ? <><Crown size={14} /> 소유자</> : "멤버"}</span>
          </section>

          {newInviteCode && (
            <div className="invite-reveal" role="status">
              <ShieldCheck />
              <div><strong>새 초대 코드</strong><p>보안을 위해 지금 한 번만 표시됩니다. 안전한 채널로 공유하세요.</p><code>{newInviteCode}</code></div>
              <button className="button button--secondary button--small" onClick={copyInvite} type="button"><Clipboard size={15} /> 복사</button>
            </div>
          )}

          <div className="team-grid">
            <Panel title="멤버" description={`${team.members?.length ?? 1}명이 함께하고 있습니다.`}>
              <div className="member-list">
                {(team.members ?? [{ id: user!.id, username: user!.username, role: team.role }]).map((member) => (
                  <div className="member-row" key={member.id}>
                    <span className="member-avatar">{member.username.slice(0, 1).toUpperCase()}</span>
                    <div className="member-row__identity"><strong>{member.username}</strong><small>{member.role === "owner" ? "팀 소유자" : "팀 멤버"}</small></div>
                    {member.role === "owner" ? <Crown size={15} /> : team.role === "owner" && teamChangesOpen && member.id !== user?.id ? (
                      <div className="member-row__actions">
                        <button
                          aria-label={`${member.username}에게 소유권 이전`}
                          className="button button--secondary button--small"
                          disabled={busy}
                          onClick={() => void manageMember(member, "transfer")}
                          type="button"
                        >
                          <ArrowRightLeft size={14} />
                          {memberAction?.kind === "transfer" && memberAction.userId === member.id ? "이전 중…" : "소유권 이전"}
                        </button>
                        <button
                          aria-label={`${member.username} 팀에서 제외`}
                          className="button button--danger-ghost button--small"
                          disabled={busy}
                          onClick={() => void manageMember(member, "remove")}
                          type="button"
                        >
                          <UserMinus size={14} />
                          {memberAction?.kind === "remove" && memberAction.userId === member.id ? "제외 중…" : "제외"}
                        </button>
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </Panel>
            <Panel title="팀 관리" description="초대와 멤버십을 관리합니다.">
              <div className="team-actions">
                {team.role === "owner" && <button className="button button--secondary" disabled={busy || !teamChangesOpen} onClick={() => void rotateInvite()} type="button"><RefreshCw size={16} /> 초대 코드 교체</button>}
                <button className="button button--danger-ghost" disabled={busy || !teamChangesOpen} onClick={() => void leave()} type="button"><LogOut size={16} /> 팀 나가기</button>
              </div>
            </Panel>
          </div>
        </>
      )}
    </div>
  );
}
