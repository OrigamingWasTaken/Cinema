<script lang="ts" module>
  // 24×24 stroke icons, drawn for this app.
  const PATHS = {
    // A clapperboard.
    films: "M4.5 10h15v8.5a1.5 1.5 0 0 1-1.5 1.5H6a1.5 1.5 0 0 1-1.5-1.5zM4.5 10l-.5-3a1.2 1.2 0 0 1 1-1.4l13-2.2a1.2 1.2 0 0 1 1.4 1l.4 2.3L4.5 10M8.6 5.2l2.3 3.3M13.6 4.4l2.3 3.3",
    // A stack of screens: one show, many episodes.
    shows: "M5.5 9.5h13a1.5 1.5 0 0 1 1.5 1.5v7.5a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5V11a1.5 1.5 0 0 1 1.5-1.5zM7 6.5h10M9.5 3.5h5",
    search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.5 15.5 20 20",
    library: "M4 5.5h16v10H4zM8.5 19.5h7M12 15.5v4",
    downloads: "M12 4v10.5M7.5 10.5 12 15l4.5-4.5M5 19.5h14",
    back: "M15 5 8 12l7 7",
    close: "M6 6l12 12M18 6 6 18",
    check: "M5 12.5 10 17.5 19 7",
    plus: "M12 5v14M5 12h14",
    retry: "M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5",
    info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5.5M12 7.6v.01",
    get: "M12 4v11M7 10.5l5 5 5-5M6 20h12",
    chevron: "M9 5l7 7-7 7",
    clear: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9 9l6 6M15 9l-6 6",
    alert: "M12 8v5M12 16.5v.01M10.3 3.9 2.6 17.3A2 2 0 0 0 4.3 20.3h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
    // A play triangle (filled where it starts something).
    play: "M7.5 5.6v12.8a1 1 0 0 0 1.5.86l10.6-6.4a1 1 0 0 0 0-1.72L9 4.74a1 1 0 0 0-1.5.86z",
    // Back to the start: a bar and a triangle pointing at it.
    restart: "M6.5 5v14M18 5.8v12.4a.8.8 0 0 1-1.3.63L9 12.63a.8.8 0 0 1 0-1.26l7.7-6.2A.8.8 0 0 1 18 5.8z",
  } as const;
  export type IconName = keyof typeof PATHS;
</script>

<script lang="ts">
  let { name, size = 22, stroke = 2 }: { name: IconName; size?: number; stroke?: number } = $props();
</script>

<svg class="icon" viewBox="0 0 24 24" width={size} height={size} stroke-width={stroke} aria-hidden="true">
  <path d={PATHS[name]} />
</svg>

<style>
  .icon {
    fill: none;
    stroke: currentColor;
    stroke-linecap: round;
    stroke-linejoin: round;
    flex: none;
    display: block;
  }
</style>
