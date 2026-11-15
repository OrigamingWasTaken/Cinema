// App-wide UI state that more than one component needs.
export const ui = $state({
  /** A bottom sheet is open: the page and the tab bar behind it are inert. */
  sheetOpen: false,
});

/** Move an element to <body>, out of any inert ancestor (used by the sheet). */
export function portal(node: HTMLElement) {
  document.body.appendChild(node);
  return {
    destroy() {
      node.remove();
    },
  };
}
