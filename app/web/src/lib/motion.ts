// Svelte transition durations that collapse to zero under prefers-reduced-motion.
const query = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;

export const ms = (duration: number): number => (query?.matches ? 0 : duration);
