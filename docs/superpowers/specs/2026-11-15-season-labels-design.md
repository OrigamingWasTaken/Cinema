# Show page: season names, years and "new episode" line

Short design (small change). Agreed in conversation on 2026-11-15.

## Intent
On JoJo's Bizarre Adventure the page listed "Season 1 … Season 6", and the user believed the airing season (Steel Ball Run, weekly on Fridays) was missing. It was Season 6; the page just gave no way to tell. Each season row should say what it is and whether it is airing.

## Goals
- Under each season title, show the TMDB season name and first-air year when they help, e.g. "Steel Ball Run · 2026" or "Stone Ocean · 2022".
  - Omit the name when it is just "Season N"/"Saison N" and show only the year.
- On the season that is airing, show "New episode Fri 20 Nov" (fr: "Nouvel épisode ven. 20 nov."), from TMDB's `next_episode_to_air`, in the device's locale.
- This works whether or not the show is already in Sonarr.

## Non-goals
- Changing season numbering. It stays Sonarr's (TVDB), as decided for TV series.
- Episode lists for shows not yet in Sonarr.

## Constraints
- Use TMDB labels only when they can't mislead: when TMDB and Sonarr have the same number of non-special seasons. Otherwise show nothing extra (or the year from Sonarr's episodes, when known). The TVDB/TMDB numbering mismatch must never put the wrong name on a season.
- The data comes from the TMDB `/tv/{id}` response we already fetch (`seasons[]`, `next_episode_to_air`), so there are no new TMDB calls.
- en/fr copy. Same error handling as today.

## Approaches
The only sensible way is to reuse the TMDB show response, guarded by the season-count check. Adding every browsed show to Sonarr just to read its episodes would fill Sonarr with shows nobody asked for.

## Design
- `tmdb.ShowInfo` gains `seasons: list[(number, name, air_year)]` and `next_episode: (season, episode, air_date) | None`.
- `ShowsService.page` adds `label` (name or None) and `year` to each season dict, plus `next_air_date` on the matching season, all under the guard.
- `SeasonRow.svelte` shows the label line and the "New episode" line.

## Left to the builder
The exact layout of the line, and the date format details.
