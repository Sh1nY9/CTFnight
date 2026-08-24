import { api } from "@/api/endpoints";
import type { AdminEvent, EventState } from "@/api/types";
import { ErrorState, LoadingState } from "@/components/States";
import { EventStatusPill } from "@/components/StatusPill";
import { PageHeader, Panel } from "@/components/Page";
import { useToast } from "@/components/Toast";
import { scoringIsLocked } from "@/lib/eventGates";
import { getErrorMessage } from "@/lib/errors";
import { fromDateTimeLocal, toDateTimeLocal } from "@/lib/utils";
import { AlertTriangle, CalendarClock, LockKeyhole, Save, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { AdminRegistrationCodes } from "./AdminRegistrationCodes";

const states: Array<{ value: EventState; label: string; description: string }> = [
  { value: "draft", label: "준비 중", description: "공개 랜딩 정보만 보이며 가입·팀·문제·제출은 닫힙니다." },
  { value: "registration", label: "등록 중", description: "계정과 팀 구성을 허용합니다." },
  { value: "live", label: "진행 중", description: "문제 제출과 점수 반영을 허용합니다." },
  { value: "frozen", label: "점수판 동결", description: "제출은 반영하되 공개 점수를 고정합니다." },
  { value: "ended", label: "종료", description: "새 제출을 거부합니다." },
  { value: "archived", label: "보관됨", description: "이벤트를 읽기 전용으로 보존합니다." },
];

export function AdminSettingsPage() {
  const { push } = useToast();
  const [event, setEvent] = useState<AdminEvent | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [persistedState, setPersistedState] = useState<EventState | null>(null);
  const [freezeAtLocked, setFreezeAtLocked] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const current = await api.admin.event();
      setEvent(current);
      setPersistedState(current.state);
      setFreezeAtLocked(scoringIsLocked(current));
    }
    catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const set = <K extends keyof AdminEvent>(key: K, value: AdminEvent[K]) => setEvent((current) => current ? { ...current, [key]: value } : current);
  const save = async (submitEvent: FormEvent) => {
    submitEvent.preventDefault();
    if (!event || persistedState === "archived") return;
    setSaving(true); setError("");
    try {
      const { id: _id, ...payload } = event;
      const updated = await api.admin.updateEvent(payload);
      setEvent(updated);
      setPersistedState(updated.state);
      setFreezeAtLocked(scoringIsLocked(updated));
      push("이벤트 설정을 저장했습니다.", "success");
    } catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setSaving(false); }
  };

  if (loading) return <LoadingState label="이벤트 설정을 불러오는 중" />;
  if (error && !event) return <ErrorState message={error} onRetry={load} />;
  if (!event) return null;
  const readOnly = persistedState === "archived";
  const persistedStateIndex = persistedState ? states.findIndex((item) => item.value === persistedState) : -1;
  const nextState = persistedStateIndex >= 0 ? states[persistedStateIndex + 1] : undefined;

  return (
    <div>
      <PageHeader eyebrow="EVENT CONTROL" title="이벤트 설정" description="경기 상태, 일정과 참가 방식을 제어합니다." actions={<EventStatusPill state={event.state} />} />
      <form className="settings-form" onSubmit={save}>
        {readOnly && <div className="warning-box" role="status"><LockKeyhole /><p>보관된 이벤트는 읽기 전용입니다. 설정과 콘텐츠를 더 이상 변경할 수 없습니다.</p></div>}
        <fieldset className="readonly-fieldset" disabled={readOnly}>
        <Panel title="기본 정보" description="참가자 홈과 점수판에 표시됩니다.">
          <div className="form-grid form-grid--2">
            <label className="field"><span>이벤트 이름</span><input maxLength={120} onChange={(input) => set("name", input.target.value)} required value={event.name} /></label>
            <label className="field"><span>Slug</span><input disabled value={event.slug} /><small>생성 후에는 변경할 수 없습니다.</small></label>
          </div>
          <label className="field"><span>소개 (Markdown)</span><textarea maxLength={100_000} onChange={(input) => set("description_md", input.target.value)} placeholder="이벤트 소개와 규칙을 작성하세요." rows={7} value={event.description_md ?? ""} /></label>
          <div className="form-grid form-grid--2">
            <label className="switch-control"><input checked={event.team_mode !== "individual"} onChange={(input) => set("team_mode", input.target.checked ? "team" : "individual")} type="checkbox" /><span /><div><strong>팀전 모드</strong><small>끄면 참가자를 개인 단위로 채점합니다.</small></div></label>
            <label className="field"><span>가입 방식</span><select aria-label="가입 방식" onChange={(input) => set("registration_access_mode", input.target.value as "open" | "code")} value={event.registration_access_mode ?? "open"}><option value="open">공개 가입</option><option value="code">접근 코드 필요</option></select><small>코드 모드에서는 유효한 이벤트 등록 코드가 있어야 가입할 수 있습니다.</small></label>
          </div>
        </Panel>

        <Panel title="경기 상태" description="상태 변경은 참가와 제출 가능 여부에 즉시 반영됩니다.">
          <div className="state-selector">
            {states.map((item, index) => {
              const selectable = index === persistedStateIndex || index === persistedStateIndex + 1;
              return <label className={`${event.state === item.value ? "is-selected" : ""} ${!selectable ? "is-disabled" : ""}`} key={item.value}><input checked={event.state === item.value} disabled={!selectable} name="event-state" onChange={() => set("state", item.value)} type="radio" value={item.value} /><span><strong>{item.label}</strong><small>{item.description}</small></span></label>;
            })}
          </div>
          {!readOnly && <div className="schedule-note"><CalendarClock /><p>{nextState ? `상태는 한 단계씩 전환합니다. 다음 단계는 “${nextState.label}”입니다.` : "현재 상태가 최종 단계입니다."}</p></div>}
          {event.state === "ended" && <div className="warning-box"><AlertTriangle /><p>이 상태에서는 모든 새 플래그 제출이 거부됩니다. 변경 전 참가자에게 공지하세요.</p></div>}
        </Panel>

        <Panel title="일정" description="모든 시간은 브라우저의 현지 시간으로 입력하고 서버에는 UTC로 저장합니다.">
          <div className="form-grid form-grid--2">
            <label className="field"><span>등록 시작</span><input onChange={(input) => set("registration_at", fromDateTimeLocal(input.target.value))} type="datetime-local" value={toDateTimeLocal(event.registration_at)} /></label>
            <label className="field"><span>경기 시작</span><input onChange={(input) => set("start_at", fromDateTimeLocal(input.target.value))} type="datetime-local" value={toDateTimeLocal(event.start_at)} /></label>
            <label className="field"><span>점수판 동결</span><input disabled={freezeAtLocked} onChange={(input) => set("freeze_at", fromDateTimeLocal(input.target.value))} type="datetime-local" value={toDateTimeLocal(event.freeze_at)} /></label>
            <label className="field"><span>경기 종료</span><input onChange={(input) => set("end_at", fromDateTimeLocal(input.target.value))} type="datetime-local" value={toDateTimeLocal(event.end_at)} /></label>
          </div>
          {freezeAtLocked && !readOnly && <div className="warning-box" role="status"><LockKeyhole /><p>점수판 동결이 시작되어 동결 기준 시각을 변경할 수 없습니다.</p></div>}
          <div className="schedule-note"><CalendarClock /><p>시작·종료 시각과 상태 전환은 별개입니다. 자동 전환을 사용하지 않는 경우 운영자가 상태를 직접 변경해야 합니다.</p></div>
        </Panel>
        </fieldset>

        {error && <div className="form-error" role="alert">{error}</div>}
        <div className="sticky-save"><div><ShieldCheck /><span>{readOnly ? "보관된 설정을 열람하고 있습니다." : "변경 사항은 감사 로그에 기록됩니다."}</span></div><button className="button button--primary" disabled={saving || readOnly} title={readOnly ? "보관된 이벤트는 수정할 수 없습니다." : undefined} type="submit"><Save size={16} /> {saving ? "저장 중…" : readOnly ? "읽기 전용" : "설정 저장"}</button></div>
      </form>
      <div className="settings-registration-codes">
        <AdminRegistrationCodes readOnly={readOnly} />
      </div>
    </div>
  );
}
