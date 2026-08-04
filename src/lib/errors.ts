function validationMessage(value: Record<string, unknown>) {
  if (typeof value.msg !== "string" || !value.msg.trim()) return null;
  const location = Array.isArray(value.loc)
    ? value.loc
        .filter((part) => part !== "body")
        .map(String)
        .join(" > ")
    : "";
  return location ? `${location}: ${value.msg}` : value.msg;
}

export function readableErrorMessage(
  value: unknown,
  fallback = "Something went wrong. Please try again.",
  depth = 0
): string {
  if (depth > 4) return fallback;

  if (value instanceof Error) {
    return readableErrorMessage(value.message, fallback, depth + 1);
  }

  if (typeof value === "string") {
    const message = value.trim();
    return message && message !== "[object Object]" ? message : fallback;
  }

  if (Array.isArray(value)) {
    const messages = value
      .map((item) => readableErrorMessage(item, "", depth + 1))
      .filter(Boolean);
    return messages.length ? messages.join(" ") : fallback;
  }

  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    const validation = validationMessage(record);
    if (validation) return validation;

    for (const key of ["detail", "message", "error", "errors"]) {
      if (key in record) {
        const message = readableErrorMessage(
          record[key],
          "",
          depth + 1
        );
        if (message) return message;
      }
    }
  }

  return fallback;
}
