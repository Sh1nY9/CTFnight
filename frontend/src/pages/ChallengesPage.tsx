import { api } from "@/api/endpoints";
import type { Challenge } from "@/api/types";
import { EmptyState, ErrorState, LoadingState } from "@/components/States";
import { PageHeader } from "@/components/Page";
import { getErrorMessage } from "@/lib/errors";
import { cx, formatNumber } from "@/lib/utils";
import { Check, ChevronRight, Flag, Search, SlidersHorizontal, Target } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

export function ChallengesPage() {
  const [challenges, setChallenges] = useState<Challenge[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [category, setCategory] = useState("전체");
  const [query, setQuery] = useState("");
  const [showSolved, setShowSolved] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setChallenges(await api.participant.challenges());
    } catch (requestError) {
      setError(getErrorMessage(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const categories = useMemo(() => [
    "전체",
    ...Array.from(new Set(challenges.map((challenge) => challenge.category))).sort((a, b) => a.localeCompare(b, "ko")),
  ], [challenges]);

  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return challenges.filter((challenge) => {
      if (category !== "전체" && challenge.category !== category) return false;
      if (!showSolved && challenge.solved) return false;
      if (normalized && !`${challenge.title} ${challenge.category}`.toLocaleLowerCase().includes(normalized)) return false;
      return true;
    });
  }, [challenges, category, query, showSolved]);

  const solved = challenges.filter((challenge) => challenge.solved).length;

  return (
    <div className="page-container">
      <PageHeader
        eyebrow="CHALLENGE BOARD"
        title="문제"
        description="분석하고, 가설을 세우고, 플래그로 증명하세요."
        actions={challenges.length > 0 ? <div className="progress-badge"><Target size={17} /><strong>{solved}</strong> / {challenges.length} 해결</div> : undefined}
      />

      <div className="challenge-toolbar">
        <label className="search-field">
          <span className="sr-only">문제 검색</span>
          <Search size={17} />
          <input onChange={(event) => setQuery(event.target.value)} placeholder="문제 또는 카테고리 검색" type="search" value={query} />
        </label>
        <label className="checkbox-control">
          <input checked={showSolved} onChange={(event) => setShowSolved(event.target.checked)} type="checkbox" />
          해결한 문제 표시
        </label>
      </div>

      <div className="category-tabs" role="group" aria-label="카테고리 필터">
        <SlidersHorizontal size={16} aria-hidden="true" />
        {categories.map((item) => (
          <button className={cx("category-tab", category === item && "is-active")} key={item} onClick={() => setCategory(item)} type="button">
            {item}
            <span>{item === "전체" ? challenges.length : challenges.filter((challenge) => challenge.category === item).length}</span>
          </button>
        ))}
      </div>

      {loading ? <LoadingState label="문제를 불러오는 중" /> : error ? (
        <ErrorState message={error} onRetry={load} />
      ) : challenges.length === 0 ? (
        <EmptyState title="공개된 문제가 없습니다" description="운영자가 문제를 공개하면 이곳에 나타납니다." />
      ) : filtered.length === 0 ? (
        <EmptyState title="조건에 맞는 문제가 없습니다" description="검색어 또는 필터를 바꿔보세요." />
      ) : (
        <div className="challenge-groups">
          {categories.filter((item) => item !== "전체" && (category === "전체" || category === item)).map((group) => {
            const items = filtered.filter((challenge) => challenge.category === group);
            if (!items.length) return null;
            return (
              <section key={group} className="challenge-group">
                <div className="challenge-group__heading">
                  <h2>{group}</h2><span>{items.length} challenges</span>
                </div>
                <div className="challenge-grid">
                  {items.map((challenge) => <ChallengeCard challenge={challenge} key={challenge.id} />)}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function ChallengeCard({ challenge }: { challenge: Challenge }) {
  const exhausted = challenge.max_attempts > 0 && challenge.attempts >= challenge.max_attempts;
  return (
    <Link className={cx("challenge-card", challenge.solved && "challenge-card--solved", exhausted && !challenge.solved && "challenge-card--exhausted")} to={`/challenges/${challenge.id}`}>
      <div className="challenge-card__top">
        <span className="challenge-category">{challenge.category}</span>
        {challenge.solved ? <span className="solved-mark"><Check size={14} /> 해결</span> : <Flag size={17} aria-hidden="true" />}
      </div>
      <h3>{challenge.title}</h3>
      <div className="challenge-card__stats">
        <strong>{formatNumber(challenge.current_points)} <small>pts</small></strong>
        <span>{formatNumber(challenge.solve_count)} solves</span>
      </div>
      <div className="challenge-card__footer">
        <span>{challenge.max_attempts > 0 ? `${challenge.attempts}/${challenge.max_attempts}회 시도` : `${challenge.attempts}회 시도`}</span>
        <ChevronRight size={17} />
      </div>
    </Link>
  );
}
