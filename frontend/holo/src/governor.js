// Quality governor (MIA-HOLO-06). Pure: no DOM, no WebGL, no clock. The renderer feeds it one sample
// per drawn frame and applies the level it returns, so the policy is testable without a GPU.
//
// Policy: step DOWN one level when, over a full window, the average draw cost exceeds the frame
// budget or too many frames arrive late (the GPU cannot keep up even if the CPU submit is cheap).
// Step UP one level only after a long run of comfortably cheap, on-time frames, so it cannot flap.
// A short settling period after every change is ignored (resize and context churn are not load).
import budgets from '../budgets.json';

export const LEVELS = budgets.levels;

export function createGovernor(options = {}) {
  const cfg = { ...budgets.frame, ...options };
  const maxLevel = LEVELS.length - 1;
  let level = Math.min(Math.max(0, options.startLevel || 0), maxLevel);
  let pinned = Number.isInteger(options.pinLevel);
  if (pinned) level = Math.min(Math.max(0, options.pinLevel), maxLevel);
  let costs = [];
  let late = 0;
  let good = 0;
  let settle = 0;
  const log = [];

  function reset() { costs = []; late = 0; good = 0; settle = cfg.settleFrames; }
  settle = 0;

  function change(next, why) {
    if (next === level) return null;
    log.push({ from: level, to: next, why });
    level = next;
    reset();
    return level;
  }

  return {
    level: () => level,
    pinned: () => pinned,
    changes: () => log.slice(),
    pin(n) { pinned = true; return change(Math.min(Math.max(0, n), maxLevel), 'pinned'); },
    auto() { pinned = false; reset(); },
    // costMs: CPU time spent in the draw. intervalMs: time since the previous drawn frame
    // (0 for the first). Returns the new level when it changed, otherwise null.
    record(costMs, intervalMs) {
      if (pinned) return null;
      if (settle > 0) { settle -= 1; return null; }
      if (intervalMs > 1000) return null; // the tab was throttled or hidden; not a load signal
      const target = 1000 / LEVELS[level].maxFps;
      const isLate = intervalMs > 0 && intervalMs > target * 1.5;
      costs.push(costMs);
      if (costs.length > cfg.window) {
        const dropped = costs.shift();
        void dropped;
      }
      late = (late * (cfg.window - 1) + (isLate ? 1 : 0)) / cfg.window; // moving ratio
      const full = costs.length >= cfg.window;
      const avg = costs.reduce((a, b) => a + b, 0) / costs.length;
      if (full && (avg > cfg.frameMs || late > cfg.lateRatio)) {
        return change(Math.min(maxLevel, level + 1), avg > cfg.frameMs ? 'cost' : 'late');
      }
      if (full && avg < cfg.frameMs * 0.5 && late < 0.05) good += 1; else good = 0;
      if (good >= cfg.upAfterFrames && level > 0) return change(level - 1, 'recovered');
      return null;
    },
  };
}
