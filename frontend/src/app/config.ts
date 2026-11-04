export const DATA_FREEZE_DATE = "2026-09-30";

export function getEffectiveDate(selectedDate: string): string {
  return selectedDate > DATA_FREEZE_DATE ? DATA_FREEZE_DATE : selectedDate;
}
