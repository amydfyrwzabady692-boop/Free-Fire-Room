/** Shared display helpers for event rows. */

export type SeatsLike = {
  capacity?: number | null;
  confirmed_count?: number | null;
};

/**
 * Capacity 0 means unlimited — customs made in the bot never cap the room, so
 * "12/0 نفر" would be nonsense.
 */
export function seats(e: SeatsLike): string {
  const taken = Math.max(0, Number(e.confirmed_count ?? 0));
  const capacity = Number(e.capacity ?? 0);
  if (!capacity || capacity <= 0) {
    return `${taken} نفر · بدون محدودیت ظرفیت`;
  }
  return `${taken}/${capacity} نفر`;
}
