const textInputTypes = new Set([
  "email",
  "number",
  "password",
  "search",
  "tel",
  "text",
  "url"
]);

export function isTextEditingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (
    target.isContentEditable ||
    target.contentEditable === "true" ||
    target.closest("[contenteditable='true']")
  ) {
    return true;
  }
  if (target instanceof HTMLTextAreaElement) return true;
  return (
    target instanceof HTMLInputElement &&
    textInputTypes.has(target.type.toLowerCase())
  );
}
