<script lang="ts">
  let { size = 18 }: { size?: number } = $props();
</script>

<span class="spinner" style:--size="{size}px" aria-hidden="true"></span>

<style>
  .spinner {
    width: var(--size);
    height: var(--size);
    flex: none;
    border-radius: 50%;
    border: 2px solid currentColor;
    border-right-color: transparent;
    animation: spin 0.8s linear infinite;
    opacity: 0.85;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) {
    .spinner { animation-duration: 2.4s; }
  }
</style>
