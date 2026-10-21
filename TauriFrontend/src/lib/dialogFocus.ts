import { nextTick, onUnmounted, watch, type Ref } from "vue";


const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");


export function useDialogFocus(
  open: Ref<boolean>,
  dialog: Ref<HTMLElement | null>,
  close: () => void,
): void {
  let restoreTarget: HTMLElement | null = null;

  function focusableElements(): HTMLElement[] {
    return Array.from(dialog.value?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) || [])
      .filter((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true");
  }

  function handleKeydown(event: KeyboardEvent): void {
    if (!open.value) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab") return;

    const elements = focusableElements();
    if (!elements.length) {
      event.preventDefault();
      dialog.value?.focus();
      return;
    }
    const first = elements[0];
    const last = elements[elements.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !dialog.value?.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  const stopWatching = watch(open, (isOpen, wasOpen) => {
    if (isOpen) {
      restoreTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      void nextTick(() => {
        const preferred = dialog.value?.querySelector<HTMLElement>("[autofocus]");
        (preferred || focusableElements()[0] || dialog.value)?.focus();
      });
      return;
    }
    if (wasOpen) {
      const target = restoreTarget;
      restoreTarget = null;
      void nextTick(() => {
        if (target?.isConnected) target.focus();
      });
    }
  }, { flush: "pre", immediate: true });

  document.addEventListener("keydown", handleKeydown);
  onUnmounted(() => {
    stopWatching();
    document.removeEventListener("keydown", handleKeydown);
    if (restoreTarget?.isConnected) restoreTarget.focus();
  });
}
