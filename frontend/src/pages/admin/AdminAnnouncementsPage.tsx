import { api } from "@/api/endpoints";
import type { Announcement } from "@/api/types";
import { Modal } from "@/components/Modal";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { Markdown } from "@/components/Markdown";
import { PageHeader } from "@/components/Page";
import { useToast } from "@/components/Toast";
import { getErrorMessage } from "@/lib/errors";
import { formatDateTime, fromDateTimeLocal, toDateTimeLocal } from "@/lib/utils";
import { FilePenLine, LockKeyhole, Plus, Radio, Send, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";

export function AdminAnnouncementsPage() {
  const { push } = useToast();
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<Announcement | null>(null);
  const [readOnly, setReadOnly] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [rows, event] = await Promise.all([api.admin.announcements(), api.admin.event()]);
      setAnnouncements(rows);
      setReadOnly(event.state === "archived");
      if (event.state === "archived") setEditorOpen(false);
    }
    catch (requestError) { setError(getErrorMessage(requestError)); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const closeEditor = useCallback(() => setEditorOpen(false), []);
  const openCreate = () => { if (!readOnly) { setEditing(null); setEditorOpen(true); } };

  const remove = async (announcement: Announcement) => {
    if (readOnly) return;
    if (!window.confirm(`“${announcement.title}” 공지를 삭제할까요?`)) return;
    try {
      await api.admin.deleteAnnouncement(announcement.id);
      setAnnouncements((current) => current.filter((item) => item.id !== announcement.id));
      push("공지를 삭제했습니다.", "success");
    } catch (requestError) { push(getErrorMessage(requestError), "error"); }
  };

  const save = async (payload: Partial<Announcement>) => {
    if (readOnly) return;
    try {
      if (editing) await api.admin.updateAnnouncement(editing.id, payload);
      else await api.admin.createAnnouncement(payload);
      setEditorOpen(false); await load();
      push(editing ? "공지를 수정했습니다." : "공지를 등록했습니다.", "success");
    } catch (requestError) { push(getErrorMessage(requestError), "error"); throw requestError; }
  };

  return (
    <div>
      <PageHeader eyebrow="BROADCAST CENTER" title="공지 관리" description="참가자 홈에 표시할 운영 메시지를 작성합니다." actions={<button className="button button--primary" disabled={loading || readOnly} onClick={openCreate} title={readOnly ? "보관된 이벤트는 수정할 수 없습니다." : undefined} type="button"><Plus size={17} /> 새 공지</button>} />
      {readOnly && <div className="warning-box admin-readonly-notice" role="status"><LockKeyhole /><p>보관된 이벤트의 공지는 읽기 전용입니다. 작성·수정·삭제할 수 없습니다.</p></div>}
      {loading ? <LoadingState label="공지를 불러오는 중" /> : error ? <ErrorState message={error} onRetry={load} /> : announcements.length === 0 ? (
        <EmptyState title="아직 공지가 없습니다" description={readOnly ? "보관된 이벤트에는 공지를 추가할 수 없습니다." : "경기 일정이나 규칙 변경을 참가자에게 전달하세요."} action={readOnly ? undefined : <button className="button button--primary button--small" onClick={openCreate} type="button"><Radio size={15} /> 첫 공지 작성</button>} />
      ) : (
        <div className="admin-announcement-list">
          {announcements.map((announcement) => (
            <article className="admin-announcement-card" key={announcement.id}>
              <div className="admin-announcement-card__icon"><Radio /></div>
              <div className="admin-announcement-card__content">
                <div><h2>{announcement.title}</h2><span>{announcement.published_at || announcement.publish_at ? formatDateTime(announcement.published_at ?? announcement.publish_at) : "즉시 공개"}</span></div>
                <Markdown>{announcement.body_md ?? announcement.body}</Markdown>
              </div>
              <div className="row-actions">
                <button aria-label={`${announcement.title} 수정`} className="icon-button" disabled={readOnly} onClick={() => { if (!readOnly) { setEditing(announcement); setEditorOpen(true); } }} title={readOnly ? "보관된 이벤트는 수정할 수 없습니다." : "수정"} type="button"><FilePenLine size={16} /></button>
                <button aria-label={`${announcement.title} 삭제`} className="icon-button icon-button--danger" disabled={readOnly} onClick={() => void remove(announcement)} title={readOnly ? "보관된 이벤트는 삭제할 수 없습니다." : "삭제"} type="button"><Trash2 size={16} /></button>
              </div>
            </article>
          ))}
        </div>
      )}
      <Modal description="Markdown 문법을 사용할 수 있습니다." onClose={closeEditor} open={editorOpen} title={editing ? "공지 수정" : "새 공지 작성"}>
        <AnnouncementForm announcement={editing} onSave={save} />
      </Modal>
    </div>
  );
}

function AnnouncementForm({ announcement, onSave }: { announcement: Announcement | null; onSave: (value: Partial<Announcement>) => Promise<void> }) {
  const [title, setTitle] = useState(announcement?.title ?? "");
  const [body, setBody] = useState(announcement?.body_md ?? announcement?.body ?? "");
  const [publishAt, setPublishAt] = useState(toDateTimeLocal(announcement?.publish_at ?? announcement?.published_at));
  const [saving, setSaving] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setSaving(true);
    try { await onSave({ title: title.trim(), body_md: body, publish_at: fromDateTimeLocal(publishAt) }); }
    catch { /* Parent renders API feedback. */ }
    finally { setSaving(false); }
  };
  return <form className="announcement-editor" onSubmit={submit}>
    <label className="field"><span>제목</span><input maxLength={200} onChange={(event) => setTitle(event.target.value)} required value={title} /></label>
    <label className="field"><span>내용 (Markdown)</span><textarea maxLength={100_000} onChange={(event) => setBody(event.target.value)} required rows={10} value={body} /></label>
    <label className="field"><span>공개 시각 (비우면 즉시)</span><input onChange={(event) => setPublishAt(event.target.value)} type="datetime-local" value={publishAt} /></label>
    <div className="form-actions"><button className="button button--primary" disabled={saving} type="submit"><Send size={16} /> {saving ? "저장 중…" : "공지 저장"}</button></div>
  </form>;
}
