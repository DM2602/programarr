/**
 * Copies text. The Clipboard API exists only on HTTPS or localhost; over plain HTTP (an address in
 * the home network) the text is copied through a selected input instead.
 */
export async function copyText(text: string, input?: HTMLInputElement | null): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through to the selection
    }
  }
  const field = input ?? Object.assign(document.createElement("textarea"), { value: text });
  const temporary = !input;
  if (temporary) {
    field.setAttribute("readonly", "");
    Object.assign(field.style, { position: "fixed", opacity: "0", top: "0", left: "0" });
    document.body.appendChild(field);
  }
  field.focus();
  field.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  if (temporary) field.remove();
  return copied;
}
