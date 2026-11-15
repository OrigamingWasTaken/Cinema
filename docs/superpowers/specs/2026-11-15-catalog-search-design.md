# Catalog search: people, studios, collections, genres

Short design (small change). Approved in conversation on 2026-11-15.

## Intent
Search today matches film titles only (TMDB `/search/movie`). The family wants Netflix / Apple TV style search: type an actor ("Tom Hanks"), a director, a studio ("Pixar", "A24") or a franchise ("Harry Potter") and find the films. They also want to browse by genre before typing.

## Goals
- One search box. Results are grouped into **Films** (poster grid), **People** (actors and directors as round photos), **Studios**, and **Collections**.
- Tapping a person opens their films (acted in or directed), most popular first.
- Tapping a studio opens its films, most popular first, with "load more".
- Tapping a collection opens its films in release order.
- Before typing: **genre chips** (from TMDB's movie genre list), each opening a page of popular films in that genre with "load more". "Popular right now" stays below the chips.
- Every film card keeps the "on the server" check badge and opens the existing film page.

## Non-goals
- Our own search index or typo tolerance beyond TMDB's.
- Voice search.
- Personal recommendations.
- TV shows. Those come with the series project; search will gain a **Shows** group then.

## Constraints
- All data comes from TMDB using the existing `TmdbClient` (bearer token, language param, in-memory TTL cache). The 1 h TTL applies to detail pages and genre lists; the 10 min TTL applies to searches.
- Error handling stays as it is: upstream failures give 502 `{"error":"upstream","service":"tmdb"}`, and an unknown id gives 404.
- The family never sees anything new that is technical.
- The Svelte app stays dependency-free beyond svelte. Copy is in en and fr.

## Approaches
The only sensible way is to call TMDB's dedicated endpoints in parallel and group the results ourselves. `/search/multi` mixes movies, TV and people but has no studios or collections, and we'd have to re-split it anyway. A local index is out of scope.

## Design
**TMDB client** (`app/cinema/tmdb.py`), new cached methods:
- `search_all(q)` runs these four in parallel and returns their results:
  - `/search/movie`
  - `/search/person`: keep `known_for_department` in {Acting, Directing}. Sort by popularity, top 12, and drop entries with no `profile_path` unless the name matches exactly.
  - `/search/company`: top 6, preferring entries with a `logo_path`.
  - `/search/collection`: top 6.
- `person(id)`: `/person/{id}` plus `/person/{id}/movie_credits`. Merge cast and Director crew, dedupe by id, and sort by popularity descending.
- `company_movies(id, page)`: `/company/{id}` plus `/discover/movie?with_companies={id}&sort_by=popularity.desc&page=N`.
- `collection(id)`: `/collection/{id}`, parts sorted by release date.
- `genres()`: `/genre/movie/list`.
- `genre_movies(id, page)`: `/discover/movie?with_genres={id}&sort_by=popularity.desc&vote_count.gte=50&page=N`.

**API** (`app/cinema/api.py`). All are GET. Film lists are `Card[]`, with `in_library` as today.
- `/api/search?q=` returns `{films, people:[{id,name,profile_path,known_for}], studios:[{id,name,logo_path}], collections:[{id,name,poster_path}]}`. **This changes the response shape:** the old `results` key becomes `films`, and the web app is updated in the same change.
- `/api/people/{id}` returns `{person:{id,name,profile_path,known_for_department}, films:[Card]}`.
- `/api/studios/{id}?page=` returns `{studio:{id,name,logo_path}, films:[Card], page, total_pages}`.
- `/api/collections/{id}` returns `{collection:{id,name,poster_path,backdrop_path}, films:[Card]}`.
- `/api/genres` returns `{genres:[{id,name}]}`.
- `/api/genres/{id}?page=` returns `{genre:{id,name}, films:[Card], page, total_pages}`.

**Svelte app:**
- The Search tab shows grouped sections. Films is the 3-column grid. People is a horizontal row of round photos with names. Studios and Collections are horizontal rows of logo or poster tiles. Empty groups are hidden, and the empty state shows when everything is empty.
- Idle state: the genre chips wrap across lines, then "Popular right now".
- New pushed routes: `#/person/{id}`, `#/studio/{id}`, `#/collection/{id}`, `#/genre/{id}`. Each shows a header (photo, logo or name) and a film grid. Studio and genre pages have "Load more", or infinite scroll with a sentinel.
- People with no photo get an initials circle. Studios with no logo get a name tile.
- New en/fr strings: People/Personnes, Studios/Studios, Collections/Collections, Genres/Genres, Load more/Plus de résultats, "Films with {name}"/"Films avec {name}", plus empty states.

## Left to the builder
Exact thresholds (popularity cut-offs, top-N), visual details of the rows and headers, and whether to use infinite scroll or a button.
