export const DATA_FREEZE_DATE = "2026-09-30";
export const STAGING_DATA_FREEZE_DATE = "2026-11-23";

export function getEffectiveDate(selectedDate: string, freezeDate: string = DATA_FREEZE_DATE): string {
  return selectedDate > freezeDate ? freezeDate : selectedDate;
}
