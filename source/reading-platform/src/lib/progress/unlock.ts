import type { Part, PartStatus } from "@/lib/types";

export function unlockedPartIds(
  parts: Part[],
  statusById: Map<string, PartStatus>,
): Set<string> {
  const ordered = [...parts].sort((a, b) => a.order - b.order);
  const unlocked = new Set<string>();
  for (let i = 0; i < ordered.length; i++) {
    if (i === 0) {
      unlocked.add(ordered[i].id);
      continue;
    }
    const prev = ordered[i - 1];
    if (statusById.get(prev.id) === "completed") {
      unlocked.add(ordered[i].id);
    } else {
      break;
    }
  }
  return unlocked;
}
