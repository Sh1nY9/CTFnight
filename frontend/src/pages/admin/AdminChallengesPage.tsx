import { api } from "@/api/endpoints";
import type { AdminChallenge, ChallengeWrite, FlagType } from "@/api/types";
import { Modal } from "@/components/Modal";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { PageHeader, Panel } from "@/components/Page";
import { useToast } from "@/components/Toast";
import { scoringIsLocked } from "@/lib/eventGates";
import { getErrorMessage } from "@/lib/errors";
import { DEFAULT_MAX_FLAG_LENGTH, maxFlagLengthFromMeta } from "@/lib/platformLimits";
import { formatNumber, fromDateTimeLocal, toDateTimeLocal } from "@/lib/utils";
import { Check, Eye, EyeOff, FilePenLine, Flag, LockKeyhole, Plus, Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";

const emptyChallenge: ChallengeWrite = {
  slug: "",
  title: "",
  category: "Misc",
  description_md: "",
  connection_info: null,
  scoring_type: "fixed",
  initial_points: 100,
  minimum_points: 100,
  decay: 20,
  max_attempts: 0,
  visible_at: null,
  prerequisite_ids: [],
  flag_type: "exact",
  flag: "",
  visible: false,
};

function challengeToForm(challenge: AdminChallenge): ChallengeWrite {
  const initial = challenge.initial_points ?? challenge.current_points;
  return {
    slug: challenge.slug,
    title: challenge.title,
    category: challenge.category,
    description_md: challenge.description_md,
    connection_info: challenge.connection_info,
    scoring_type: challenge.scoring_type,
    initial_points: initial,
    minimum_points: challenge.minimum_points ?? (challenge.scoring_type === "dynamic" ? 50 : initial),
    decay: challenge.decay ?? 20,
    max_attempts: challenge.max_attempts,
    visible_at: challenge.visible_at,
    prerequisite_ids: challenge.prerequisite_ids,
    flag_type: challenge.flag_type,
    flag: "",
    visible: challenge.visible,
  };
}

export function AdminChallengesPage() {
  const { push } = useToast();
  const [challenges, setChallenges] = useState<AdminChallenge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<AdminChallenge | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [readOnly, setReadOnly] = useState(false);
  const [scoringLocked, setScoringLocked] = useState(false);
  const [maxFlagLength, setMaxFlagLength] = useState(DEFAULT_MAX_FLAG_LENGTH);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [rows, event, meta] = await Promise.all([
        api.admin.challenges(),
        api.admin.event(),
        api.meta().catch(() => null),
      ]);
      setChallenges(rows);
      setReadOnly(event.state === "archived");
      setScoringLocked(scoringIsLocked(event));
      setMaxFlagLength(maxFlagLengthFromMeta(meta));
      if (event.state === "archived") setEditorOpen(false);
    }
    catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void load(); }, [load]);
  const closeEditor = useCallback(() => setEditorOpen(false), []);

  const filtered = useMemo(() => {
    const value = query.toLocaleLowerCase().trim();
    return challenges.filter((challenge) => !value || `${challenge.title} ${challenge.slug} ${challenge.category}`.toLocaleLowerCase().includes(value));
  }, [challenges, query]);

  const openCreate = () => { if (!readOnly) { setEditing(null); setEditorOpen(true); } };
  const openEdit = (challenge: AdminChallenge) => { if (!readOnly) { setEditing(challenge); setEditorOpen(true); } };

  const save = async (form: ChallengeWrite) => {
    if (readOnly) return;
    setSavingId(editing?.id ?? "new");
    try {
      if (editing) {
        const payload: Partial<ChallengeWrite> = { ...form };
        if (!payload.flag) delete payload.flag;
        await api.admin.updateChallenge(editing.id, payload);
        push("문제를 수정했습니다.", "success");
      } else {
        await api.admin.createChallenge(form);
        push("문제를 만들었습니다.", "success");
      }
      setEditorOpen(false);
      await load();
    } catch (requestError) {
      push(getErrorMessage(requestError), "error");
      throw requestError;
    } finally { setSavingId(null); }
  };

  const toggleVisibility = async (challenge: AdminChallenge) => {
    if (readOnly) return;
    setSavingId(challenge.id);
    try {
      await api.admin.setChallengeVisibility(challenge.id, !challenge.visible);
      setChallenges((current) => current.map((item) => item.id === challenge.id ? { ...item, visible: !item.visible } : item));
      push(challenge.visible ? "문제를 비공개로 전환했습니다." : "문제를 공개했습니다.", "success");
    } catch (requestError) { push(getErrorMessage(requestError), "error"); }
    finally { setSavingId(null); }
  };

  return (
    <div>
      <PageHeader eyebrow="CHALLENGE OPERATIONS" title="문제 관리" description="문제 내용, 플래그 규칙과 공개 상태를 관리합니다." actions={<button className="button button--primary" disabled={loading || readOnly} onClick={openCreate} title={readOnly ? "보관된 이벤트는 수정할 수 없습니다." : undefined} type="button"><Plus size={17} /> 새 문제</button>} />
      {readOnly && <div className="warning-box admin-readonly-notice" role="status"><LockKeyhole /><p>보관된 이벤트의 문제는 읽기 전용입니다. 내용과 공개 상태를 변경할 수 없습니다.</p></div>}
      <div className="admin-toolbar">
        <label className="search-field"><span className="sr-only">문제 검색</span><Search size={17} /><input onChange={(event) => setQuery(event.target.value)} placeholder="제목, slug, 카테고리 검색" type="search" value={query} /></label>
        <span>전체 {challenges.length} · 공개 {challenges.filter((challenge) => challenge.visible).length}</span>
      </div>

      {loading ? <LoadingState label="문제를 불러오는 중" /> : error ? <ErrorState message={error} onRetry={load} /> : challenges.length === 0 ? (
        <EmptyState title="아직 문제가 없습니다" description={readOnly ? "보관된 이벤트에는 문제를 추가할 수 없습니다." : "첫 번째 문제를 만들어 이벤트를 준비하세요."} action={readOnly ? undefined : <button className="button button--primary button--small" onClick={openCreate} type="button"><Plus size={15} /> 문제 만들기</button>} />
      ) : filtered.length === 0 ? <EmptyState title="검색 결과 없음" description="다른 검색어를 입력해보세요." /> : (
        <Panel className="flush-panel">
          <div className="table-scroll">
            <table className="data-table admin-challenges-table">
              <caption className="sr-only">관리자 문제 목록</caption>
              <thead><tr><th scope="col">문제</th><th scope="col">카테고리</th><th scope="col">점수</th><th scope="col">풀이</th><th scope="col">플래그</th><th scope="col">상태</th><th scope="col"><span className="sr-only">작업</span></th></tr></thead>
              <tbody>{filtered.map((challenge) => (
                <tr key={challenge.id}>
                  <th scope="row"><strong>{challenge.title}</strong><code>{challenge.slug}</code></th>
                  <td><span className="challenge-category">{challenge.category}</span></td>
                  <td>{formatNumber(challenge.current_points)}</td>
                  <td>{formatNumber(challenge.solve_count)}</td>
                  <td>{challenge.has_flag ? <span className="configured"><Check size={13} /> {challenge.flag_type}</span> : <span className="not-configured"><Flag size={13} /> 미설정</span>}</td>
                  <td><span className={`visibility-badge ${challenge.visible ? "is-visible" : ""}`}>{challenge.visible ? "공개" : "비공개"}</span></td>
                  <td><div className="row-actions">
                    <button aria-label={`${challenge.title} ${challenge.visible ? "비공개" : "공개"} 전환`} className="icon-button" disabled={readOnly || savingId === challenge.id} onClick={() => void toggleVisibility(challenge)} title={readOnly ? "보관된 이벤트는 수정할 수 없습니다." : challenge.visible ? "비공개 전환" : "공개 전환"} type="button">{challenge.visible ? <EyeOff size={16} /> : <Eye size={16} />}</button>
                    <button aria-label={`${challenge.title} 수정`} className="icon-button" disabled={readOnly} onClick={() => openEdit(challenge)} title={readOnly ? "보관된 이벤트는 수정할 수 없습니다." : "수정"} type="button"><FilePenLine size={16} /></button>
                  </div></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Panel>
      )}

      <Modal description="플래그 원문은 저장 후 다시 표시되지 않습니다." onClose={closeEditor} open={editorOpen} title={editing ? "문제 수정" : "새 문제 만들기"} wide>
        <ChallengeForm challenge={editing ? challengeToForm(editing) : emptyChallenge} editing={Boolean(editing)} hasFlag={editing?.has_flag ?? false} maxFlagLength={maxFlagLength} onSave={save} saving={savingId !== null} scoringLocked={scoringLocked} />
      </Modal>
    </div>
  );
}

function ChallengeForm({ challenge, editing, hasFlag, maxFlagLength, saving, scoringLocked, onSave }: { challenge: ChallengeWrite; editing: boolean; hasFlag: boolean; maxFlagLength: number; saving: boolean; scoringLocked: boolean; onSave: (value: ChallengeWrite) => Promise<void> }) {
  const [form, setForm] = useState<ChallengeWrite>({ ...challenge });
  const [prerequisites, setPrerequisites] = useState(challenge.prerequisite_ids.join(", "));
  const [formError, setFormError] = useState("");
  const set = <K extends keyof ChallengeWrite>(key: K, value: ChallengeWrite[K]) => setForm((current) => ({ ...current, [key]: value }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setFormError("");
    if (!editing && !form.flag?.trim()) { setFormError("새 문제에는 플래그가 필요합니다."); return; }
    if (form.scoring_type === "dynamic" && form.minimum_points > form.initial_points) { setFormError("최소 점수는 시작 점수보다 클 수 없습니다."); return; }
    try {
      await onSave({
        ...form,
        minimum_points: form.scoring_type === "fixed" ? form.initial_points : form.minimum_points,
        decay: form.scoring_type === "fixed" ? Math.max(form.decay, 20) : form.decay,
        flag: form.flag?.trim(),
        prerequisite_ids: prerequisites.split(",").map((item) => item.trim()).filter(Boolean),
      });
    } catch { /* Parent displays the API message in a toast. */ }
  };

  return (
    <form className="challenge-editor" onSubmit={submit}>
      <fieldset><legend>기본 정보</legend><div className="form-grid form-grid--2">
        <label className="field"><span>제목</span><input maxLength={120} onChange={(event) => set("title", event.target.value)} required value={form.title} /></label>
        <label className="field"><span>Slug</span><input disabled={editing} maxLength={63} onChange={(event) => set("slug", event.target.value.toLowerCase())} pattern="[a-z0-9][a-z0-9-]{1,62}" placeholder="web-welcome" required value={form.slug} /><small>2–63자, 영문 소문자·숫자로 시작하고 하이픈을 사용할 수 있습니다.</small></label>
        <label className="field"><span>카테고리</span><input list="challenge-categories" maxLength={80} onChange={(event) => set("category", event.target.value)} required value={form.category} /><datalist id="challenge-categories"><option>Web</option><option>Pwn</option><option>Reversing</option><option>Crypto</option><option>Forensics</option><option>Misc</option></datalist></label>
        <label className="field"><span>공개 시각 (선택)</span><input onChange={(event) => set("visible_at", fromDateTimeLocal(event.target.value))} type="datetime-local" value={toDateTimeLocal(form.visible_at)} /></label>
      </div>
      <label className="field"><span>설명 (Markdown)</span><textarea maxLength={100_000} onChange={(event) => set("description_md", event.target.value)} placeholder="## Mission…" required rows={9} value={form.description_md} /></label>
      <label className="field"><span>접속 정보 (선택)</span><input maxLength={2000} onChange={(event) => set("connection_info", event.target.value || null)} placeholder="nc challenge.example.com 31337" value={form.connection_info ?? ""} /></label></fieldset>

      <fieldset><legend>채점</legend>{scoringLocked && <div className="warning-box" role="status"><LockKeyhole /><p>점수판 동결이 시작되어 점수 방식과 배점을 변경할 수 없습니다.</p></div>}<div className="form-grid form-grid--3">
        <label className="field"><span>점수 방식</span><select disabled={scoringLocked} onChange={(event) => set("scoring_type", event.target.value as "fixed" | "dynamic")} value={form.scoring_type}><option value="fixed">고정 점수</option><option value="dynamic">solve 수 감쇠</option></select></label>
        <label className="field"><span>{form.scoring_type === "dynamic" ? "시작 점수" : "점수"}</span><input disabled={scoringLocked} max={1_000_000} min={1} onChange={(event) => set("initial_points", Number(event.target.value))} required type="number" value={form.initial_points} /></label>
        <label className="field"><span>최대 시도 (0=무제한)</span><input max={1_000_000} min={0} onChange={(event) => set("max_attempts", Number(event.target.value))} required type="number" value={form.max_attempts} /></label>
        {form.scoring_type === "dynamic" && <><label className="field"><span>최소 점수</span><input disabled={scoringLocked} max={1_000_000} min={1} onChange={(event) => set("minimum_points", Number(event.target.value))} required type="number" value={form.minimum_points} /></label><label className="field"><span>감쇠 기준 solve 수</span><input disabled={scoringLocked} max={1_000_000} min={1} onChange={(event) => set("decay", Number(event.target.value))} required type="number" value={form.decay} /></label></>}
      </div></fieldset>

      <fieldset><legend>플래그와 공개</legend><div className="form-grid form-grid--2">
        <label className="field"><span>플래그 형식</span><select onChange={(event) => set("flag_type", event.target.value as FlagType)} value={form.flag_type}><option value="exact">정확히 일치</option><option value="regex">정규식</option></select></label>
        <label className="field"><span>플래그 {editing && hasFlag ? "(변경할 때만 입력)" : ""}</span><input autoComplete="off" maxLength={maxFlagLength} onChange={(event) => set("flag", event.target.value)} placeholder={editing && hasFlag ? "기존 플래그 유지" : "FLAG{...}"} required={!editing || !hasFlag} spellCheck={false} type="password" value={form.flag ?? ""} /><small><code>{"FLAG{...}"}</code>는 예시일 뿐이며 실제 형식은 문제별로 자유롭게 정할 수 있습니다. 서버 허용 한도는 최대 {formatNumber(maxFlagLength)}자이며 원문은 저장 후 다시 조회할 수 없습니다.</small></label>
      </div>
      <label className="field"><span>선행 문제 ID (선택)</span><input onChange={(event) => setPrerequisites(event.target.value)} placeholder="UUID, UUID" value={prerequisites} /><small>여러 ID는 쉼표로 구분합니다.</small></label>
      <label className="switch-control"><input checked={form.visible} onChange={(event) => set("visible", event.target.checked)} type="checkbox" /><span /><div><strong>저장 즉시 공개</strong><small>선행 조건과 공개 시각은 별도로 적용됩니다.</small></div></label></fieldset>
      {formError && <div className="form-error" role="alert">{formError}</div>}
      <div className="form-actions"><button className="button button--primary" disabled={saving} type="submit">{saving ? "저장 중…" : editing ? "변경사항 저장" : "문제 만들기"}</button></div>
    </form>
  );
}
