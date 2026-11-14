<script lang="ts" module>
  // 24×24 stroke icons, drawn for this app.
  const PATHS = {
    home: "M4 10.5 12 4l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5.5h-5V20H5a1 1 0 0 1-1-1z",
    search: "M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM15.5 15.5 20 20",
    library: "M4 5.5h16v10H4zM8.5 19.5h7M12 15.5v4",
    downloads: "M12 4v10.5M7.5 10.5 12 15l4.5-4.5M5 19.5h14",
    back: "M15 5 8 12l7 7",
    close: "M6 6l12 12M18 6 6 18",
    check: "M5 12.5 10 17.5 19 7",
    retry: "M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5",
    info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 11v5.5M12 7.6v.01",
    get: "M12 4v11M7 10.5l5 5 5-5M6 20h12",
    chevron: "M9 5l7 7-7 7",
    clear: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9 9l6 6M15 9l-6 6",
    alert: "M12 8v5M12 16.5v.01M10.3 3.9 2.6 17.3A2 2 0 0 0 4.3 20.3h15.4a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z",
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
