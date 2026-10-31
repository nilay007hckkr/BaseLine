import { BadgeCheck, Lock } from "lucide-react";
import type { Feature } from "../api/client";
import { useInstance } from "../instance";
import { ACHIEVEMENTS, XP_PER_LEVEL, useApp, type AchievementId } from "../state";

/** Badges that belong to a page this instance may hide. */
const NEEDS: Partial<Record<AchievementId, Feature>> = {
  incident: "incidents",
  test: "tests",
  risk: "risk",
  map: "map",
  impact: "impact",
  proof: "proof",
};

/** Your progress through Hindsight: level, XP and the badges for trying each feature. */
export function Profile() {
  const { unlocked, xp, level, setOnboarded } = useApp();
  const { features } = useInstance();
  const ids = (Object.keys(ACHIEVEMENTS) as AchievementId[]).filter((id) => {
    const need = NEEDS[id];
    return need === undefined || features.includes(need);
  });
  return (
    <>
      <div className="label">Visitor</div>
      <div className="profile-id">Explorer</div>
      <div className="row">
        <span className="badge">{level.name}</span>
        <span className="muted small">Level {level.number}</span>
      </div>
      <div className="cm-label">Progress</div>
      <div className="cm-card cm-rows">
        <div className="cm-row">
          <span>Experience</span>
          <span className="cm-row-value c-people">{xp} XP</span>
        </div>
        <div className="cm-row">
          <span>Badges</span>
          <span className="cm-row-value c-people">
            {ids.filter((id) => unlocked.includes(id)).length}/{ids.length}
          </span>
        </div>
        <div className="cm-row">
          <span>Next level</span>
          <span className="cm-row-value">{XP_PER_LEVEL - (xp % XP_PER_LEVEL)} XP</span>
        </div>
      </div>
      <div className="label" style={{ marginBottom: 8 }}>
        Badges · try each feature once
      </div>
      <div className="badges">
        {ids.map((id) => {
          const a = ACHIEVEMENTS[id];
          const done = unlocked.includes(id);
          return (
            <div key={id} className={done ? "badge-card done" : "badge-card"}>
              {done ? (
                <BadgeCheck size={20} aria-hidden="true" />
              ) : (
                <Lock size={20} aria-hidden="true" />
              )}
              <div>
                <div className="badge-title">{a.title}</div>
                <div className="small muted">
                  {a.text} · {a.xp} XP
                </div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="row" style={{ marginTop: 28 }}>
        <button
          onClick={() => {
            setOnboarded(false);
          }}
        >
          Replay the tour
        </button>
      </div>
    </>
  );
}
