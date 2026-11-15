// hls.js ships no types for its light build: the same API without subtitles,
// alternate audio tracks or DRM, none of which the player uses.
declare module "hls.js/light" {
  export * from "hls.js";
  export { default } from "hls.js";
}
